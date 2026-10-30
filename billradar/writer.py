"""Letters the family reviews and signs. Built in code from the case and the findings.

Every number in a letter comes from the case or a finding's math. Every rule a letter
leans on is a verified quote; a paragraph whose quote is not verified is dropped and
reported, never sent out unverified.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from billradar import appeal as appeals
from billradar import fpl, plans, policy
from billradar.case import Case, Document, Role
from billradar.checks import Kind, Report


@dataclass
class Paragraph:
    text: str
    refs: list[str] = field(default_factory=list)


@dataclass
class Letter:
    kind: str
    button: str
    to: str
    subject: str
    paragraphs: list[Paragraph]
    attachments: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # paragraphs removed because a quote did not verify

    @property
    def refs(self) -> list[str]:
        return [r for p in self.paragraphs for r in p.refs]


def _money(x) -> str:
    return f"${x:,.2f}"


def _a(number: int) -> str:
    """'an 85%', 'an 83%', 'an 80%', 'a 90%', 'a 100%' — by how the number is said."""
    return "an" if str(number).startswith("8") or number in (11, 18) else "a"


def _date(d: date | None) -> str:
    return f"{d:%B %-d, %Y}" if d else ""


def _account(case: Case, doc: Document) -> str:
    ref = doc.account_ref.strip()
    if ref.startswith("[") and ref.endswith("]"):
        return case.profile.local.tokens.get(ref[1:-1], "[account number]")
    return ref or "[account number]"


def _name(case: Case, token: str, fallback: str) -> str:
    return appeals.person_name(case, token, fallback)


def _mother(case: Case) -> str:
    m = next((p for p in case.profile.people if p.role == Role.MOTHER), None)
    return m.name if m and m.name else "[your name]"


def _billing_office(case: Case) -> str:
    return f"Patient Financial Services, {case.letterhead}" if case.letterhead else "Patient Financial Services"


def policy_name(pol, version: bool = True) -> str:
    """The policy as a letter names it: no revision month ("Revised May 2026") that a dated letter could contradict.
    `version`: say it is the current public version (under a quote); the letter's own sentence just names it."""
    name = re.sub(r",?\s*\b[Rr]evised\b.*$", "", pol.title).strip()
    return f"{name} (current public version)" if name != pol.title and version else name


DEMO_RULES = "Rules: the public Financial Assistance Policy and form of Memorial Regional Hospital (FL; MHS = Memorial Healthcare System), applied to the fictional Seaside case."


def verified(ref: str) -> bool:
    """A quote from a hospital policy, a law, or the family's insurance plan document."""
    if plans.is_ref(ref):
        return plans.verified(ref)
    try:
        return policy.verified(ref)
    except (FileNotFoundError, KeyError, ValueError):  # a law id not in the base yet
        return False


def _finalize(letter: Letter) -> Letter:
    kept = []
    for p in letter.paragraphs:
        bad = [r for r in p.refs if not verified(r)]
        if bad:
            letter.dropped.append(f"{p.text[:60]}... (unverified {', '.join(bad)})")
        else:
            kept.append(p)
    letter.paragraphs = kept
    return letter


def _services(start: date | None, end: date | None) -> str:
    """'services March 4, 2030 to March 6, 2030', 'services on March 4, 2030', or nothing when no date was read."""
    if start and end and start != end:
        return f"services {_date(start)} to {_date(end)}"
    return f"services on {_date(start or end)}" if (start or end) else ""


def itemized_bill(case: Case) -> Letter:
    accounts: dict[tuple[str, str], list] = {}  # one line per person and account; pages of one bill share it
    from billradar.checks import hospital_bills
    for b in (b for b in (hospital_bills(case) or case.bills()) if b.patient_balance):  # the office we write to
        acct = _account(case, b) if b.account_ref.strip() else ""  # not read: left out; a token with no value: a blank to fill
        key = (_name(case, b.patient, "the patient"), acct)
        dates = accounts.setdefault(key, [])
        dates += [d for d in (b.service_from, b.service_to) if d]
    lines = [", ".join(x for x in (who, f"account {acct}" if acct else "", _services(min(d) if d else None, max(d) if d else None)) if x)
             for (who, acct), d in accounts.items() if acct or d]  # a bill with neither account nor dates says nothing
    return _finalize(Letter(
        kind="itemized", button="Letter: request itemized bill", to=_billing_office(case),
        subject="Request for an itemized statement",
        paragraphs=[
            Paragraph("I am asking for an itemized statement for each of these accounts: " + "; ".join(lines) + "."),
            Paragraph("Florida law says the hospital must provide it after discharge upon request, within 7 days "
                      "of my request (Florida Statutes 395.301(1)(e)).",
                      ["fl-395-301#itemized-on-request", "fl-395-301#itemized-7-days"]),
            Paragraph("Please send it to the address below. Thank you."),
        ]))


def apply_discount(case: Case, report: Report) -> Letter | None:
    f3 = next((f for f in report.findings if f.kind == Kind.F3), None)
    if not f3:
        return None
    has, lacks = (case.doc(e.doc_id) for e in f3.evidence)
    pol = policy.load(case.policy_id)
    lacks_name = _name(case, lacks.patient, "my child")
    paragraphs = [
        Paragraph(f"Financial assistance was applied to account {_account(case, has)} for my hospital stay "
                  f"({_date(has.service_from)} to {_date(has.service_to)}). The account for {lacks_name}, "
                  f"{_account(case, lacks)}, is from the same stay and still shows the full balance of "
                  f"{_money(lacks.patient_balance)}."),
        Paragraph("Please apply the same financial assistance to that account. "
                  f"Your Financial Assistance Policy covers balances left after insurance payment and says the "
                  f"hospital will also consider accounts from the prior twelve months.",
                  [r for r in (f"{pol.policy_id}#insured-balances", f"{pol.policy_id}#prior-accounts")
                   if r.split("#")[1] in pol.fragments]),
        Paragraph("Please send me a corrected statement. Thank you."),
    ]
    return _finalize(Letter(kind="apply-discount", button=f"Letter: apply discount to {lacks_name}'s bill",
                            to=_billing_office(case), subject=f"Apply financial assistance to account {_account(case, lacks)}",
                            paragraphs=paragraphs))


def application(case: Case, report: Report) -> Letter | None:
    fa = [f for f in report.findings if f.kind in (Kind.F1, Kind.F3)]
    if not fa:
        return None
    pol = policy.load(case.policy_id)
    tier = pol.tier_for(report.fpl_pct)
    if tier is None:
        return None
    p = case.profile
    window = next((f for f in report.findings if f.kind == Kind.F2), None)
    paragraphs = [
        Paragraph(f"I am applying for financial assistance. My household has {p.household_size} people and our "
                  f"yearly income is {_money(p.annual_income)}, which is {report.fpl_pct}% of the {pol.fpg_year or fpl.latest_year()} federal "
                  f"poverty guidelines."),
        Paragraph(f"Your {policy_name(pol, version=False)} lists {_a(tier.discount_pct)} {tier.discount_pct}% discount at this income level.",
                  [f"{pol.policy_id}#{tier.fragment}"]),
    ]
    if window and window.deadline:
        paragraphs.append(Paragraph(f"I am applying before {_date(window.deadline)}, within the application period.",
                                    window.policy_refs))
    paragraphs.append(Paragraph("The completed Financial Evaluation Form and the documents it lists are attached."))
    return _finalize(Letter(kind="application", button="Financial assistance form · ready to sign",
                            to=_billing_office(case), subject="Application for financial assistance",
                            paragraphs=paragraphs,
                            attachments=["Financial Evaluation Form (filled, sign before sending)",
                                         "Documents listed in Table C of the form"]))


def dispute(case: Case, report: Report) -> Letter | None:
    e1 = [f for f in report.findings if f.kind == Kind.E1]
    e2 = [f for f in report.findings if f.kind == Kind.E2]
    if not e1 and not e2:
        return None
    paras = []
    for f in e2:
        first, second = f.evidence[0], f.evidence[1]
        bill = case.doc(first.doc_id)
        paras.append(Paragraph(
            f"My statement for account {_account(case, bill)} lists the same service twice: lines {first.line_no} "
            f"and {second.line_no}, code {f.math['code']}, on {_date(date.fromisoformat(f.math['service_date']))}, "
            f"{_money(Decimal(f.math['charges']))} each. Please remove the duplicate"
            + (f" and my {_money(f.amount)} share of it, as my insurance statement shows." if f.amount else " and the charge to me for it.")))
    for f in e1:
        bill_ev = f.evidence[0]
        bill = case.doc(bill_ev.doc_id)
        line = next(ln for ln in bill.lines if ln.line_no == bill_ev.line_no)
        code = f" (code {line.code})" if line.code else ""
        claim = f" for claim {line.claim_ref}" if line.claim_ref else ""
        paras.append(Paragraph(
            f"Line {line.line_no} of my statement for account {_account(case, bill)} asks "
            f"{_money(line.patient_responsibility)} for {line.description or 'a service'}{code} on "
            f"{_date(line.service_date)}. My insurance Explanation of Benefits{claim} shows my patient "
            f"responsibility for it as {_money(Decimal(f.math['eob_patient_responsibility']))}, a difference of "
            f"{_money(Decimal(f.math['difference']))}."))
    ask = ("Please correct the balance to match my insurance, or explain the difference in writing." if e1
           else "Please correct the balance, or explain it in writing.")
    from billradar.checks import is_hospital_bill
    first_bill = case.doc((e2 or e1)[0].evidence[0].doc_id)
    if is_hospital_bill(case, first_bill) or not first_bill.provider:  # the statute binds hospitals, not a doctors' group
        paras.append(Paragraph(ask + " Florida law requires the hospital to give a first response to a billing dispute "
                               "within 7 business days (Florida Statutes 395.301(6)).", ["fl-395-301#grievance-7-days"]))
    else:
        paras.append(Paragraph(ask + " Please answer in writing within 30 days."))
    from billradar.callprep import provider_name
    to = (_billing_office(case) if is_hospital_bill(case, first_bill) or not first_bill.provider
          else f"Billing office, {provider_name(first_bill.provider)}")  # the office that sent the bill, not always the hospital
    subject = ("Billing dispute: the same service billed twice" if e2 and not e1
               else "Billing dispute: amount does not match my insurance")
    return _finalize(Letter(kind="dispute", button="Letter: fix the amount from your insurance statement",
                            to=to, subject=subject,
                            paragraphs=paras, attachments=["Copy of the Explanation of Benefits"]))


def all_letters(case: Case, report: Report) -> list[Letter]:
    letters = [itemized_bill(case), apply_discount(case, report), dispute(case, report), application(case, report)]
    return [x for x in letters if x is not None]


def signature(case: Case) -> list[str]:
    loc = case.profile.local
    city = ", ".join(x for x in (loc.city, " ".join(x for x in (loc.state, loc.zip) if x)) if x)
    return [x for x in (_mother(case), loc.street, city, loc.phone) if x]


# --- insurance appeal ----------------------------------------------------------------------

def _plan_quote(plan: plans.Plan, fragment_id: str) -> tuple[str, int]:
    frag = plan.fragment(fragment_id)
    return frag.quote, frag.page


def appeal(case: Case, plan: plans.Plan, denial: appeals.Denial) -> Letter | None:
    """An appeal of one denied claim, quoting the family's plan document for every point.
    A ground whose quote is not on its page is dropped; with no ground left there is no letter."""
    shows_birth = any(p.role == Role.NEWBORN for p in case.profile.people) or "birth" in case.profile.context
    grounds = [g for code in denial.codes for g in plan.grounds.get(code, [])
               if g.needs != "birth" or shows_birth]
    grounds = list({g.fragment: g for g in grounds}.values())
    if not grounds:
        return None
    patient = appeals.person_name(case, denial.patient, "the patient")
    when = _date(denial.service_from) + (f" to {_date(denial.service_to)}"
                                         if denial.service_to and denial.service_to != denial.service_from else "")
    claim = f"claim {denial.claim_ref}" if denial.claim_ref else "the claim"
    what = denial.description or "hospital care"
    paras: list[Paragraph] = []
    for code in denial.codes:
        frag = plan.code_fragment(code)
        meaning = frag.quote.removeprefix(code).strip()
        paras.append(Paragraph(
            f"I am appealing the denial of {claim} for {patient} ({what}, {when}). Your Explanation of Benefits"
            f"{' dated ' + _date(denial.denial_date) if denial.denial_date else ''} denies it with remark code "
            f"{code}: “{meaning}”", [frag.ref]))
    for g in grounds:
        quote, page = _plan_quote(plan, g.fragment)
        paras.append(Paragraph(f"{g.say} My plan document says (page {page}): “{quote}”",
                               [plan.ref(g.fragment)]))
    if denial.patient_responsibility:
        paras.append(Paragraph(
            f"Your Explanation of Benefits leaves {_money(denial.patient_responsibility)} for me to pay on this "
            "claim. Please review the claim and process it as a covered service under my plan."))
    dl = appeals.deadline(plan, denial)
    if dl:
        paras.append(Paragraph(
            f"I am sending this appeal before {_date(dl.day)}, within the time the plan allows after a denial. "
            "Please also send me free copies of all documents, records and other information relevant to my claim.",
            dl.refs + [plan.ref("copies-free")]))
    if plan.plan_type == "employer":
        paras.append(Paragraph(
            "Federal rules for employer health plans give at least 180 days to appeal a denied claim "
            "(29 CFR 2560.503-1(h)(3)(i)).", [appeals.FEDERAL_APPEAL_REF]))
    paras.append(Paragraph("Please send your decision in writing to the address below. Thank you."))
    member = appeals.token_value(case, denial.member_ref, "")
    subject = f"Appeal of denied {claim}" + (f", member ID {member}" if member else "")
    return _finalize(Letter(
        kind=appeals.letter_kind(denial),
        button=f"Appeal to {plan.insurer}", to=", ".join(plan.appeals_to), subject=subject,
        paragraphs=paras,
        attachments=[f"Explanation of Benefits (sheet {denial.eob_id})", "Itemized hospital bill",
                     "Member appeal form (filled, sign before sending)"]))


def appeal_letters(case: Case) -> list[Letter]:
    out = []
    for d in appeals.denials(case):
        letter = appeal(case, plans.load(d.plan_id), d)
        if letter:
            out.append(letter)
    return out


# --- for the screens: where each quote comes from, and the letter as plain text ------------

def paged(ref: str) -> bool:
    """Statutes and regulations read from a web page have no page numbers worth printing."""
    from billradar.policies import source
    pid = ref.split("#", 1)[0]
    return plans.is_ref(ref) or pid not in source.SOURCES or source.SOURCES[pid].kind == "pdf"


def source_of(ref: str) -> dict:
    """Title, page, quote and link of one source: a hospital policy, a law, or the family's plan document."""
    if plans.is_ref(ref):
        frag = plans.resolve(ref)
        return {"ref": ref, "title": plans.load(frag.plan_id).title, "page": frag.page, "quote": frag.quote, "url": ""}
    pid, fid = ref.split("#", 1)
    pol = policy.load(pid)
    frag = pol.fragment(fid)
    return {"ref": ref, "title": policy_name(pol), "page": frag.page, "quote": frag.quote, "url": pol.url}


def sources(letter: Letter) -> list[dict]:
    """Every source a letter leans on, in order, each once."""
    return [source_of(ref) for ref in dict.fromkeys(letter.refs)]


def greeting(letter: Letter) -> str:
    """Dear <the office the letter is addressed to>, not always the hospital's."""
    office = letter.to.split(",")[0].strip() or "Billing office"
    return f"Dear {office} team," if office.startswith("Patient Financial") else f"Dear {letter.to.split(', ', 1)[-1]} billing team,"


def plain_text(letter: Letter, case: Case) -> str:
    """What "Copy" puts on the clipboard and "Save" writes to a .txt file."""
    parts = [_date(case.today), "", letter.to, "", f"Subject: {letter.subject}", "", greeting(letter), ""]
    parts += [p.text + "\n" for p in letter.paragraphs]
    if letter.attachments:
        parts += ["Attached: " + "; ".join(letter.attachments), ""]
    parts += ["Sincerely,", ""] + signature(case)
    if case.demo:
        parts = ["SAMPLE - FICTIONAL DATA", ""] + parts
    return "\n".join(parts).rstrip() + "\n"
