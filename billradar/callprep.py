"""Call cheat sheet: what to say first, which numbers to have ready, and what to answer when the
billing office or the insurer pushes back.

Built in code from the case, the findings and the quoted rules. Every answer that leans on a rule
carries its quote and page; an answer whose quote is not verified is dropped and reported, the
same as a letter paragraph. Numbers come from the papers, never from the model.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from billradar import appeal as appeals
from billradar import plans, policy, writer
from billradar.case import Case, Role
from billradar.checks import Kind, Report, hospital_bills


@dataclass
class Number:
    label: str
    value: str


@dataclass
class Objection:
    they_say: str
    answer: str
    refs: list[str] = field(default_factory=list)


@dataclass
class CallSheet:
    to: str  # "hospital" or "insurer"
    who: str  # the name printed on the papers
    title: str
    say_first: str
    numbers: list[Number]
    objections: list[Objection]
    ask_for: list[str]
    tip: str
    dropped: list[str] = field(default_factory=list)

    @property
    def refs(self) -> list[str]:
        return [r for o in self.objections for r in o.refs]


ASK_FOR = ["The name of the person you speak with", "A reference number for the call",
           "What happens next, and by when, in writing"]
TIP = "Be polite, take notes, and confirm the next steps before you end the call."


def _d(d: date | None) -> str:
    return f"{d:%m/%d/%Y}" if d else ""


def _span(a: date | None, b: date | None) -> str:
    return _d(a) + (f" - {_d(b)}" if b and b != a else "")


def _number_like(value: str) -> bool:
    """A number a person can read out: 5+ letters and digits, at least 4 of them digits. A token the model
    copied badly ('ICIAIM 81') is not one."""
    v = "".join(ch for ch in value if ch.isalnum())
    return len(v) >= 5 and sum(ch.isdigit() for ch in v) >= 4


def _names(case: Case, tokens: list[str]) -> str:
    seen = list(dict.fromkeys(appeals.norm_token(t) for t in tokens if t))
    names = [n for n in (appeals.person_name(case, t, "") for t in seen) if n]  # a token with no name is left out
    return " and ".join(names) if names else "my family"


def _quote_answer(lead: str, ref: str) -> str:
    src = writer.source_of(ref)
    page = f" (page {src['page']})" if writer.paged(ref) else ""
    return f"{lead}{page}: “{src['quote']}”"


def _keep(sheet: CallSheet, items: list[Objection]) -> None:
    for o in items:
        bad = [r for r in o.refs if not writer.verified(r)]
        if bad:
            sheet.dropped.append(f"{o.they_say} (unverified {', '.join(bad)})")
        else:
            sheet.objections.append(o)


def _frag(pol: policy.Policy, fragment_id: str) -> str | None:
    return f"{pol.policy_id}#{fragment_id}" if fragment_id in pol.fragments else None


def provider_name(raw: str) -> str:
    """The sender as a person would say it: no file prefix ("300_"), no ward code ("(IP) 300"), no all caps."""
    name = re.sub(r"^\d+_", "", (raw or "").strip())
    name = re.sub(r"\s*\((?:IP|OP)\)\s*\d*$", "", name).strip()
    return name.title() if name.isupper() else name


def paper_name(doc_id: str) -> str:
    return f"sheet {doc_id[1:]}" if doc_id.startswith("S") else f"file {doc_id[1:]}"


def hospital(case: Case, report: Report) -> CallSheet:
    bills = hospital_bills(case) or case.bills()
    who = case.letterhead or next((b.provider for b in bills if b.provider), "the hospital")
    starts = [b.service_from for b in bills if b.service_from]
    ends = [b.service_to or b.service_from for b in bills if b.service_to or b.service_from]
    span = _span(min(starts), max(ends)) if starts else ""
    names = _names(case, [b.patient for b in bills])
    say = (f"Hi, I'm calling about the bills for {names}" + (f" from {span}" if span else "")
           + ". I want to make sure every discount and all financial assistance I qualify for are applied.")
    # every number says whose it is and where it was read, so eight claim numbers are not eight equal cards
    numbers = [Number(f"Account at {provider_name(b.provider) or who} · {appeals.person_name(case, b.patient, 'patient')} · {paper_name(b.doc_id)}",
                      appeals.token_value(case, b.account_ref, "")) for b in bills if b.account_ref]
    claims: dict[str, list] = {}
    for e in case.eobs():
        for ln in e.lines:
            if ln.claim_ref:
                claims.setdefault(ln.claim_ref, []).append((e, ln))
    for ref, rows in claims.items():  # a covered number with no value kept here: no card
        e = rows[0][0]
        codes = list(dict.fromkeys(ln.code for _, ln in rows if ln.code))
        codes = ", ".join(codes[:4]) + (", …" if len(codes) > 4 else "")
        numbers.append(Number(f"Insurance claim · {appeals.person_name(case, e.patient, 'patient')} · {paper_name(e.doc_id)}"
                              + (f" · codes {codes}" if codes else ""), appeals.token_value(case, ref, "")))
    numbers = [n for n in {(n.label, n.value): n for n in numbers if _number_like(n.value)}.values()]
    sheet = CallSheet("hospital", who, f"Before you call {who}", say, numbers, [], list(ASK_FOR), TIP)

    pol = policy.load(case.policy_id)
    kinds = {f.kind for f in report.findings}
    items: list[Objection] = []
    if kinds & {Kind.F1, Kind.F3} and case.profile.insured and (ref := _frag(pol, "insured-balances")):
        items.append(Objection("The discount is only for uninsured patients.",
                               _quote_answer("Your Financial Assistance Policy says", ref), [ref]))
    if Kind.F3 in kinds and (ref := _frag(pol, "prior-accounts")):
        items.append(Objection("The baby's account is separate from mine.",
                               _quote_answer("Your policy says", ref), [ref]))
    f2 = next((f for f in report.findings if f.kind == Kind.F2), None)
    if f2 and f2.deadline:
        # quote the rule that gives this very date: the hospital's own window or the federal one
        used = next((k for k, v in f2.math.items() if v == f2.deadline.isoformat()), "")
        ref = ("federal-501r#application-period" if used.startswith("federal")
               else next((r for r in f2.policy_refs if r.startswith(pol.policy_id)), "federal-501r#application-period"))
        lead = "The federal rule for nonprofit hospitals says" if ref.startswith("federal") else "Your policy says"
        items.append(Objection("It's too late to apply for financial assistance.",
                               f"I can apply until {f2.deadline:%B %-d, %Y}. " + _quote_answer(lead, ref), [ref]))
    for f in report.findings:
        if f.kind == Kind.E1 and len(f.evidence) >= 2 and "eob_patient_responsibility" in f.math:
            bill, eob = f.evidence[0], f.evidence[1]
            items.append(Objection(
                "That is what your insurance says you owe.",
                f"My insurance statement (sheet {eob.doc_id}, line {eob.line_no}) says I owe "
                f"${Decimal(f.math['eob_patient_responsibility']):,.2f} for this service, but your bill "
                f"(sheet {bill.doc_id}, line {bill.line_no}) asks ${Decimal(f.math['bill_patient_responsibility']):,.2f}."))
        elif f.kind == Kind.E2 and len(f.evidence) >= 2 and "code" in f.math and _hospitals(case, f):
            a, b = f.evidence[0], f.evidence[1]
            items.append(Objection(
                "Those are two different services.",
                f"Lines {a.line_no} and {b.line_no} on sheet {a.doc_id} have the same code {f.math['code']}, "
                f"the same date and the same price. Please check whether it was given twice."))
    items.append(Objection("We can't send you an itemized bill right now.",
                           _quote_answer("Florida law says", "fl-395-301#itemized-7-days"),
                           ["fl-395-301#itemized-on-request", "fl-395-301#itemized-7-days"]))
    if any(f.kind in (Kind.E1, Kind.E2) and _hospitals(case, f) for f in report.findings):
        items.append(Objection("There is nothing to dispute.",
                               "I would like to file a billing dispute in writing. "
                               + _quote_answer("As I understand it, Florida Statutes 395.301 describe the hospital's "
                                               "grievance process", "fl-395-301#grievance-7-days"),
                               ["fl-395-301#grievance-7-days"]))
    _keep(sheet, items)
    return sheet


def _hospitals(case: Case, f) -> bool:
    """The bill a finding is about comes from the hospital being called, not a doctors' office (S10 is Meridian's)."""
    from billradar.checks import is_hospital_bill
    try:
        return is_hospital_bill(case, case.doc(f.evidence[0].doc_id))
    except (StopIteration, IndexError):
        return False


def insurer(case: Case) -> CallSheet | None:
    denied = appeals.denials(case)
    if not denied:
        return None
    plan = plans.load(denied[0].plan_id)
    first = denied[0]
    claims = ", ".join(v for v in (appeals.token_value(case, d.claim_ref, "") for d in denied) if v) or "my claim"
    names = _names(case, [d.patient for d in denied])
    say = (f"Hi, I'm calling about {claims} for {names}, denied with code {', '.join(first.codes)}. "
           "I want to understand the denial and appeal it.")
    numbers = []
    member = appeals.token_value(case, first.member_ref, "")
    if member:
        numbers.append(Number(f"Member ID ({plan.insurer})", member))
    numbers += [Number("Claim number", v) for v in (appeals.token_value(case, d.claim_ref, "") for d in denied)
                if _number_like(v)]
    sheet = CallSheet("insurer", plan.insurer, f"Before you call {plan.insurer}", say, numbers, [],
                      list(ASK_FOR) + ["Ask them to note on the claim that you are appealing"], TIP)
    shows_birth = any(p.role == Role.NEWBORN for p in case.profile.people) or "birth" in case.profile.context
    items: list[Objection] = []
    if "P31" in {c for d in denied for c in d.codes}:
        if shows_birth and "no-auth-for-childbirth-stay" in plan.fragments:
            ref = plan.ref("no-auth-for-childbirth-stay")
            items.append(Objection("The stay needed prior authorization.",
                                   _quote_answer("My plan document says", ref), [ref]))
        if "emergency-auth" in plan.fragments:
            ref = plan.ref("emergency-auth")
            items.append(Objection("Without prior authorization we can't pay it.",
                                   _quote_answer("My plan document says", ref), [ref]))
    dl = appeals.deadline(plan, first)
    if dl:
        items.append(Objection("It's too late to appeal.",
                               f"I can appeal until {dl.day:%B %-d, %Y}. "
                               + _quote_answer("My plan document says", dl.refs[0]), dl.refs))
    if "appeal-address" in plan.fragments:
        ref = plan.ref("appeal-address")
        items.append(Objection("You have to send that in writing.",
                               "I will. " + _quote_answer("Your plan document gives the address", ref), [ref]))
    if "copies-free" in plan.fragments:
        ref = plan.ref("copies-free")
        items.append(Objection("We can't share the claim file.", _quote_answer("My plan document says", ref), [ref]))
    _keep(sheet, items)
    return sheet


def build(case: Case, report: Report, to: str = "hospital") -> CallSheet | None:
    if to == "insurer":
        return insurer(case)
    if to == "hospital":
        return hospital(case, report)
    raise ValueError(f"unknown call target {to!r}")


def all_sheets(case: Case, report: Report) -> list[CallSheet]:
    return [s for s in (hospital(case, report), insurer(case)) if s]
