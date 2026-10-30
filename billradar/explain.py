"""Bill lines and codes in plain words. What a line means on the bill, never what it means for your health.

Code families come from the code itself:
  hospital revenue codes (4 digits, 0xxx): the billing category, by the first three digits
  CPT procedure codes (5 digits): the section of the code book, by range
  HCPCS Level II (a letter and 4 digits): the kind of item, by the letter
  adjustment codes on insurance statements (CO-45, PR-2): who carries the amount, and the common reasons
  diagnosis codes (ICD-10): named as diagnosis codes and not explained: BillRadar gives no medical advice
A code we do not know is sent to the text model with the line around it, and its
answer is shown with its source and only if it passes the same no-medical-advice check.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal

from billradar.case import Line

# Hospital revenue code families by the first three digits (as a number: 11 -> "011x").
REVENUE: list[tuple[int, int, str]] = [
    (10, 10, "an all-inclusive daily rate"),
    (11, 16, "the hospital room and board"),
    (17, 17, "newborn nursery care"),
    (20, 21, "intensive or coronary care unit"),
    (25, 25, "medicines from the hospital pharmacy"),
    (26, 26, "IV therapy"),
    (27, 27, "medical and surgical supplies"),
    (30, 31, "laboratory tests"),
    (32, 32, "X-rays"),
    (35, 35, "a CT scan"),
    (36, 36, "the operating room"),
    (37, 37, "anesthesia (the hospital's part)"),
    (38, 39, "blood and blood storage"),
    (40, 40, "other imaging, such as ultrasound"),
    (41, 41, "respiratory care"),
    (42, 44, "physical, occupational or speech therapy"),
    (45, 45, "the emergency room"),
    (48, 48, "heart tests"),
    (51, 52, "a clinic visit"),
    (61, 61, "an MRI"),
    (63, 63, "medicines listed one by one"),
    (72, 72, "the labor and delivery room"),
    (73, 73, "an EKG"),
    (76, 76, "a treatment or observation room"),
    (96, 98, "a professional fee billed by the hospital"),
]
# CPT sections by range.
CPT: list[tuple[int, int, str]] = [
    (100, 1999, "anesthesia for a procedure"),
    (10004, 58999, "a procedure or surgery"),
    (59000, 59899, "maternity care and delivery"),
    (59900, 69990, "a procedure or surgery"),
    (70010, 79999, "imaging (X-ray, ultrasound, CT, MRI)"),
    (80047, 89398, "a lab or pathology test"),
    (90281, 99199, "a medical service, test or treatment"),
    (99202, 99499, "a doctor's visit or the doctor's daily care in the hospital"),
]
HCPCS = {
    "A": "supplies or transport", "E": "medical equipment", "G": "a procedure or service",
    "J": "a drug given by injection or infusion", "Q": "a temporary code for a drug or supply",
}
GROUP = {
    "CO": "the provider writes this off under its contract: neither you nor your plan pays it",
    "PR": "this is your share",
    "OA": "another adjustment, not your share by itself",
    "PI": "the plan's own adjustment, not your share by itself",
}
REASON = {
    "1": "deductible", "2": "coinsurance", "3": "copay",
    "16": "the claim was missing information", "18": "a duplicate claim",
    "22": "another plan should pay first", "27": "after your coverage ended",
    "29": "the claim was sent too late", "45": "above the plan's contracted price",
    "50": "the plan says it was not medically necessary", "96": "not covered",
    "97": "included in another service's payment", "109": "not covered by this plan",
    "197": "approval was needed before the service", "204": "not covered by your benefit plan",
    "242": "the provider is not in the plan's network",
}
MEDICAL = re.compile(r"diagnos|treat(ment)?\b|symptom|you should take|dose|prescri|cure|disease", re.I)


@dataclass(frozen=True)
class Explained:
    code: str
    family: str  # "revenue" | "cpt" | "hcpcs" | "adjustment" | "diagnosis" | "model" | "unknown"
    plain: str
    source: str  # where the meaning comes from


def _in(table: list[tuple[int, int, str]], n: int) -> str | None:
    return next((text for lo, hi, text in table if lo <= n <= hi), None)


def code(raw: str) -> Explained | None:
    c = raw.strip().upper().replace(" ", "")
    if not c:
        return None
    if re.fullmatch(r"0\d{3}", c):
        text = _in(REVENUE, int(c[:3]))
        if text:
            return Explained(c, "revenue", f"Hospital charge for {text}.", "hospital revenue code family")
        return Explained(c, "revenue", "A hospital billing category.", "hospital revenue code")
    if re.fullmatch(r"\d{5}", c):
        text = _in(CPT, int(c))
        if text:
            return Explained(c, "cpt", f"Procedure code for {text}.", "CPT code section")
    if re.fullmatch(r"[A-V]\d{4}", c):
        return Explained(c, "hcpcs", f"Item code for {HCPCS.get(c[0], 'a supply or service')}.", "HCPCS Level II letter")
    m = re.fullmatch(r"(CO|PR|OA|PI)-?(\d{1,3})", c)
    if m:
        group, reason = m.groups()
        why = REASON.get(reason)
        plain = f"Adjustment: {GROUP[group]}" + (f" (reason: {why})." if why else ".")
        return Explained(c, "adjustment", plain, "claim adjustment group and reason codes")
    if re.fullmatch(r"[A-TV-Z]\d{2}(\.\w{1,4})?", c) and "." in c:
        return Explained(c, "diagnosis", "A diagnosis code. BillRadar does not explain diagnoses.", "ICD-10 format")
    return None


def _money(x: Decimal | None) -> str:
    return f"${x:,.2f}" if x is not None else ""


def line(ln: Line) -> str:
    """One printed line in plain words: what it is, and where the money went."""
    what = code(ln.code)
    parts = [what.plain if what and what.family != "unknown" else (
        f"{ln.description.strip().capitalize()}." if ln.description.strip() else "A charge.")]
    if ln.charges is not None:
        parts.append(f"Charged {_money(ln.charges)}.")
    if ln.insurance_paid is not None and ln.insurance_paid:
        parts.append(f"Insurance paid {_money(ln.insurance_paid)}.")
    if ln.adjustment:
        parts.append(f"{_money(abs(ln.adjustment))} was taken off by contract: nobody owes it.")
    if ln.fa_discount:
        parts.append(f"Financial assistance took off {_money(abs(ln.fa_discount))}.")
    if ln.patient_responsibility is not None:
        if ln.patient_responsibility and ln.patient_responsibility < 0:  # "$-40.00" reads as a typo: it is a credit
            parts.append(f"A credit to you: {_money(abs(ln.patient_responsibility))}.")
        else:
            parts.append(f"Your share: {_money(ln.patient_responsibility)}." if ln.patient_responsibility
                         else "Your share: nothing.")
    if ln.denial_codes.strip():
        codes = [c.strip() for c in ln.denial_codes.split(",") if c.strip()]
        known = [x for x in (code(c) for c in codes) if x and x.family == "adjustment"]
        parts.append(" ".join(x.plain for x in known) if known else
                     f"Remark code {', '.join(codes)}: its meaning is printed on the back of the statement.")
    return " ".join(parts)


def unknown_codes(lines: list[Line]) -> list[str]:
    seen = []
    for ln in lines:
        c = ln.code.strip().upper()
        if c and code(c) is None and c not in seen:
            seen.append(c)
    return seen


# --- the model for codes we do not know (replay until credits) -------------------------------

MODEL = "reader"
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["codes"],
    "properties": {"codes": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["code", "plain"],
        "properties": {"code": {"type": "string"}, "plain": {"type": ["string", "null"]}}}}},
}
INSTRUCTIONS = """You explain codes printed on US hospital bills and insurance statements, in one short plain-English sentence each, for a family that is not used to medical billing.
Say only what the code means on the bill (a kind of charge, a supply, a billing rule). Never say anything about health, a diagnosis, a treatment or what the person should do medically.
If you do not know a code for sure, return null for it. Do not guess."""


def request_body(codes: list[tuple[str, str]]) -> dict:
    """codes: (code, the description printed next to it)."""
    return {
        "model": MODEL, "store": False, "stream": True, "max_output_tokens": 2000,
        "text": {"format": {"type": "json_schema", "name": "billradar_codes", "schema": SCHEMA, "strict": True}},
        "input": [
            {"role": "developer", "content": [{"type": "input_text", "text": INSTRUCTIONS}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(
                [{"code": c, "printed_description": d} for c, d in codes])}]},
        ],
    }


def with_model(client, codes: list[tuple[str, str]]) -> dict[str, Explained]:
    """Codes the tables do not know, explained by the text model. A missing recording, a null answer or an answer
    that sounds medical gives nothing for that code: the screen then shows the printed description only."""
    from billradar import providers
    from billradar.model_api import ModelError
    if not codes:
        return {}
    try:
        res = client.respond(request_body(codes), label="explain-codes")
        items = json.loads(res.text).get("codes", [])
    except (ModelError, ValueError):
        return {}
    asked = {c.upper() for c, _ in codes}
    out = {}
    for it in items:
        c, plain = str(it.get("code", "")).upper(), it.get("plain")
        if c in asked and plain and not MEDICAL.search(plain):
            out[c] = Explained(c, "model", plain.strip(), providers.labels()["text"] + (" (replayed)" if res.replayed else ""))
    return out


# --- findings in plain words ---------------------------------------------------------------

FINDING = {
    "E1": "The bill asks for more than your insurance statement says you owe for the same service.",
    "E2": "The same service appears twice, same day and same price.",
    "E3": "There is a credit on your account: money that should come back to you.",
    "E4": "One sheet shows two different amounts to pay. Ask which one is right before paying.",
    "B1": "The baby's care was billed under mom's insurance ID, so the plan may have refused it.",
    "B2": "A doctors' office is asking you for the baby's care because no insurance has been matched to the baby yet.",
    "P1": "Some pages of a statement are missing from the pile; totals may be on them.",
    "F1": "Your income fits the hospital's financial assistance policy, but no discount is on the bill.",
    "F2": "The last day to apply for the hospital's financial assistance.",
    "F3": "One bill of the same stay has the assistance discount and another does not.",
    "N1": "An out-of-network doctor at an in-network hospital may charge you only your in-network share.",
    "A1": "The last day to appeal what your insurance refused to pay.",
    "I1": "Ask for the itemized bill to see every charge; errors often hide in the total.",
    "C1": "Without insurance, the hospital's own published cash price is lower than what this line charges.",
    "C2": "Your plan allowed more than the price the hospital publishes for your plan, so your share may be too high.",
}


def finding(kind: str) -> str | None:
    return FINDING.get(kind)
