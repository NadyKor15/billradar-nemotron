"""The measurement: same model, same papers, three ways.

  simple     the model gets the sheets and one question a worried parent would ask.
  strong     the model gets the sheets, the hospital's full policy, the household numbers,
             our list of checks and our output schema. No code.
  billradar  the whole pipeline: reading, code checks, the checker.

Each pack has a key written before any run (`key.json`). Keys of the held-out packs are written
by someone who never sees the prompts or the code, and their hash is committed before the first
run (`seal`); scoring a held-out pack refuses a key whose hash does not match.

Scored per class: found k of n, false findings, wrong amounts, wrong deadlines. A strong prompt
catching up is a result we publish, not a failure we hide.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field

from billradar import policy
from billradar.checks import Kind, Report
from billradar.model_api import data_url
from billradar.policies import source

NOT_SCORED = {Kind.NM, Kind.B2}  # a note, and an action for the family (send the baby's insurance): no planted error in the keys
CLASSES = [k.value for k in Kind if k not in NOT_SCORED]
MODEL = "reader"
SIMPLE_QUESTION = "Here are my hospital bills and insurance letters. Is anything wrong? Can I pay less?"


class KeyItem(BaseModel):
    cls: str
    sheets: list[str] = Field(default_factory=list)  # e.g. ["S1", "S2"]; any overlap counts
    amount: Decimal | None = None
    deadline: date | None = None
    note: str = ""


class Key(BaseModel):
    pack_id: str
    split: str  # "work" or "holdout"
    items: list[KeyItem]


class Prediction(BaseModel):
    cls: str
    sheets: list[str] = Field(default_factory=list)
    amount: Decimal | None = None
    deadline: date | None = None


# --- sealing --------------------------------------------------------------------------------

def key_hash(path: Path) -> str:
    """Hash of the key's content, independent of whitespace and key order."""
    canon = json.dumps(json.loads(path.read_text()), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def seal(paths: list[Path], ledger: Path) -> list[str]:
    lines = [f"{key_hash(p)}  {p.parent.name}/{p.name}" for p in sorted(paths)]
    with ledger.open("a") as f:
        f.write("\n".join(lines) + "\n")
    return lines


def check_seal(path: Path, ledger: Path) -> bool:
    want = f"{key_hash(path)}  {path.parent.name}/{path.name}"
    return ledger.exists() and want in ledger.read_text().splitlines()


# --- predictions from each path -----------------------------------------------------------------

def from_report(report: Report) -> list[Prediction]:
    return [Prediction(cls=f.kind.value, sheets=sorted({e.doc_id for e in f.evidence}), amount=f.amount,
                       deadline=f.deadline) for f in report.findings if f.kind not in NOT_SCORED]


STRONG_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["findings"],
    "properties": {"findings": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["cls", "sheets", "amount", "deadline", "explanation"],
        "properties": {
            "cls": {"type": "string", "enum": CLASSES},
            "sheets": {"type": "array", "items": {"type": "string"}},
            "amount": {"type": "string"},
            "deadline": {"type": "string"},
            "explanation": {"type": "string"},
        }}}},
}

STRONG_INSTRUCTIONS = """You help a family find money they are owed on hospital bills and insurance statements. You do not give medical advice.

You get photos of their paper sheets (S1, S2, ...) and digital files (D1, ...), the hospital's full financial assistance policy, the federal rule on the application period, and the household: size, yearly income, savings, insurance.

Check all of these and report each one you find, with the sheets it is on:
- E1: a bill line asks the patient for more than the insurance statement (EOB) says the patient owes for the same service. amount = the difference.
- E2: the same service billed twice (same date, code, charge). amount = what the patient is asked for the second one.
- E3: a credit or overpayment left on the account. amount = the credit.
- E4: one sheet shows two different amounts to pay.
- B1: newborn care billed under the mother's insurance ID.
- P1: pages of a statement missing ("Page N of M").
- F1: the household qualifies under the policy (use the 2026 federal poverty guidelines) and a bill has no financial assistance discount. amount = the discount on that bill.
- F3: the discount is on one bill of the same hospital stay (mom) but not on another (baby). amount = the discount missing.
- F2: the last day to apply for financial assistance, counted as the policy and the federal rule say. deadline = YYYY-MM-DD.
Amounts as numbers like "1054.00", "" if none. Report only what the papers and the policy support."""


def _media(sheets: list[tuple[str, bytes]], files: list[tuple[str, str, bytes]]) -> list[dict]:
    content: list[dict] = []
    for doc_id, jpeg in sheets:
        content += [{"type": "input_text", "text": f"Sheet {doc_id}:"},
                    {"type": "input_image", "image_url": data_url(jpeg, "image/jpeg")}]
    for doc_id, name, pdf in files:
        content += [{"type": "input_text", "text": f"File {doc_id} ({name}):"},
                    {"type": "input_file", "filename": name, "file_data": data_url(pdf, "application/pdf")}]
    return content


def simple_body(sheets, files) -> dict:
    return {"model": MODEL, "store": False, "stream": True, "reasoning": {"effort": "medium"},
            "max_output_tokens": 16000,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": SIMPLE_QUESTION}] + _media(sheets, files)}]}


def strong_body(sheets, files, policy_id: str, household: dict) -> dict:
    policy_pdf = source.pdf_path(policy_id).read_bytes()
    federal = source.page_texts("federal-501r")[0]
    stable = [{"type": "input_text", "text": STRONG_INSTRUCTIONS},
              {"type": "input_text", "text": "Federal rule (26 CFR 1.501(r)-1):\n" + federal[:20000]}]
    user = ([{"type": "input_file", "filename": f"{policy_id}.pdf", "file_data": data_url(policy_pdf, "application/pdf")},
             {"type": "input_text", "text": "Household: " + json.dumps(household)}] + _media(sheets, files))
    return {"model": MODEL, "store": False, "stream": True, "reasoning": {"effort": "medium"},
            "max_output_tokens": 32000,
            "text": {"format": {"type": "json_schema", "name": "strong_baseline", "schema": STRONG_SCHEMA, "strict": True}},
            "input": [{"role": "developer", "content": stable}, {"role": "user", "content": user}]}


def from_strong(text: str) -> list[Prediction]:
    from billradar.case import parse_date, parse_money
    out = []
    for f in json.loads(text).get("findings", []):
        out.append(Prediction(cls=f["cls"], sheets=f.get("sheets", []), amount=parse_money(f.get("amount")),
                              deadline=parse_date(f.get("deadline"))))
    return out


JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items", "other_money_claims"],
    "properties": {
        "items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["index", "found", "amount", "deadline"],
            "properties": {"index": {"type": "integer"}, "found": {"type": "boolean"},
                           "amount": {"type": "string"}, "deadline": {"type": "string"}}}},
        "other_money_claims": {"type": "integer"},
    },
}


def judge_body(answer: str, key: Key) -> dict:
    """The simple path answers in free text, so a judge maps it to the key. The judge sees the key,
    never the pipeline; its raw answer is kept next to the score."""
    listing = "\n".join(f"{i}. [{k.cls}] sheets {', '.join(k.sheets) or '-'}; {k.note}" for i, k in enumerate(key.items))
    text = (f"An assistant answered a family about their hospital papers. Answer:\n<<<\n{answer}\n>>>\n\n"
            f"Known issues in these papers:\n{listing}\n\nFor each known issue: did the answer clearly point it out "
            "(found), and if it gave an amount or date for it, copy it. Then count other claims of money owed to "
            "the family that are not in the list (other_money_claims).")
    return {"model": MODEL, "store": False, "stream": True, "reasoning": {"effort": "medium"},
            "max_output_tokens": 8000,
            "text": {"format": {"type": "json_schema", "name": "judge", "schema": JUDGE_SCHEMA, "strict": True}},
            "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}]}


def from_judge(text: str, key: Key) -> tuple[list[Prediction], int]:
    from billradar.case import parse_date, parse_money
    data = json.loads(text)
    preds = []
    for it in data.get("items", []):
        if it.get("found") and 0 <= it.get("index", -1) < len(key.items):
            k = key.items[it["index"]]
            preds.append(Prediction(cls=k.cls, sheets=k.sheets, amount=parse_money(it.get("amount")),
                                    deadline=parse_date(it.get("deadline"))))
    return preds, int(data.get("other_money_claims", 0))


# --- scoring --------------------------------------------------------------------------------------

@dataclass
class ClassScore:
    n: int = 0
    found: int = 0
    false: int = 0
    wrong_amount: int = 0
    no_amount: int = 0  # found, but said no amount where the key has one
    wrong_deadline: int = 0


@dataclass
class Score:
    by_class: dict[str, ClassScore] = field(default_factory=dict)
    extra_false: int = 0  # unmatched money claims the judge counted (simple path)

    def add(self, other: "Score") -> None:
        for c, s in other.by_class.items():
            t = self.by_class.setdefault(c, ClassScore())
            t.n += s.n; t.found += s.found; t.false += s.false
            t.wrong_amount += s.wrong_amount; t.no_amount += s.no_amount; t.wrong_deadline += s.wrong_deadline
        self.extra_false += other.extra_false


def score(key: Key, preds: list[Prediction], extra_false: int = 0) -> Score:
    s = Score(extra_false=extra_false)
    left = list(preds)
    for k in key.items:
        cs = s.by_class.setdefault(k.cls, ClassScore())
        cs.n += 1
        match = next((p for p in left if p.cls == k.cls and (not k.sheets or not p.sheets or set(k.sheets) & set(p.sheets))),
                     None)
        if not match:
            continue
        left.remove(match)
        cs.found += 1
        if k.amount is not None and match.amount is None:
            cs.no_amount += 1
        elif k.amount is not None and abs(match.amount - k.amount) > Decimal("1.00"):
            cs.wrong_amount += 1
        if k.deadline is not None and match.deadline != k.deadline:
            cs.wrong_deadline += 1
    for p in left:
        s.by_class.setdefault(p.cls, ClassScore()).false += 1
    return s


def table(results: dict[str, Score]) -> str:
    """Markdown: one row per class, one column per path, 'k of n' with falses and wrong numbers."""
    paths = list(results)
    classes = sorted({c for r in results.values() for c in r.by_class}, key=CLASSES.index)
    rows = ["| Class | " + " | ".join(paths) + " |", "|---|" + "---|" * len(paths)]
    for c in classes:
        cells = []
        for p in paths:
            cs = results[p].by_class.get(c, ClassScore())
            cell = f"{cs.found} of {cs.n}"
            extras = [f"{cs.false} false" if cs.false else "", f"{cs.wrong_amount} wrong $" if cs.wrong_amount else "",
                      f"{cs.no_amount} no $" if cs.no_amount else "",
                      f"{cs.wrong_deadline} wrong date" if cs.wrong_deadline else ""]
            extras = [e for e in extras if e]
            cells.append(cell + (f" ({', '.join(extras)})" if extras else ""))
        rows.append(f"| {c} | " + " | ".join(cells) + " |")
    if any(r.extra_false for r in results.values()):
        rows.append("| other money claims not in the key | " + " | ".join(str(results[p].extra_false) for p in paths) + " |")
    return "\n".join(rows)


def verified_policy(policy_id: str) -> bool:
    return all(policy.verify_fragment(f).found for f in policy.load(policy_id).fragments.values())
