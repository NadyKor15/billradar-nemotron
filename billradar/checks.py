"""Deterministic checks over a family case. Code decides money and dates; no model here.

Classes:
  E1  a bill asks for more than the insurance statement (EOB) says the patient owes
  F1  the family qualifies under the hospital's policy, but no discount is on the bills
  F3  the discount is on one bill of the birth (mom) but not on another (baby)
  F2  the last day to apply, counted from the first bill after discharge
  NM  "no money here, and why" — the honest answers next to the findings
  N1  No Surprises Act: an out-of-network specialist or emergency bill asks more than the in-network share
  A1  the last day to appeal a denied claim (29 CFR 2560.503-1, at least 180 days)
  I1  a hospital statement without its itemized bill: ask for it (Florida: within 7 days)
  C1  no insurance: a line billed at the hospital's gross charge, while it publishes a lower cash price
  C2  insured: the plan allowed more than the rate the hospital publishes for that plan (e.g. a birth's case rate)

Severity: "high" findings are confirmed and carry money; "borderline" ones go to `worth_asking`,
shown without a sum, next to the letter that asks for what is missing.

Order matters: E1 runs first, and F1/F3 discount the bill *after* the overcharge
is removed, so the same dollar is never counted twice.

A policy finding is shown only if every quote it cites is found in the policy's
own text (policy.verified). Otherwise it goes to `held_back` with the reason.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from billradar import fpl, policy
from billradar.case import Case, Document, DocKind, Line, Role

CENT = Decimal("0.01")
EPISODE_GAP_DAYS = 7


def _q(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(d)


class Kind(StrEnum):
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"
    B1 = "B1"
    B2 = "B2"  # a bill for the baby that no insurance statement covers
    P1 = "P1"
    F1 = "F1"
    F2 = "F2"
    F3 = "F3"
    N1 = "N1"
    A1 = "A1"
    I1 = "I1"
    C1 = "C1"
    C2 = "C2"
    NM = "NM"


class Evidence(BaseModel):
    doc_id: str
    line_no: int | None = None
    note: str = ""


class Finding(BaseModel):
    kind: Kind
    title: str
    detail: str
    amount: Decimal | None = None  # what may come off; None for dates and no-money notes
    deadline: date | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    math: dict[str, str] = Field(default_factory=dict)  # every number the result came from
    severity: Literal["high", "borderline"] | None = None  # borderline -> "worth asking", shown without a sum
    confidence: float | None = None  # from the verifier, 0..1; None until it has looked
    laws: list[str] = Field(default_factory=list)  # protection ids in policies, "<policy_id>#<fragment>"
    explain: str | None = None  # the finding in plain words, for the family


class Report(BaseModel):
    case_id: str
    policy_id: str
    fpl_pct: Decimal
    findings: list[Finding]
    no_money: list[Finding]
    held_back: list[Finding]  # cited quotes not verified -> not shown to the family
    total_may_come_off: Decimal
    apply_by: date | None
    worth_asking: list[Finding] = Field(default_factory=list)  # borderline: no sum, a question to ask


def episodes(bills: list[Document]) -> list[list[Document]]:
    """Bills whose service starts within a week of each other belong to one stay (a birth: mom + baby)."""
    dated = sorted((b for b in bills if b.service_from), key=lambda b: b.service_from)
    groups: list[list[Document]] = []
    for b in dated:
        if groups and (b.service_from - groups[-1][0].service_from).days <= EPISODE_GAP_DAYS:
            groups[-1].append(b)
        else:
            groups.append([b])
    undated = [b for b in bills if not b.service_from]
    if undated:
        groups.append(undated)
    return groups


GENERIC_NAME_WORDS = {"hospital", "health", "healthcare", "system", "systems", "regional", "medical", "center",
                      "centre", "the", "of", "inc", "llc", "pa", "and", "care", "group", "services", "south",
                      "north", "memorial", "baptist", "general", "community"}


def _name_words(name: str) -> set[str]:
    import re
    return {w for w in re.findall(r"[a-z]+", name.lower()) if len(w) > 2 and w not in GENERIC_NAME_WORDS}


def _is_facility_code(code: str) -> bool:
    import re
    return bool(re.fullmatch(r"0\d{3}", code.strip()))


def is_hospital_bill(case: Case, doc: Document) -> bool:
    """Is this bill from the hospital whose policy we check (not from a doctors' group)?
    The hospital's name printed on its bills (`letterhead`) and the provider share a distinctive word."""
    if not case.letterhead or not doc.provider:
        return True
    return bool(_name_words(case.letterhead) & _name_words(doc.provider))


def one_office_per_account(docs: list[Document], letterhead: str) -> list[Document]:
    """Papers of one account come from one office. A doctor's statement often prints the hospital as the place of
    service ("Seaside Regional Hospital (IP)") while its coupon says "make check payable to Women First OB/GYN":
    when the papers of an account name the hospital and another office, the account is that office's."""
    if not letterhead:
        return docs
    home = _name_words(letterhead)
    office: dict[str, str] = {}
    for d in docs:
        acct = d.account_ref.strip()
        if acct and d.provider and not (home & _name_words(d.provider)):
            office.setdefault(acct, d.provider)
    # a paper of that account read with the hospital's name or with no name at all is the office's too
    return [d.model_copy(update={"provider": office[d.account_ref.strip()]})
            if d.account_ref.strip() in office and (not d.provider or home & _name_words(d.provider)) else d for d in docs]


def hospital_bills(case: Case) -> list[Document]:
    return [b for b in case.bills() if is_hospital_bill(case, b)]


def one_per_account(bills: list[Document]) -> list[Document]:
    """A statement and an itemized bill of the same account are one debt, not two.
    Keep one paper per account: the statement over the itemized bill, then the newest."""
    keep: dict[str, Document] = {}
    loose = []
    for b in bills:
        acct = b.account_ref.strip()
        if not acct:
            loose.append(b)
            continue
        cur = keep.get(acct)
        rank = (b.kind == DocKind.HOSPITAL_STATEMENT, b.statement_date or date.min)
        if cur is None or rank > (cur.kind == DocKind.HOSPITAL_STATEMENT, cur.statement_date or date.min):
            keep[acct] = b
    kept = [b for b in bills if b in loose or keep.get(b.account_ref.strip()) is b]
    # the statement may carry a guarantor number and the itemized bill the account number: same patient and same
    # balance is the same debt too, so the itemized copy goes
    # balance is the same debt too: one paper per (patient, balance), the statement over the itemized bill, the one
    # that prints a coupon amount, then the newest
    best: dict[tuple, Document] = {}
    for b in kept:
        if not b.patient_balance:
            continue
        k = (_who(b), b.patient_balance)
        rank = (b.kind == DocKind.HOSPITAL_STATEMENT, b.amount_due_coupon is not None, b.statement_date or date.min)
        cur = best.get(k)
        if cur is None or rank > (cur.kind == DocKind.HOSPITAL_STATEMENT, cur.amount_due_coupon is not None,
                                  cur.statement_date or date.min):
            best[k] = b
    return [b for b in kept if not b.patient_balance or best[(_who(b), b.patient_balance)] is b]


def _who(doc: Document) -> str:
    """The patient token as one key: a reader may print PATIENT_1 as "PATIENT 1"."""
    return re.sub(r"[\s_]+", "_", (doc.patient or "").strip().upper())


def has_discount(doc: Document) -> bool:
    if doc.fa_discount and doc.fa_discount != 0:
        return True
    return any(line.fa_discount and line.fa_discount != 0 for line in doc.lines)


# --- E1: bill vs insurance statement -------------------------------------------------------

def _eob_line_for(case: Case, bill: Document, line: Line) -> tuple[Document, Line] | None:
    for eob in case.eobs():
        if eob.patient and bill.patient and eob.patient != bill.patient:
            continue
        for el in eob.lines:
            if line.claim_ref and el.claim_ref and line.claim_ref == el.claim_ref and (
                not line.code or not el.code or line.code == el.code
            ):
                return eob, el
            if not line.claim_ref and line.service_date and line.code and (
                el.service_date == line.service_date and el.code == line.code
            ):
                return eob, el
    return None


def check_e1(case: Case) -> tuple[list[Finding], list[Finding], dict[str, Decimal]]:
    findings, matches, overcharge_by_bill = [], [], {}
    for bill in case.bills():
        for line in bill.lines:
            if line.patient_responsibility is None:
                continue
            hit = _eob_line_for(case, bill, line)
            if not hit:
                continue
            eob, el = hit
            if el.patient_responsibility is None:
                continue
            diff = _q(line.patient_responsibility - el.patient_responsibility)
            ev = [Evidence(doc_id=bill.doc_id, line_no=line.line_no), Evidence(doc_id=eob.doc_id, line_no=el.line_no)]
            math = {
                "bill_patient_responsibility": str(line.patient_responsibility),
                "eob_patient_responsibility": str(el.patient_responsibility),
                "difference": str(diff),
            }
            if diff > 0:
                # On a bill that already carries a discount, only the undiscounted share of the
                # overcharge actually reaches the family: amount = diff * balance / (balance + discount).
                effective = diff
                if has_discount(bill) and bill.fa_discount and bill.patient_balance is not None:
                    before = bill.patient_balance + abs(bill.fa_discount)
                    effective = _q(diff * bill.patient_balance / before) if before else diff
                    math["share_left_after_discount"] = f"{bill.patient_balance}/{before}"
                math["counts_toward_total"] = str(effective)
                overcharge_by_bill[bill.doc_id] = overcharge_by_bill.get(bill.doc_id, Decimal(0)) + effective
                findings.append(Finding(
                    kind=Kind.E1,
                    title=f"Bill asks ${diff:,.2f} more than your insurance says",
                    detail=(f"Line {line.line_no} on sheet {bill.doc_id} asks ${line.patient_responsibility:,.2f}. "
                            f"Your insurance statement ({eob.doc_id}, line {el.line_no}) says you owe "
                            f"${el.patient_responsibility:,.2f} for the same service."
                            + (f" Your discount already covers part of it, so ${effective:,.2f} of it is yours."
                               if effective != diff else "")),
                    amount=effective, evidence=ev, math=math))
            else:
                matches.append(Finding(
                    kind=Kind.NM,
                    title=f"Sheet {bill.doc_id[1:]} line {line.line_no} matches your insurance statement",
                    detail=f"Both say ${line.patient_responsibility:,.2f}. Nothing to dispute here.",
                    evidence=ev, math=math))
    return findings, matches, overcharge_by_bill


# --- F1 / F3: financial assistance ---------------------------------------------------------

def check_assistance(case: Case, pol: policy.Policy, fpl_pct: Decimal,
                     overcharge: dict[str, Decimal]) -> tuple[list[Finding], list[Finding]]:
    prof = case.profile
    bills = one_per_account([b for b in hospital_bills(case) if (b.patient_balance or 0) > 0])
    others = [b for b in case.bills() if not is_hospital_bill(case, b) and (b.patient_balance or 0) > 0]
    notes: list[Finding] = []
    scope_ref = next((f"{pol.policy_id}#{fid}" for fid in ("facilities-only", "facility-only") if fid in pol.fragments), None)
    if others:
        names = sorted({b.provider for b in others})
        notes.append(Finding(
            kind=Kind.NM,
            title="Doctors' bills are outside the hospital's assistance policy",
            detail=(f"{', '.join(names)} bill separately. This policy covers the hospital and its own doctors only; "
                    "ask each office whether it has its own help program."),
            evidence=[Evidence(doc_id=b.doc_id) for b in others],
            policy_refs=[scope_ref] if scope_ref else []))
    if not bills:
        return [], notes

    if prof.insured and not pol.covers_insured_balances:
        return [], [Finding(
            kind=Kind.NM,
            title="This hospital's policy does not cover what insurance leaves you to pay",
            detail=(f"{pol.hospital}: financial assistance is for uninsured patients. Your copays, deductible "
                    "and coinsurance are not covered by it. Check the bill against your insurance statement instead."),
            policy_refs=[f"{pol.policy_id}#uninsured-only"])] + notes

    tier = pol.tier_for(fpl_pct)
    if tier is None:
        top = pol.tiers[-1].max_fpl_pct if pol.tiers else 0
        return [], [Finding(
            kind=Kind.NM,
            title="Your income is above this policy's limit",
            detail=(f"Your household income is {fpl_pct}% of the federal poverty guideline. "
                    f"This policy helps households up to {top}%."),
            policy_refs=[f"{pol.policy_id}#{pol.tiers[-1].fragment}"] if pol.tiers else [],
            math={"fpl_pct": str(fpl_pct)})] + notes

    refs = [f"{pol.policy_id}#{tier.fragment}"]
    if prof.insured and pol.covers_insured_balances and "insured-balances" in pol.fragments:
        refs.append(f"{pol.policy_id}#insured-balances")
    pct = Decimal(tier.discount_pct)
    caveat = ""
    if tier.discount_pct == 100 and pol.liquid_assets_limit_for_free_care is not None:
        refs.append(f"{pol.policy_id}#liquid-assets")
        if prof.liquid_assets is not None and prof.liquid_assets >= pol.liquid_assets_limit_for_free_care:
            return [], [Finding(
                kind=Kind.NM,
                title="Free care needs savings under the policy's limit",
                detail=(f"Your income fits the 100% level, but this policy also asks for savings under "
                        f"${pol.liquid_assets_limit_for_free_care:,.0f}. Ask the hospital how it treats your case."),
                policy_refs=[f"{pol.policy_id}#liquid-assets"])] + notes
        if prof.liquid_assets is None:
            caveat = f" This level also needs savings under ${pol.liquid_assets_limit_for_free_care:,.0f}."

    total_balance = sum((b.patient_balance - overcharge.get(b.doc_id, 0) for b in bills), Decimal(0))
    if pol.min_request_total is not None and total_balance <= pol.min_request_total:
        return [], [Finding(
            kind=Kind.NM,
            title=f"Balances are under the ${pol.min_request_total:,.0f} minimum",
            detail=(f"All your balances together are ${total_balance:,.2f}. This policy considers requests "
                    f"of more than ${pol.min_request_total:,.0f}."),
            policy_refs=[f"{pol.policy_id}#min-request"], math={"total_balance": str(total_balance)})] + notes

    findings: list[Finding] = []
    for group in episodes(bills):
        with_discount = [b for b in group if has_discount(b)]
        without = [b for b in group if not has_discount(b)]
        if not without:
            continue
        for b in without:
            base = b.patient_balance - overcharge.get(b.doc_id, Decimal(0))
            saving = _q(base * pct / 100)
            math = {"balance": str(b.patient_balance), "minus_overcharge": str(overcharge.get(b.doc_id, 0)),
                    "fpl_pct": str(fpl_pct), "discount_pct": str(pct), "saving": str(saving)}
            role = case.profile.role_of(b.patient)
            if with_discount:
                other = with_discount[0]
                who = "the baby's" if role == Role.NEWBORN else "this"
                findings.append(Finding(
                    kind=Kind.F3,
                    title=f"Discount on sheet {other.doc_id}, not on {who} bill",
                    detail=(f"Same hospital stay. Financial assistance is on sheet {other.doc_id}, "
                            f"but sheet {b.doc_id} still asks ${base:,.2f}. "
                            f"At your income level the policy gives {pct}% off.{caveat}"),
                    amount=saving, evidence=[Evidence(doc_id=other.doc_id, note="discount applied"),
                                             Evidence(doc_id=b.doc_id, note="no discount")],
                    policy_refs=refs, math=math))
            else:
                findings.append(Finding(
                    kind=Kind.F1,
                    title="You likely qualify for financial assistance",
                    detail=(f"Household of {prof.household_size}, income ${prof.annual_income:,.0f}: "
                            f"{fpl_pct}% of the poverty guideline. The policy gives {pct}% off at this level. "
                            f"Sheet {b.doc_id} has no discount yet.{caveat}"),
                    amount=saving, evidence=[Evidence(doc_id=b.doc_id, note="no discount")],
                    policy_refs=refs, math=math))

    if pol.income_cap_pct and findings and "income-cap" in pol.fragments:
        cap = _q(prof.annual_income * pol.income_cap_pct / 100)
        owed_after = total_balance - sum((f.amount or Decimal(0) for f in findings), Decimal(0))
        if owed_after > cap:
            extra = _q(owed_after - cap)
            findings.append(Finding(
                kind=Kind.F1,
                title=f"Capped at {pol.income_cap_pct}% of your yearly income",
                detail=(f"After the discount you would still owe ${owed_after:,.2f}. The policy caps it at "
                        f"{pol.income_cap_pct}% of household income: ${cap:,.2f}."),
                amount=extra, policy_refs=[f"{pol.policy_id}#income-cap"],
                math={"owed_after_discount": str(owed_after), "cap": str(cap)}))
    return findings, notes


# --- F2: application window ----------------------------------------------------------------

def after_stay_bills(case: Case) -> list[Document]:
    """Hospital bills for the stay itself: a statement for care that ended before the latest stay began (prenatal
    visits, say) is not the first bill after discharge, even when it came later."""
    bills = hospital_bills(case)
    starts = [b.service_from for b in bills if b.service_from]
    stay = max(starts, default=None)
    return [b for b in bills if not (stay and (b.service_to or b.service_from) and (b.service_to or b.service_from) < stay)]


def check_window(case: Case, pol: policy.Policy) -> Finding | None:
    bills = after_stay_bills(case)  # the application window runs from the hospital's first bill, not a doctor's
    if not bills:
        return None
    flagged = [b for b in bills if b.first_after_discharge and b.statement_date]
    first = min(flagged or [b for b in bills if b.statement_date], key=lambda b: b.statement_date, default=None)
    if first is None:
        return None
    w = pol.window
    candidates: dict[str, date] = {}
    refs = []
    if "after_first_statement_days" in w:
        candidates["policy: days after first bill"] = first.statement_date + timedelta(days=w["after_first_statement_days"])
    if "after_service_days" in w:
        last_service = max((b.service_to or b.service_from for b in bills if b.service_to or b.service_from), default=None)
        if last_service:
            candidates["policy: one year after service"] = last_service + timedelta(days=w["after_service_days"])
    if "after_last_insurance_payment_months" in w:
        paid = [d.insurance_payment_date for d in case.documents if d.insurance_payment_date]
        # Most EOBs print only their own date. For the hospital's claims (facility revenue codes 0xxx),
        # that date stands for the payment. A doctor's EOB is never used: it could push the date too late.
        paid += [d.statement_date for d in case.eobs() if not d.insurance_payment_date and d.statement_date
                 and any(_is_facility_code(ln.code) for ln in d.lines)]
        if paid:
            candidates["policy: 12 months after last insurance payment"] = _add_months(
                max(paid), w["after_last_insurance_payment_months"])
    if w:
        refs.append(f"{pol.policy_id}#{w['fragment']}")
    candidates["federal: 240 days after first bill"] = first.statement_date + timedelta(days=240)
    refs.append("federal-501r#application-period")

    deadline = max(candidates.values())
    days_left = (deadline - case.today).days
    detail = (f"First bill after discharge: sheet {first.doc_id}, {first.statement_date:%b %d, %Y}. "
              + ("" if flagged else "No sheet says 'first bill', so we used the earliest hospital bill we have. ")
              + (f"{days_left} days left." if days_left >= 0
                 else "The window has passed; the hospital may still accept a late application."))
    return Finding(
        kind=Kind.F2, title=f"Apply by {deadline:%b %d, %Y}", detail=detail, deadline=deadline,
        evidence=[Evidence(doc_id=first.doc_id, note="first bill after discharge")], policy_refs=refs,
        math={k: v.isoformat() for k, v in candidates.items()} | {"days_left": str(days_left)})


# --- N1 / A1 / I1: protections the papers can show -------------------------------------------

SPECIALTY_WORDS = ("anesthes", "radiolog", "patholog", "neonat", "emergency")
OUT_OF_NETWORK_WORDS = ("out of network", "out-of-network", "non-participating", "nonparticipating", "non-par ")


def _out_of_network(case: Case, doc: Document) -> bool:
    if "out_of_network" in case.profile.context:
        return True
    texts = [ln.description.lower() for ln in doc.lines]
    for eob in case.eobs():
        texts += [ln.description.lower() for ln in eob.lines if ln.claim_ref and ln.claim_ref in _claims(doc)]
    return any(w in t for t in texts for w in OUT_OF_NETWORK_WORDS)


def _claims(doc: Document) -> set[str]:
    return {r for r in [doc.claim_ref, *(ln.claim_ref for ln in doc.lines)] if r}


def nsa_kind_of(case: Case, doc: Document) -> str | None:
    """'nsa-ancillary' for an out-of-network specialist's bill, 'nsa-emergency' for emergency care, else None."""
    if not case.profile.insured or is_hospital_bill(case, doc) and "er_visit" not in case.profile.context:
        return None
    provider = doc.provider.lower()
    if "er_visit" in case.profile.context or "emergency" in provider:
        return "nsa-emergency" if _out_of_network(case, doc) else None
    if any(w in provider for w in SPECIALTY_WORDS) and _out_of_network(case, doc):
        return "nsa-ancillary"
    return None


def check_nsa(case: Case, prior: list[Finding]) -> list[Finding]:
    """An out-of-network specialist or emergency bill. If E1 already caught the overcharge line by line,
    the law is added to that finding (the dollar is counted once). Otherwise the bill's balance is
    compared with what the insurance statement leaves the patient for the same claims."""
    from billradar import laws
    out: list[Finding] = []
    for bill in case.bills():
        which = nsa_kind_of(case, bill)
        if not which or not (bill.patient_balance or 0) > 0:
            continue
        refs = laws.refs_for(which)
        caught = [f for f in prior if f.kind == Kind.E1 and f.evidence and f.evidence[0].doc_id == bill.doc_id]
        for f in caught:
            f.laws = list(dict.fromkeys(f.laws + refs))
        if caught:
            continue
        claims = _claims(bill)
        eob_lines = [(e, ln) for e in case.eobs() for ln in e.lines
                     if ln.claim_ref and ln.claim_ref in claims and ln.patient_responsibility is not None]
        cite = "45 CFR 149.420" if which == "nsa-ancillary" else "45 CFR 149.410"
        if not eob_lines:
            out.append(Finding(
                kind=Kind.N1, severity="borderline",
                title=f"Out-of-network bill from {bill.provider}: ask for in-network rates",
                detail=(f"Sheet {bill.doc_id} asks ${bill.patient_balance:,.2f}. Under the No Surprises Act ({cite}) "
                        "you owe only your in-network share. We found no insurance statement for it yet: ask your "
                        "insurance how much you owe for this claim."),
                evidence=[Evidence(doc_id=bill.doc_id)], laws=refs, policy_refs=refs,
                math={"patient_balance": str(bill.patient_balance)}))
            continue
        share = _q(sum((ln.patient_responsibility for _, ln in eob_lines), Decimal(0)))
        diff = _q(bill.patient_balance - share)
        if diff <= 0:
            continue
        out.append(Finding(
            kind=Kind.N1, severity="high",
            title=f"Out-of-network bill asks ${diff:,.2f} more than your in-network share",
            detail=(f"Sheet {bill.doc_id} from {bill.provider} asks ${bill.patient_balance:,.2f}. Your insurance "
                    f"statement leaves you ${share:,.2f} for the same claims. Under the No Surprises Act ({cite}) "
                    "this provider can bill you only your in-network share."),
            amount=diff, evidence=[Evidence(doc_id=bill.doc_id)] + [
                Evidence(doc_id=e.doc_id, line_no=ln.line_no) for e, ln in eob_lines],
            laws=refs, policy_refs=refs,
            math={"patient_balance": str(bill.patient_balance), "eob_patient_share": str(share),
                  "difference": str(diff)}))
    return out


def check_appeal(case: Case, prior: list[Finding]) -> list[Finding]:
    """A denied claim line that leaves the family owing money. A claim already explained as the baby
    billed under mom's ID (B1) is left to B1: the fix there is a corrected claim, not an appeal."""
    from billradar import laws
    if not case.profile.insured:
        return []
    b1_lines = {(e.doc_id, e.line_no) for f in prior if f.kind == Kind.B1 for e in f.evidence}
    out = []
    for eob in case.eobs():
        b1_claims = {ln.claim_ref or eob.claim_ref for ln in eob.lines if (eob.doc_id, ln.line_no) in b1_lines}
        lines = [ln for ln in eob.lines if laws.denied(ln) and (ln.patient_responsibility or 0) > 0
                 and (ln.claim_ref or eob.claim_ref) not in b1_claims]
        if not lines or not eob.statement_date:
            continue
        deadline = laws.appeal_deadline(eob.statement_date)
        days_left = (deadline - case.today).days
        codes = sorted({c.strip() for ln in lines for c in ln.denial_codes.split(",") if c.strip()})
        out.append(Finding(
            kind=Kind.A1, severity="high", title=f"Appeal the denial by {deadline:%b %d, %Y}",
            detail=(f"Insurance statement {eob.doc_id} ({eob.statement_date:%b %d, %Y}) paid nothing on "
                    f"{len(lines)} line{'s' if len(lines) != 1 else ''} (code {', '.join(codes)}). An employer "
                    f"health plan must give you at least {laws.APPEAL_DAYS} days to appeal. "
                    + (f"{days_left} days left." if days_left >= 0 else "That time has passed; ask the plan anyway.")),
            deadline=deadline, evidence=[Evidence(doc_id=eob.doc_id, line_no=ln.line_no, note="denied")
                                         for ln in lines],
            laws=laws.refs_for("appeal-180-days"), policy_refs=[laws.APPEAL_REF],
            math={"notice_date": eob.statement_date.isoformat(), "days": str(laws.APPEAL_DAYS),
                  "days_left": str(days_left)}))
    return out


def check_itemized(case: Case) -> list[Finding]:
    from billradar import laws
    if laws.hospital(case).get("state") != "FL":
        return []
    itemized = {b.account_ref.strip() for b in case.bills() if b.kind == DocKind.ITEMIZED_BILL}
    same_debt = {(_who(b), b.patient_balance) for b in case.bills() if b.kind == DocKind.ITEMIZED_BILL}
    missing = [b for b in hospital_bills(case) if b.kind == DocKind.HOSPITAL_STATEMENT and (b.patient_balance or 0) > 0
               and not (b.account_ref.strip() and b.account_ref.strip() in itemized)
               and (_who(b), b.patient_balance) not in same_debt]  # its itemized bill is already in the pile
    if not missing or any(b.kind == DocKind.ITEMIZED_BILL and not b.account_ref.strip() for b in case.bills()):
        return []
    refs = laws.refs_for("itemized-bill-7-days")
    return [Finding(
        kind=Kind.I1, severity="borderline", title="Ask for the itemized bill",
        detail=(f"Sheet{'s' if len(missing) > 1 else ''} {', '.join(b.doc_id for b in missing)} "
                f"show{'' if len(missing) > 1 else 's'} a total without every charge. Line-by-line errors can hide "
                "there. A Florida hospital must send the itemized bill within 7 days of your request."),
        evidence=[Evidence(doc_id=b.doc_id, note="no itemized bill") for b in missing],
        laws=refs, policy_refs=refs, math={"letter": "itemized"})]


def check_cash(case: Case) -> tuple[list[Finding], dict[str, Decimal]]:
    """No insurance: each hospital line whose charge is units x the published gross charge, on a bill that
    shows no self-pay discount, should cost the published cash price. A code that is listed but whose charge
    fits no single published price is borderline: ask for the cash price, no sum."""
    from billradar import prices
    index = prices.load(case.policy_id)
    if case.profile.insured or index is None:
        return [], {}
    out, saved = [], {}
    when = _published(index.last_updated_on)
    for bill in hospital_bills(case):
        if has_discount(bill) or (bill.patient_balance or 0) <= 0:
            continue
        for ln in bill.lines:
            if not ln.code or not ln.charges or ln.charges <= 0 or ln.adjustment:
                continue
            hit = prices.match(index, ln.code, ln.charges)
            if hit is None:
                continue
            src = {"mrf_url": index.url, "mrf_updated": index.last_updated_on, "mrf_version": index.version}
            ev = [Evidence(doc_id=bill.doc_id, line_no=ln.line_no)]
            if hit == "ambiguous":
                cash = sorted({p.cash for p in index.candidates(ln.code)})
                out.append(Finding(
                    kind=Kind.C1, severity="borderline", title=f"Ask for the cash price of code {ln.code}",
                    detail=(f"Line {ln.line_no} on sheet {bill.doc_id} charges ${ln.charges:,.2f}. {index.hospital} "
                            f"publishes cash prices for this code from ${cash[0]:,.2f}"
                            + (f" to ${cash[-1]:,.2f}" if len(cash) > 1 else "")
                            + f" (prices as of the hospital's publication date, {when}). Ask which applies."),
                    evidence=ev, math=src | {"code": ln.code, "charge": str(ln.charges)}))
                continue
            price, units = hit
            amount = _q(units * (price.gross - price.cash))
            owed = ln.patient_responsibility if ln.patient_responsibility is not None else ln.charges
            amount = min(amount, owed)
            if amount <= 0:
                continue
            saved[bill.doc_id] = saved.get(bill.doc_id, Decimal(0)) + amount
            out.append(Finding(
                kind=Kind.C1, title=f"Cash price is ${amount:,.2f} lower on line {ln.line_no}",
                detail=(f"Line {ln.line_no} on sheet {bill.doc_id} charges ${ln.charges:,.2f}"
                        + (f" ({units} x ${price.gross:,.2f})" if units > 1 else "")
                        + f". {index.hospital} publishes ${price.cash:,.2f} as its cash price for "
                        f"{price.description} (code {price.code}), prices as of the hospital's publication "
                        f"date, {when}. Without insurance, ask to be billed the cash price."),
                amount=amount, evidence=ev,
                math=src | {"code": price.code, "code_type": price.code_type, "description": price.description,
                            "setting": price.setting, "gross": str(price.gross), "cash": str(price.cash),
                            "units": str(units), "charge": str(ln.charges), "mrf_row": str(price.row)}))
    return out, saved


DRG_CODE = re.compile(r"^(?:MS-?)?DRG\s*#?\s*(\d{3})$", re.I)


def check_plan_rate(case: Case) -> list[Finding]:
    """Insured, with the plan named in the profile: what the plan allowed for a hospital claim line
    (charges minus the contract adjustment) against the rate the hospital publishes for that plan.

    Firm rates (case rate, fee schedule, per diem) with the family owing the whole allowed amount (a deductible)
    give a sum: the excess. Otherwise (coinsurance, or a rate the hospital computes as a percent of charges)
    the finding is borderline: ask the plan to reprocess, no sum."""
    from billradar import prices
    plan, index = case.profile.plan, prices.load(case.policy_id)
    if not case.profile.insured or plan is None or index is None:
        return []
    out = []
    when = _published(index.last_updated_on)
    for eob in case.eobs():
        if case.letterhead and eob.provider and not (_name_words(case.letterhead) & _name_words(eob.provider)):
            continue  # a doctor's claim: the hospital's file has the hospital's rates only
        for ln in eob.lines:
            if not ln.code or ln.charges is None or ln.adjustment is None:
                continue
            m = DRG_CODE.match(ln.code.strip())
            ctype, code = ("MS-DRG", m.group(1)) if m else (None, ln.code.strip().upper())
            found = (index.plan_rates(plan.payer, plan.plan_name, ctype, code) if ctype else
                     [r for t in prices.CODE_TYPES for r in index.plan_rates(plan.payer, plan.plan_name, t, code)])
            if not found:
                continue
            allowed = _q(ln.charges - abs(ln.adjustment))
            dollars = {r.dollar for r in found}
            rate = found[0]
            src = {"plan_payer": plan.payer, "plan_name": plan.plan_name, "code": code, "code_type": rate.code_type,
                   "allowed": str(allowed), "mrf_url": index.url, "mrf_updated": index.last_updated_on,
                   "mrf_version": index.version}
            ev = [Evidence(doc_id=eob.doc_id, line_no=ln.line_no)]
            label = f"{plan.payer} {plan.plan_name}"
            if len(dollars) > 1:
                if allowed > min(dollars):
                    out.append(Finding(
                        kind=Kind.C2, severity="borderline", title=f"Check the plan's rate for code {code}",
                        detail=(f"Your plan allowed ${allowed:,.2f} on line {ln.line_no} of {eob.doc_id}. "
                                f"{index.hospital} publishes several {label} rates for this code, from "
                                f"${min(dollars):,.2f} to ${max(dollars):,.2f} (prices as of the hospital's "
                                f"publication date, {when}). Ask the plan which applies."),
                        evidence=ev, math=src | {"rates": ",".join(str(d) for d in sorted(dollars))}))
                continue
            if allowed <= rate.dollar + Decimal("1.00"):
                continue
            excess = _q(allowed - rate.dollar)
            share = ln.patient_responsibility if ln.patient_responsibility is not None else Decimal(0)
            firm = rate.methodology in prices.FIRM
            sure = firm and share >= allowed  # the family owes all of it: every extra dollar is theirs
            amount = min(excess, share) if sure else None
            math = src | {"rate": str(rate.dollar), "methodology": rate.methodology, "description": rate.description,
                          "setting": rate.setting, "mrf_row": str(rate.row), "excess": str(excess),
                          "patient_share": str(share)}
            if not sure and share > 0:
                math["amount_if_confirmed"] = str(_q(share * excess / allowed))
            kind_of_rate = rate.methodology if firm else "an estimate the hospital computes from its charges"
            out.append(Finding(
                kind=Kind.C2, severity="high" if sure else "borderline",
                title=(f"Plan allowed ${excess:,.2f} over its published rate" if sure else
                       f"Plan allowed more than its published rate for code {code}"),
                detail=(f"Line {ln.line_no} of insurance statement {eob.doc_id}: the plan allowed ${allowed:,.2f}. "
                        f"{index.hospital} publishes ${rate.dollar:,.2f} for {label}, {rate.description} "
                        f"({kind_of_rate}), prices as of the hospital's publication date, {when}. "
                        + ("You were asked to pay the whole allowed amount, so the difference is yours to dispute."
                           if sure else "Ask the plan to reprocess the claim at the published rate.")),
                amount=amount, evidence=ev, math=math))
    return out


def _published(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%b %d, %Y")
    except ValueError:
        return iso or "date not given"


def _laws_for(case: Case, f: Finding) -> list[str]:
    from billradar import laws
    nonprofit = laws.hospital(case).get("nonprofit")
    extra: list[str] = []
    if f.kind in (Kind.F1, Kind.F3) and nonprofit:
        extra = laws.refs_for("agb-limit") + laws.refs_for("fap-refund")
    elif f.kind == Kind.F2 and nonprofit:
        extra = laws.refs_for("fap-240-days")
    return list(dict.fromkeys(f.laws + extra))


# --- run all ------------------------------------------------------------------------------

def run(case: Case) -> Report:
    pol = policy.load(case.policy_id)
    # The policy's own guideline year ("Per the 2026 FPG"), not the year of today's date.
    pct = fpl.percent_of_guideline(case.profile.annual_income, case.profile.household_size,
                                   pol.fpg_year or fpl.latest_year())
    from billradar import sheet_checks  # imports Finding from here

    e1, matches, overcharge = check_e1(case)
    sheet = sheet_checks.run(case)
    for doc_id, extra in sheet_checks.overcharge_by_doc(sheet).items():
        overcharge[doc_id] = overcharge.get(doc_id, Decimal(0)) + extra
    cash, cash_saved = check_cash(case)
    for doc_id, extra in cash_saved.items():  # assistance is computed on what is left after the cash price
        overcharge[doc_id] = overcharge.get(doc_id, Decimal(0)) + extra
    fa, nm = check_assistance(case, pol, pct, overcharge)
    window = check_window(case, pol) if fa else None
    nsa = check_nsa(case, e1)
    candidates = e1 + sheet + nsa + cash + check_plan_rate(case) + fa + ([window] if window else []) + check_appeal(case, sheet) + check_itemized(case)
    from billradar import explain
    for f in candidates:
        f.laws = [r for r in _laws_for(case, f) if policy.verified(r)]
        f.severity = f.severity or "high"
        f.explain = f.explain or explain.finding(f.kind.value)

    shown, held = [], []
    for f in candidates:
        bad = [r for r in f.policy_refs if not policy.verified(r)]
        if bad:
            held.append(f.model_copy(update={"detail": f.detail + f" [held back: unverified {', '.join(bad)}]"}))
        else:
            shown.append(f)
    no_money = [f for f in nm + matches if all(policy.verified(r) for r in f.policy_refs)]
    return split_borderline(Report(case_id=case.case_id, policy_id=case.policy_id, fpl_pct=pct, findings=shown,
                                   no_money=no_money, held_back=held, total_may_come_off=Decimal(0), apply_by=None))


def split_borderline(report: Report) -> Report:
    """Borderline findings move to `worth_asking` without a sum; the total counts confirmed money only."""
    sure, ask = [], list(report.worth_asking)
    for f in report.findings:
        if f.severity == "borderline":
            if f.amount is not None:
                f = f.model_copy(update={"amount": None, "math": f.math | {"amount_if_confirmed": str(f.amount)}})
            ask.append(f)
        else:
            sure.append(f)
    total = sum((f.amount for f in sure if f.amount is not None), Decimal(0))
    apply_by = next((f.deadline for f in sure if f.kind == Kind.F2), None)
    return report.model_copy(update={"findings": sure, "worth_asking": ask, "total_may_come_off": _q(total),
                                     "apply_by": apply_by})


__all__ = ["run", "Report", "Finding", "Kind", "Evidence", "episodes", "DocKind"]
