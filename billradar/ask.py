"""Questions about the case. Nemotron 3 Super answers from the family's papers and the quoted policies only,
and every answer must carry a quote that code finds on that sheet or in that verified policy text.

Order:
  1. A question about health (what to take, symptoms, treatment) is declined by code before any model call.
  2. The model gets the case without names (tokens only), the findings and the verified quotes.
  3. Every citation is checked: a sheet quote must be printed on that sheet as read, a policy quote must be
     part of a fragment that passed policy.verified(). Citations that fail are dropped; an answer with no
     citation left is not shown.
  4. An answer that sounds like medical advice is not shown either.
Until credits arrive the model is replayed; with no recording the family sees "no answer yet", not a guess.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from billradar import laws, policy
from billradar.case import Case
from billradar.checks import Report
from billradar.model_api import ModelError
from billradar.policies.source import compact

MODEL = "reader"
HEALTH = re.compile(
    r"\b(should i (take|stop|start|get)|medication|dosage|dose|symptom|side effects?|diagnos\w*|treat(ment|ed)?|"
    r"is (it|this) (safe|normal|dangerous|serious)|cure|vaccin\w*|pain|fever|infection|surgery i need)\b", re.I)
BILLING = re.compile(r"\b(bill\w*|charge\w*|cost\w*|pay\w*|price\w*|insur\w*|owe\w*|code\w*|line|sheet|refund\w*|"
                     r"discount\w*|appeal\w*|deadline|apply|assistance|eob|statement|balance|\$)", re.I)
ADVICE = re.compile(r"diagnos|you should (take|stop|start)|treatment plan|medical advice|dose|prescri", re.I)
DECLINE = ("I can only help with your bills and insurance papers. For questions about your health, "
           "please ask your doctor or nurse.")

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answer", "citations", "refused"],
    "properties": {
        "answer": {"type": "string"},
        "refused": {"type": "boolean"},
        "citations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["source", "ref", "quote"],
            "properties": {"source": {"type": "string", "enum": ["sheet", "policy"]},
                           "ref": {"type": "string"}, "quote": {"type": "string"}}}},
    },
}
INSTRUCTIONS = """You answer a family's question about their hospital bills and insurance statements.
Use only what is given: the papers as read (sheet ids like S3), the findings, and the policy and law quotes.
Every answer cites at least one source: {"source": "sheet", "ref": "<sheet id>", "quote": "<text or number exactly as on that sheet>"} or {"source": "policy", "ref": "<ref as given>", "quote": "<words copied from that quote>"}.
If the papers do not answer the question, say so and set refused to false with no claim you cannot cite.
If the question is about health, symptoms, medicines or treatment, set refused to true: you help with bills only.
Plain English, short. Never promise an outcome."""


@dataclass
class Citation:
    source: str  # "sheet" | "policy"
    ref: str
    quote: str
    url: str = ""


@dataclass
class Answer:
    text: str
    citations: list[Citation] = field(default_factory=list)
    refused: bool = False
    reason: str = ""  # "health" | "no_citation" | "sounds_medical" | "no_model" | ""
    dropped: list[Citation] = field(default_factory=list)  # citations that did not check out
    replayed: bool = True


def is_health_question(question: str) -> bool:
    return bool(HEALTH.search(question)) and not BILLING.search(question)


def _quotes(case: Case, report: Report) -> list[dict]:
    refs = list(dict.fromkeys([r for f in report.findings + report.worth_asking for r in f.policy_refs + f.laws]
                              + [r for c in laws.for_case(case).cards if c.applies == "yes" for r in c.refs]))
    out = []
    for r in refs:
        if policy.verified(r):
            q = laws.quote(r)
            out.append({"ref": r, "title": q.title, "quote": q.text})
    return out


def request_body(case: Case, report: Report, question: str) -> dict:
    from billradar.verifier import redacted
    c = redacted(case)
    payload = {
        "papers": json.loads(c.model_dump_json(include={"documents", "today", "letterhead"})),
        "findings": [{"kind": f.kind.value, "title": f.title, "detail": f.detail,
                      "amount": str(f.amount) if f.amount is not None else None,
                      "deadline": f.deadline.isoformat() if f.deadline else None,
                      "sheets": [e.doc_id for e in f.evidence]} for f in report.findings],
        "worth_asking": [{"kind": f.kind.value, "title": f.title, "detail": f.detail} for f in report.worth_asking],
        "quotes": _quotes(case, report),
        "question": question,
    }
    return {
        "model": MODEL, "store": False, "stream": True, "max_output_tokens": 3000,
        "text": {"format": {"type": "json_schema", "name": "billradar_answer", "schema": SCHEMA, "strict": True}},
        "input": [
            {"role": "developer", "content": [{"type": "input_text", "text": INSTRUCTIONS}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(payload, default=str)}]},
        ],
    }


def _sheet_text(case: Case, doc_id: str) -> str | None:
    try:
        doc = case.doc(doc_id.strip())
    except StopIteration:
        return None
    raw = doc.model_dump_json()
    money = " ".join(f"{x:,.2f}" for x in re.findall(r'"(-?\d+\.\d\d)"', raw) for x in [float(x)])
    return compact(raw + " " + money)


def check_citation(case: Case, c: Citation) -> bool:
    quote = compact(c.quote)
    if len(quote) < 2:
        return False
    if c.source == "sheet":
        text = _sheet_text(case, c.ref)
        return text is not None and quote.replace("$", "") in text
    if c.source == "policy" and "#" in c.ref:
        try:
            return policy.verified(c.ref) and quote in compact(laws.quote(c.ref).text)
        except (KeyError, FileNotFoundError, StopIteration):
            return False
    return False


def answer(client, case: Case, report: Report, question: str) -> Answer:
    if is_health_question(question):
        return Answer(DECLINE, refused=True, reason="health")
    try:
        res = client.respond(request_body(case, report, question), label="ask")
        raw = json.loads(res.text)
    except ModelError:
        return Answer("No answer yet: the model is not connected.", refused=True, reason="no_model")
    except ValueError:
        return Answer("No answer: the model's reply could not be read.", refused=True, reason="no_citation")
    if raw.get("refused"):
        return Answer(DECLINE, refused=True, reason="health", replayed=res.replayed)
    text = str(raw.get("answer", "")).strip()
    if ADVICE.search(text):
        return Answer(DECLINE, refused=True, reason="sounds_medical", replayed=res.replayed)
    kept, dropped = [], []
    for item in raw.get("citations", []):
        c = Citation(str(item.get("source", "")), str(item.get("ref", "")), str(item.get("quote", "")))
        if check_citation(case, c):
            if c.source == "policy":
                c.url = laws.quote(c.ref).url
            kept.append(c)
        else:
            dropped.append(c)
    if not kept:
        return Answer("I could not find that in your papers or the policy.", refused=True, reason="no_citation",
                      dropped=dropped, replayed=res.replayed)
    return Answer(text, kept, dropped=dropped, replayed=res.replayed)
