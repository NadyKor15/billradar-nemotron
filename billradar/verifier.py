"""The checker. It only reads: it never changes a finding or a letter, it says what it could not confirm.

Two layers:
  1. Code. Every amount and date is recomputed from the sheets and the policy along a separate
     path (not from the finding's own `math`), every sheet and line a finding points to must
     exist, every quote must be on its page, and every dollar amount in a letter must trace back
     to the case. Wording that sounds like medical advice or a promise is flagged.
  2. Nemotron 3 Ultra (the second read, BILLRADAR_SECOND_READ=1): reads the same case, findings and letters and reports anything it
     disagrees with, using a strict schema. Its notes are shown to the person; they never edit.

The pipeline decides what to do with problems (hold a finding back); the checker does not.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from billradar import fpl, policy
from billradar import writer
from billradar.case import Case, DocKind, LocalDetails
from billradar.checks import Finding, Kind, Report
from billradar.nemotron_client import NemotronClient
from billradar.writer import Letter

CENT = Decimal("0.01")
RISKY_WORDS = ["diagnos", "treatment plan", "medical advice", "you should take", "we will send", "guarantee",
               "we promise", "you will get", "lawsuit", "sue "]


def _q(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Issue:
    subject: str  # "F3 #1", "letter dispute", ...
    severity: str  # "error" (do not show as is) or "note"
    what: str


@dataclass
class Verdict:
    issues: list[Issue] = field(default_factory=list)
    checked: int = 0
    model_notes: list[dict] = field(default_factory=list)
    checker: str = "second read"  # the model that read the findings, as shown on screen (checker_name)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def errors_for(self, subject: str) -> list[Issue]:
        return [i for i in self.issues if i.subject == subject and i.severity == "error"]


def subject(f: Finding, n: int) -> str:
    """"F1 #2 on D3": the paper in the name, so a reviewer cannot pin one finding's note on its twin."""
    doc = f.evidence[0].doc_id if f.evidence else ""
    return f"{f.kind.value} #{n}" + (f" on {doc}" if doc else "")


# --- independent recomputation ------------------------------------------------------------

def _line(case: Case, doc_id: str, line_no: int | None):
    try:
        d = case.doc(doc_id)
    except StopIteration:
        return None, None
    if line_no is None:
        return d, None
    return d, next((ln for ln in d.lines if ln.line_no == line_no), None)


def _expect_e1(case: Case, f: Finding) -> Decimal | None:
    (bill, bl), (_, el) = (_line(case, e.doc_id, e.line_no) for e in f.evidence[:2])
    if not bl or not el or bl.patient_responsibility is None or el.patient_responsibility is None:
        return None
    diff = bl.patient_responsibility - el.patient_responsibility
    if bill.fa_discount and bill.patient_balance is not None:
        share = bill.patient_balance / (bill.patient_balance + abs(bill.fa_discount))
        return _q(diff * share)
    return _q(diff)


def _tier_pct(case: Case) -> Decimal | None:
    pol = policy.load(case.policy_id)
    pct = Decimal(case.profile.annual_income) * 100 / fpl.guideline(case.profile.household_size,
                                                                     pol.fpg_year or fpl.latest_year())
    for t in pol.tiers:  # a separate walk over the tiers, whole percents as printed
        if int(pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) <= t.max_fpl_pct:
            return Decimal(t.discount_pct)
    return None


def _expect_discount(case: Case, f: Finding, report: Report) -> Decimal | None:
    target = next((e for e in f.evidence if e.note == "no discount"), None)
    if target is None:
        return None
    bill = case.doc(target.doc_id)
    pct = _tier_pct(case)
    if pct is None or bill.patient_balance is None:
        return None
    taken = sum((x.amount for x in report.findings
                 if x.kind in (Kind.E1, Kind.E2, Kind.C1) and x.amount and x.evidence[0].doc_id == bill.doc_id),
                Decimal(0))
    return _q((bill.patient_balance - taken) * pct / 100)


def _expect_deadline(case: Case):
    from billradar.checks import hospital_bills
    bills = hospital_bills(case)
    flagged = [b for b in bills if b.first_after_discharge and b.statement_date]
    dated = flagged or [b for b in bills if b.statement_date]
    if not dated:
        return None
    first = min(b.statement_date for b in dated)
    options = [first + timedelta(days=240)]
    w = policy.load(case.policy_id).window
    if "after_first_statement_days" in w:
        options.append(first + timedelta(days=w["after_first_statement_days"]))
    if "after_service_days" in w:
        ends = [b.service_to or b.service_from for b in bills if b.service_to or b.service_from]
        if ends:
            options.append(max(ends) + timedelta(days=w["after_service_days"]))
    if "after_last_insurance_payment_months" in w:
        paid = [d.insurance_payment_date for d in case.documents if d.insurance_payment_date]
        for d in case.documents:  # an EOB for hospital claims (revenue codes 0xxx) dates the payment
            if d.kind == DocKind.EOB and not d.insurance_payment_date and d.statement_date and any(
                    len(ln.code) == 4 and ln.code.startswith("0") and ln.code.isdigit() for ln in d.lines):
                paid.append(d.statement_date)
        if paid:
            p = max(paid)
            m = p.month - 1 + w["after_last_insurance_payment_months"]
            y, mo = p.year + m // 12, m % 12 + 1
            day = min(p.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30,
                              31, 31, 30, 31, 30, 31][mo - 1])
            options.append(p.replace(year=y, month=mo, day=day))
    return max(options)


def check_findings(case: Case, report: Report) -> Verdict:
    v = Verdict()
    counts: dict[str, int] = {}
    for f in report.findings:
        counts[f.kind.value] = counts.get(f.kind.value, 0) + 1
        s = subject(f, counts[f.kind.value])
        v.checked += 1
        for e in f.evidence:
            d, ln = _line(case, e.doc_id, e.line_no)
            if d is None:
                v.issues.append(Issue(s, "error", f"points to {e.doc_id}, which is not in the case"))
            elif e.line_no is not None and ln is None:
                v.issues.append(Issue(s, "error", f"points to line {e.line_no} of {e.doc_id}, which does not exist"))
        for ref in f.policy_refs:
            if not policy.verified(ref):
                v.issues.append(Issue(s, "error", f"quote {ref} is not on its page"))
        expected = None
        if f.kind == Kind.E1:
            expected = _expect_e1(case, f)
        elif f.kind in (Kind.F1, Kind.F3) and "fpl_pct" in f.math:
            expected = _expect_discount(case, f, report)
        elif f.kind == Kind.E2:
            shares = []
            for e in f.evidence[1::2]:  # every second evidence line is a repeat
                _, ln = _line(case, e.doc_id, e.line_no)
                if ln and ln.patient_responsibility and ln.patient_responsibility > 0:
                    shares.append(ln.patient_responsibility)
            expected = shares[0] if shares else None
        elif f.kind == Kind.E3:
            d, ln = _line(case, f.evidence[0].doc_id, f.evidence[0].line_no)
            src = ln.patient_responsibility if ln else (d.patient_balance if d else None)
            expected = abs(src) if src is not None else None
        elif f.kind == Kind.C1 and f.evidence and f.amount is not None:
            from billradar import prices
            _, ln = _line(case, f.evidence[0].doc_id, f.evidence[0].line_no)
            index = prices.load(case.policy_id)
            hit = prices.match(index, ln.code, ln.charges) if index and ln and ln.charges else None
            if isinstance(hit, tuple):  # the line's charge matched again, straight from the hospital's file
                price, units = hit
                owed = ln.patient_responsibility if ln.patient_responsibility is not None else ln.charges
                expected = min(_q(units * (price.gross - price.cash)), owed)
        elif f.kind == Kind.C2 and f.evidence and f.amount is not None and case.profile.plan:
            from billradar import prices
            _, ln = _line(case, f.evidence[0].doc_id, f.evidence[0].line_no)
            index = prices.load(case.policy_id)
            if index and ln and ln.charges is not None and ln.adjustment is not None:
                code = re.sub(r"^(?:MS-?)?DRG\s*#?\s*", "", ln.code.strip(), flags=re.I).upper()
                ctype = "MS-DRG" if code != ln.code.strip().upper() else f.math.get("code_type", "")
                rates = {r.dollar for r in index.plan_rates(case.profile.plan.payer, case.profile.plan.plan_name,
                                                             ctype, code)}
                if len(rates) == 1:  # the plan's one published rate, read again from the hospital's file
                    allowed = ln.charges - abs(ln.adjustment)
                    expected = min(_q(allowed - rates.pop()), ln.patient_responsibility or Decimal(0))
        elif f.kind == Kind.N1 and f.evidence:
            bill, _ = _line(case, f.evidence[0].doc_id, None)
            shares = [ln.patient_responsibility for e in f.evidence[1:]
                      for _, ln in [_line(case, e.doc_id, e.line_no)] if ln and ln.patient_responsibility is not None]
            if bill and bill.patient_balance is not None and shares:
                expected = _q(bill.patient_balance - sum(shares, Decimal(0)))
        if f.kind == Kind.F2:
            want = _expect_deadline(case)
            if want != f.deadline:
                v.issues.append(Issue(s, "error", f"deadline {f.deadline} but the sheets and policy give {want}"))
        elif f.amount is not None and expected is not None and expected != f.amount:
            v.issues.append(Issue(s, "error", f"amount {f.amount} but recomputed from the sheets: {expected}"))
        elif f.amount is not None and expected is None and f.kind not in (Kind.F1,):
            v.issues.append(Issue(s, "note", "amount could not be recomputed independently"))
    total = _q(sum((f.amount for f in report.findings if f.amount is not None), Decimal(0)))
    if total != report.total_may_come_off:
        v.issues.append(Issue("total", "error", f"total {report.total_may_come_off} but findings add up to {total}"))
    return v


def check_letters(case: Case, report: Report, letters: list[Letter], v: Verdict | None = None) -> Verdict:
    v = v or Verdict()
    blob = case.model_dump_json() + report.model_dump_json()
    # money only: a covered scan line kept in the tokens is a long run of digits, not an amount
    known = {Decimal(x).quantize(CENT) for x in re.findall(r'"(-?\d{1,12}(?:\.\d+)?)"', blob)}
    known |= {abs(k) for k in known}
    for letter in letters:
        s = f"letter {letter.kind}"
        v.checked += 1
        text = " ".join(p.text for p in letter.paragraphs)
        for amount in re.findall(r"\$([\d,]+\.\d\d)", text):
            if Decimal(amount.replace(",", "")) not in known:
                v.issues.append(Issue(s, "error", f"${amount} does not come from the case"))
        for ref in letter.refs:
            if not policy.verified(ref):
                v.issues.append(Issue(s, "error", f"quote {ref} is not on its page"))
        if re.search(r"\[(ACCT|PATIENT|MEMBER)_\d+\]", text):
            v.issues.append(Issue(s, "error", "a token is left in the letter instead of the real value"))
        low = text.lower()
        for w in RISKY_WORDS:
            if w in low:
                v.issues.append(Issue(s, "error", f"wording '{w.strip()}' sounds like advice or a promise"))
        if letter.dropped:
            v.issues.append(Issue(s, "note", f"{len(letter.dropped)} paragraph(s) left out: quote not verified"))
    return v


# --- the model pass -----------------------------------------------------------------------

MODEL = "checker"
REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["subject", "verdict", "reason"],
        "properties": {
            "subject": {"type": "string"},
            "verdict": {"type": "string", "enum": ["confirmed", "problem", "cannot_tell"]},
            "reason": {"type": "string"},
        }}}},
}
REVIEW_INSTRUCTIONS = """You check the work of BillRadar, a helper for hospital bills and insurance statements. You only read. You never rewrite a letter or a finding.

You get: the family's case as read from their papers (amounts as numbers), the findings with the sheets, lines and policy quotes behind each, and the letters. For every finding and every letter, answer:
- confirmed: every number, date and sheet reference follows from the case and the quoted policy text;
- problem: something does not follow (wrong amount, wrong date, a sheet or line that does not say this, a quote that does not support the claim, wording that sounds like medical advice or promises an outcome) - say exactly what;
- cannot_tell: the case does not hold enough to check it.
Use the subject names given (e.g. "F3 #1", "letter dispute"). Be brief and concrete."""


def redacted(case: Case) -> Case:
    """The same case without anything that only lives on the device: names, birth dates, address, real numbers."""
    people = [p.model_copy(update={"name": "", "birth_date": "", "employer": ""}) for p in case.profile.people]
    profile = case.profile.model_copy(update={"people": people, "local": LocalDetails()})
    return case.model_copy(update={"profile": profile})


def review_body(case: Case, report: Report, letters: list[Letter]) -> dict:
    """What the second read sees. Letters are rebuilt from the redacted case, so the model reads the same
    wording with tokens and placeholders, never the family's names or account numbers."""
    case = redacted(case)
    letters = writer.all_letters(case, report) if letters else []
    counts: dict[str, int] = {}
    findings = []
    for f in report.findings:
        counts[f.kind.value] = counts.get(f.kind.value, 0) + 1
        findings.append({"subject": subject(f, counts[f.kind.value]), "title": f.title, "detail": f.detail,
                         "amount": str(f.amount) if f.amount is not None else None,
                         "deadline": f.deadline.isoformat() if f.deadline else None,
                         "evidence": [e.model_dump() for e in f.evidence],
                         "math": {k: v for k, v in f.math.items() if k != "why_unsure"},  # what the code computed, to check
                         "quotes": [{"ref": r, "page": policy.load(r.split('#')[0]).fragment(r.split('#')[1]).page,
                                     "text": policy.load(r.split('#')[0]).fragment(r.split('#')[1]).quote}
                                    for r in f.policy_refs]})
    pol = policy.load(case.policy_id)
    year = pol.fpg_year or fpl.latest_year()
    payload = {
        "case": json.loads(case.model_dump_json(exclude={"profile": {"local": True}})),
        # what the code used that is not on the papers or in the policy quotes: the federal guideline for the percent
        "reference": {"federal_poverty_guideline": {
            "year": year, "household_size": case.profile.household_size,
            "amount": str(fpl.guideline(case.profile.household_size, year)), "source": fpl.SOURCE.get(year, ""),
            "annual_income": str(case.profile.annual_income), "percent_of_guideline": str(report.fpl_pct)},
            # the policy's discount table as rows (its quote prints the table flattened into one line)
            "policy_discount_table": [{"up_to_percent_of_guideline": t.max_fpl_pct, "discount_percent": t.discount_pct,
                                       "quote": f"{pol.policy_id}#{t.fragment}"} for t in pol.tiers]},
        "findings": findings,
        "letters": [{"subject": f"letter {x.kind}", "text": [p.text for p in x.paragraphs], "quotes": x.refs}
                    for x in letters],
    }
    return {
        "model": MODEL, "store": False, "stream": True, "reasoning": {"effort": "medium"},
        "max_output_tokens": 24000,  # 14 findings with their math: 16000 was cut once (29.10)
        "text": {"format": {"type": "json_schema", "name": "billradar_review", "schema": REVIEW_SCHEMA, "strict": True}},
        "input": [
            {"role": "developer", "content": [{"type": "input_text", "text": REVIEW_INSTRUCTIONS}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(payload, default=str)}]},
        ],
    }


def checker_name(client) -> str:
    """Screen name of the model that reviews (the Nemotron model id without its vendor prefix)."""
    from billradar import providers
    model = providers.models(client)["check"]
    return model.split("/")[-1]


def model_review(client: NemotronClient, case: Case, report: Report, letters: list[Letter], v: Verdict) -> Verdict:
    v.checker = checker_name(client)
    res = client.respond(review_body(case, report, letters), label="review")
    items = json.loads(res.text).get("items", [])
    v.model_notes = items
    papers = {d.doc_id for d in case.documents}
    for it in items:
        wrong = _says_missing(it.get("reason", "")) & papers if it.get("verdict") == "problem" else set()
        if wrong:  # checked by code: the paper it calls missing is in the case it got
            it["verdict"] = "overruled"
            it["code_check"] = f"{', '.join(sorted(wrong))} is in the case: the second read was wrong here"
            v.issues.append(Issue(it.get("subject", "?"), "note",
                                  f"{v.checker} said {', '.join(sorted(wrong))} does not exist; it does (checked by code)"))
        elif it.get("verdict") == "problem":
            v.issues.append(Issue(it.get("subject", "?"), "note", f"{v.checker}: {it.get('reason', '')}"))
    return v


_MISSING = re.compile(r"(?i)\b([SD]\d{1,2})\b[^.;]{0,40}?\b(?:does not exist|doesn't exist|is not in the case|"
                      r"not (?:present|found) in the case|is missing from the case)")


def _says_missing(reason: str) -> set[str]:
    """Paper ids the second read calls absent: "References sheet S10 which does not exist" -> {"S10"}."""
    return {m.group(1).upper() for m in _MISSING.finditer(reason)}


def verify(case: Case, report: Report, letters: list[Letter]) -> Verdict:
    return check_letters(case, report, letters, check_findings(case, report))


# --- confidence (Я11) ---------------------------------------------------------------------

CONFIRMED = 0.7  # below this a finding is borderline: shown under "worth asking", without a sum


def subjects(report: Report) -> dict[int, str]:
    """Subject names ("F3 #1") of the findings as the checker numbered them, by object identity."""
    counts: dict[str, int] = {}
    out = {}
    for f in report.findings:
        counts[f.kind.value] = counts.get(f.kind.value, 0) + 1
        out[id(f)] = subject(f, counts[f.kind.value])
    return out


def confidence(case: Case, f: Finding, s: str, v: Verdict) -> tuple[float, list[str]]:
    """How sure we are, 0..1, and why it is less than 1. Starts at 1: the finding already passed the checker's
    errors (sheets, lines, quotes, recomputed amounts), else it would have been held back."""
    c, why = 1.0, []
    notes = [i.what for i in v.issues if i.subject == s and i.severity == "note"]
    if any("could not be recomputed" in n for n in notes):
        c -= 0.25
        why.append("amount not recomputed on a second path")
    if f.kind in (Kind.F1, Kind.F3) and any(r.endswith("#liquid-assets") for r in f.policy_refs) \
            and case.profile.liquid_assets is None:
        c -= 0.2
        why.append("free care also depends on savings we do not know")
    for item in v.model_notes:
        if item.get("subject") != s:
            continue
        if item.get("verdict") == "problem":
            c -= 0.4
            why.append(f"{v.checker}: {item.get('reason', '')}")
        elif item.get("verdict") == "cannot_tell":
            c -= 0.15
            why.append(f"{v.checker} could not check it from the papers")
    return round(max(0.0, min(1.0, c)), 2), why


def grade(case: Case, report: Report, v: Verdict, names: dict[int, str] | None = None) -> Report:
    """Put a confidence on every finding; a finding under CONFIRMED becomes borderline and moves to
    "worth asking" without its sum. Read-only on the content: titles, amounts and evidence stay as they are."""
    from billradar.checks import split_borderline
    names = names or subjects(report)
    graded = []
    for f in report.findings:
        c, why = confidence(case, f, names.get(id(f), ""), v)
        update = {"confidence": c, "severity": f.severity or "high"}
        if c < CONFIRMED:
            update |= {"severity": "borderline", "math": f.math | {"why_unsure": "; ".join(why)}}
        graded.append(f.model_copy(update=update))
    asking = [f if f.confidence is not None else f.model_copy(update={"confidence": 0.5})
              for f in report.worth_asking]
    return split_borderline(report.model_copy(update={"findings": graded, "worth_asking": asking}))
