"""Questions about the case, with quotes the code picked. The model chooses, it never types a quote.

Order:
  1. Health questions are declined by code before any model call (ask.is_health_question).
  2. Code lists candidate quotes C1..Cn: every value printed on every sheet (amounts in both forms, dates,
     codes, line descriptions, ids) and every sentence of the verified policy and law quotes for this case.
     Each candidate passes ask.check_citation before the model sees it.
  3. The model (Nemotron 3 Super) gets the case without names, the findings and the candidates, and answers
     with the ids of the candidates it relies on.
  4. Citations are built by code from those ids and checked again; an unknown id is dropped, an answer with
     no citation left is not shown. Medical-sounding answers are not shown (ask.ADVICE).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal

from billradar import ask
from billradar.case import Case
from billradar.checks import Report
from billradar.model_api import ModelError

ENABLED = os.environ.get("BILLRADAR_QA_CITE", "1") != "0"  # ask.answer goes through here unless set to 0

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answer", "refused", "cite"],
    "properties": {
        "answer": {"type": "string"},
        "refused": {"type": "boolean"},
        "cite": {"type": "array", "items": {"type": "string"}},
    },
}
INSTRUCTIONS = """You answer a family's question about their hospital bills and insurance statements.
Use only what is given: the papers as read (sheet ids like S3, files like D1), the findings BillRadar's code computed, and the candidate quotes.
The candidates are quotes the code found on the papers and in the policy and law texts, each with an id like C12.
Cite by id only, in "cite": pick the candidates that show what your answer says. Never write a quote yourself.
State the exact amount, date or sheet id the question asks for, as printed or as computed in the findings.
Every answer cites at least one id. For an amount or a date that a finding computed, give the finding's own amount or deadline and cite from that finding's "evidence_ids": the sheets, lines and policy text it came from.
If the papers do not answer the question, say so with no claim you cannot cite.
If the question is about health, symptoms, medicines or treatment, set refused to true: you help with bills only.
Plain English, short. Never promise an outcome."""


@dataclass(frozen=True)
class Candidate:
    id: str
    source: str  # "sheet" | "policy"
    ref: str  # sheet id or policy ref
    label: str  # where on the sheet: "patient_balance", "line 3 code", ...
    quote: str

    def citation(self) -> ask.Citation:
        return ask.Citation(self.source, self.ref, self.quote)


def _value(v) -> list[str]:
    if v is None or v == "" or v is False or v is True:
        return []
    if isinstance(v, Decimal):
        return [f"{v:,.2f}"] if abs(v) >= 1000 else [f"{v:.2f}"]
    return [str(v)]


def _sentences(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[.;:])\s+", text) if len(p.strip()) >= 12]
    return parts or ([text.strip()] if text.strip() else [])


def candidates(case: Case, report: Report) -> list[Candidate]:
    from billradar.verifier import redacted
    c = redacted(case)
    raw: list[tuple[str, str, str, str]] = []
    for doc in c.documents:
        for name, v in doc:
            if name in ("doc_id", "source", "kind", "lines", "first_after_discharge", "page_no", "page_count"):
                continue
            for q in _value(v):
                raw.append(("sheet", doc.doc_id, name, q))
        for line in doc.lines:
            for name, v in line:
                if name == "line_no":
                    continue
                for q in _value(v):
                    raw.append(("sheet", doc.doc_id, f"line {line.line_no} {name}", q))
    for q in ask._quotes(case, report):
        for s in _sentences(q["quote"]):
            raw.append(("policy", q["ref"], q["title"], s))
    out, seen = [], set()
    for source, ref, label, quote in raw:
        if (source, ref, quote) in seen:
            continue
        cand = Candidate(f"C{len(out) + 1}", source, ref, label, quote)
        if ask.check_citation(case, cand.citation()):  # only quotes the checker accepts are offered
            seen.add((source, ref, quote))
            out.append(cand)
    return out


def evidence_ids(f, cands: list[Candidate], cap: int = 16) -> list[str]:
    """Candidates the code itself used for a finding: its sheets (the lines it names, or the sheet's own
    fields when it names none) and its policy and law text."""
    lines = {(e.doc_id, e.line_no) for e in f.evidence}
    sheets = {e.doc_id for e in f.evidence}
    refs = set(f.policy_refs) | set(f.laws)
    out = []
    for x in cands:
        if x.source == "policy" and x.ref in refs:
            out.append(x.id)
        elif x.source == "sheet" and x.ref in sheets:
            m = re.match(r"line (\d+) ", x.label)
            if (m and (x.ref, int(m.group(1))) in lines) or (not m and (x.ref, None) in lines):
                out.append(x.id)
    sheet_ids = [i for i in out if by_source(cands, i) == "sheet"]
    policy_ids = [i for i in out if by_source(cands, i) == "policy"]
    return sheet_ids[: cap // 2] + policy_ids[: cap - min(len(sheet_ids), cap // 2)]


def by_source(cands: list[Candidate], cid: str) -> str:
    return cands[int(cid[1:]) - 1].source


def request_body(case: Case, report: Report, question: str, cands: list[Candidate]) -> dict:
    body = ask.request_body(case, report, question)
    payload = json.loads(body["input"][1]["content"][0]["text"])
    payload.pop("quotes", None)  # the policy sentences are in the candidates now
    for item, f in zip(payload["findings"], report.findings):
        item["evidence_ids"] = evidence_ids(f, cands)
    payload["candidates"] = [{"id": x.id, "source": x.source, "ref": x.ref, "where": x.label, "quote": x.quote}
                             for x in cands]
    body["text"] = {"format": {"type": "json_schema", "name": "billradar_answer_cited", "schema": SCHEMA,
                               "strict": True}}
    body["input"] = [
        {"role": "developer", "content": [{"type": "input_text", "text": INSTRUCTIONS}]},
        {"role": "user", "content": [{"type": "input_text", "text": json.dumps(payload, default=str)}]},
    ]
    return body


def answer(client, case: Case, report: Report, question: str) -> ask.Answer:
    if ask.is_health_question(question):
        return ask.Answer(ask.DECLINE, refused=True, reason="health")
    cands = candidates(case, report)
    by_id = {x.id: x for x in cands}
    try:
        res = client.respond(request_body(case, report, question, cands), label="ask")
        raw = json.loads(res.text)
    except ModelError:
        return ask.Answer("No answer yet: the model is not connected.", refused=True, reason="no_model")
    except ValueError:
        return ask.Answer("No answer: the model's reply could not be read.", refused=True, reason="no_citation")
    if raw.get("refused"):
        return ask.Answer(ask.DECLINE, refused=True, reason="health", replayed=res.replayed)
    text = str(raw.get("answer", "")).strip()
    if ask.ADVICE.search(text):
        return ask.Answer(ask.DECLINE, refused=True, reason="sounds_medical", replayed=res.replayed)
    kept, dropped = [], []
    for cid in dict.fromkeys(str(i).strip() for i in raw.get("cite", [])):
        cand = by_id.get(cid)
        if cand is None:
            dropped.append(ask.Citation("unknown", cid, ""))
            continue
        c = cand.citation()
        if ask.check_citation(case, c):  # checked again, the same way as a typed quote
            if c.source == "policy":
                c.url = ask.laws.quote(c.ref).url
            kept.append(c)
        else:
            dropped.append(c)
    if not kept:
        return ask.Answer("I could not find that in your papers or the policy.", refused=True, reason="no_citation",
                          dropped=dropped, replayed=res.replayed)
    return ask.Answer(text, kept, dropped=dropped, replayed=res.replayed)
