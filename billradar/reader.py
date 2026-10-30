"""Nemotron 3.5 Super VL reads every sheet and digital file of the case (in groups of 8 sheets).

The model reads and links (which statement, whose bill, which claim, which line).
It returns text exactly as printed; code turns amounts and dates into numbers and
refuses anything it cannot parse instead of guessing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from billradar.case import Document, DocKind, Line, Source, parse_date, parse_money
from billradar.model_api import ModelError, data_url
from billradar.nemotron_client import NemotronClient

MODEL = "reader"

_s = {"type": "string"}
_b = {"type": "boolean"}

LINE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["line_no", "service_date", "code", "description", "charges", "insurance_paid",
                 "adjustment", "fa_discount", "patient_responsibility", "claim_ref", "denial_codes"],
    "properties": {
        "line_no": {"type": "integer"},
        "service_date": _s, "code": _s, "description": _s, "charges": _s, "insurance_paid": _s,
        "adjustment": _s, "fa_discount": _s, "patient_responsibility": _s, "claim_ref": _s, "denial_codes": _s,
    },
}

DOC_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["doc_id", "kind", "patient", "provider", "account_ref", "claim_ref", "statement_date",
                 "service_from", "service_to", "first_after_discharge", "insurance_payment_date",
                 "total_charges", "insurance_paid", "fa_discount", "patient_balance", "amount_due_coupon",
                 "member_ref", "page_no", "page_count", "lines"],
    "properties": {
        "doc_id": _s,
        "kind": {"type": "string", "enum": [k.value for k in DocKind]},
        "patient": _s, "provider": _s, "account_ref": _s, "claim_ref": _s, "statement_date": _s,
        "service_from": _s, "service_to": _s, "first_after_discharge": _b, "insurance_payment_date": _s,
        "total_charges": _s, "insurance_paid": _s, "fa_discount": _s, "patient_balance": _s,
        "amount_due_coupon": _s, "member_ref": _s, "page_no": {"type": "integer"}, "page_count": {"type": "integer"},
        "lines": {"type": "array", "items": LINE_SCHEMA},
    },
}

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["documents"],
    "properties": {"documents": {"type": "array", "items": DOC_SCHEMA}},
}

INSTRUCTIONS = """You read a family's medical billing papers for BillRadar, a helper for hospital bills and insurance statements. You do not give medical advice.

You get photos of paper sheets (S1, S2, ...) and digital files (D1, D2, ...). Return one entry per sheet or file, using the id you were given. Never invent a document and never skip one.

For each document:
- kind: hospital_statement (a bill or statement with a balance), itemized_bill (line-by-line charges), eob (Explanation of Benefits from the insurance plan, "this is not a bill"), fa_letter (a financial assistance decision), payment_receipt, other.
- patient: the patient as printed. If a token like [PATIENT_1] is printed instead of a name, copy the token exactly with brackets removed: PATIENT_1.
- account_ref, claim_ref: copy exactly as printed, including tokens like [ACCT_1].
- Dates: YYYY-MM-DD. If a date is not printed or not readable, return "".
- Amounts: copy the text as printed, e.g. "$1,240.00" or "(312.40)". If not printed, return "". Never calculate an amount.
- first_after_discharge: true only if the sheet says it is the first statement or first bill after the hospital stay.
- insurance_payment_date: the date the plan paid, if printed (EOBs).
- fa_discount: a financial assistance or charity discount shown on the bill, if any.
- patient_responsibility on an EOB line: what the plan says the patient may owe. On a bill line: what the bill asks the patient to pay for that line.
- patient_balance: the "balance due" / "your balance" printed on the sheet. amount_due_coupon: the amount in a "PAY THIS AMOUNT" / "amount enclosed" coupon box, if the sheet has one. Copy each as printed even if they disagree.
- member_ref: the insurance member ID printed for this document (copy tokens exactly).
- page_no, page_count: from "Page 2 of 5"; 0 if not printed.
- denial_codes on a line: remark, reason or denial codes printed for that line, as printed (e.g. "X17" or "X05, X42"); "" if none.
- A credit or overpayment is printed in parentheses or with CR: copy it as printed, e.g. "(25.00)".
- lines: every printed charge or claim line, in order, line_no as printed or counted from 1.
Copy what is printed. Do not fix, round or guess anything."""


@dataclass
class ReadResult:
    documents: list[Document]
    problems: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0
    replayed: bool = True


def request_body(sheets: list[tuple[str, bytes]], files: list[tuple[str, str, bytes]],
                 effort: str = "low", max_output_tokens: int = 32000) -> dict:
    """sheets: (doc_id, jpeg). files: (doc_id, filename, pdf bytes). Stable text first for the prefix cache."""
    ids = ", ".join([d for d, _ in sheets] + [d for d, _, _ in files])
    content: list[dict] = [{"type": "input_text", "text": f"Documents in this case: {ids}. Images and files follow in this order."}]
    for doc_id, jpeg in sheets:
        content.append({"type": "input_text", "text": f"Sheet {doc_id}:"})
        content.append({"type": "input_image", "image_url": data_url(jpeg, "image/jpeg")})
    for doc_id, name, pdf in files:
        content.append({"type": "input_text", "text": f"File {doc_id} ({name}):"})
        content.append({"type": "input_file", "filename": name, "file_data": data_url(pdf, "application/pdf")})
    return {
        "model": MODEL,
        "store": False,
        "stream": True,
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "text": {"format": {"type": "json_schema", "name": "billradar_case", "schema": SCHEMA, "strict": True}},
        "input": [
            {"role": "developer", "content": [{"type": "input_text", "text": INSTRUCTIONS}]},
            {"role": "user", "content": content},
        ],
    }


_MONEY = ("charges", "insurance_paid", "adjustment", "fa_discount", "patient_responsibility")
_DOC_MONEY = ("total_charges", "insurance_paid", "fa_discount", "patient_balance", "amount_due_coupon")
_DOC_DATES = ("statement_date", "service_from", "service_to", "insurance_payment_date")


def _money(raw: dict, key: str, where: str, problems: list[str]):
    text = raw.get(key, "")
    value = parse_money(text)
    if text and value is None:
        problems.append(f"{where}: {key} {text!r} is not an amount; left empty")
    return value


def _date(raw: dict, key: str, where: str, problems: list[str]):
    text = raw.get(key, "")
    value = parse_date(text)
    if text and value is None:
        problems.append(f"{where}: {key} {text!r} is not a date; left empty")
    return value


def to_documents(payload: dict, expected: dict[str, Source]) -> tuple[list[Document], list[str]]:
    """The reader's JSON -> validated Documents. `expected` maps every doc id we sent to its source."""
    problems: list[str] = []
    docs: list[Document] = []
    seen: set[str] = set()
    for raw in payload.get("documents", []):
        doc_id = raw.get("doc_id", "").strip()
        if doc_id not in expected:
            problems.append(f"model returned unknown document {doc_id!r}; ignored")
            continue
        if doc_id in seen:
            problems.append(f"model returned {doc_id} twice; second copy ignored")
            continue
        seen.add(doc_id)
        lines = []
        for i, rl in enumerate(raw.get("lines", []), start=1):
            where = f"{doc_id} line {rl.get('line_no') or i}"
            lines.append(Line(
                line_no=rl.get("line_no") or i,
                service_date=_date(rl, "service_date", where, problems),
                code=rl.get("code", "").strip(), description=rl.get("description", "").strip(),
                claim_ref=rl.get("claim_ref", "").strip(), denial_codes=rl.get("denial_codes", "").strip(),
                **{k: _money(rl, k, where, problems) for k in _MONEY},
            ))
        docs.append(Document(
            doc_id=doc_id, source=expected[doc_id], kind=DocKind(raw.get("kind", "other")),
            patient=raw.get("patient", "").strip().strip("[]"), provider=raw.get("provider", "").strip(),
            account_ref=raw.get("account_ref", "").strip(), claim_ref=raw.get("claim_ref", "").strip(),
            first_after_discharge=bool(raw.get("first_after_discharge")),
            member_ref=raw.get("member_ref", "").strip(),
            page_no=raw.get("page_no") or None, page_count=raw.get("page_count") or None,
            lines=lines,
            **{k: _date(raw, k, doc_id, problems) for k in _DOC_DATES},
            **{k: _money(raw, k, doc_id, problems) for k in _DOC_MONEY},
        ))
    missing = [d for d in expected if d not in seen]
    if missing:
        problems.append(f"model did not return {', '.join(missing)}")
    order = list(expected)
    docs.sort(key=lambda d: order.index(d.doc_id))
    return docs, problems


def batches(sheets: list, files: list, max_images: int | None) -> list[tuple[list, list]]:
    """One request when the model takes every image at once. Nemotron 3.5 Super VL on
    Token Factory takes at most 8 images per prompt: sheets go in groups of 8, the files with the last group."""
    if not max_images or len(sheets) <= max_images:
        return [(sheets, files)]
    groups = [sheets[i:i + max_images] for i in range(0, len(sheets), max_images)]
    return [(g, files if i == len(groups) - 1 else []) for i, g in enumerate(groups)]


def read_case(client: NemotronClient, sheets: list[tuple[str, bytes]], files: list[tuple[str, str, bytes]],
              effort: str = "low") -> ReadResult:
    expected = {d: Source.PAPER for d, _ in sheets} | {d: Source.PDF for d, _, _ in files}
    payload: dict = {"documents": []}
    usage: dict = {}
    seconds, replayed = 0.0, True
    for part_sheets, part_files in batches(sheets, files, getattr(client, "max_images", None)):
        res = client.respond(request_body(part_sheets, part_files, effort), label="read")
        try:
            payload["documents"] += json.loads(res.text).get("documents", [])
        except json.JSONDecodeError as exc:
            raise ModelError(f"reader answer is not JSON ({exc}); first 200 chars: {res.text[:200]!r}") from exc
        for k in ("input_tokens", "output_tokens"):
            usage[k] = usage.get(k, 0) + res.usage.get(k, 0)
        seconds += res.seconds
        replayed &= res.replayed
    docs, problems = to_documents(payload, expected)
    return ReadResult(docs, problems, usage, round(seconds, 2), replayed)
