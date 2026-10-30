"""Checks that need no hospital policy: what the papers themselves get wrong.

  E2  the same service billed twice: same date, same code, same charge
  E3  a credit or overpayment sits on the sheet instead of coming back to the family
  E4  one sheet prints two different amounts to pay ("Balance due $0.00" and "PAY THIS AMOUNT")
  B1  the baby's care is billed under the mother's insurance ID
  P1  "Page 2 of 5": pages of a statement are missing from the pile

These are common billing error patterns on hospital, doctor and insurance papers after a birth.
"""

from __future__ import annotations

import re
from decimal import Decimal

from billradar.case import Case, DocKind, Document, Role, Source
from billradar.checks import Evidence, Finding, Kind, is_hospital_bill

# Newborn and neonatal care: CPT 9946x and 9947x; revenue codes 017x (nursery).


def _is_newborn_code(code: str) -> bool:
    c = code.strip().upper()
    return bool(re.fullmatch(r"994[67]\d", c) or re.fullmatch(r"0?17\d", c))


def duplicates(case: Case) -> list[Finding]:
    """Bills first. The same double line on the insurance statement joins the bill's finding as
    evidence instead of becoming a second finding, so the extra charge is counted once."""
    out: list[Finding] = []
    by_service: dict[tuple, Finding] = {}
    by_charge: dict[tuple, Finding] = {}  # an insurance statement may print no date: match code and charge
    docs = sorted(case.documents, key=lambda d: d.kind == DocKind.EOB)
    for d in docs:
        seen: dict[tuple, int] = {}
        # a bill line read without a date takes the date of a line of the same paper with its code and charge
        dated = {(x.code.upper(), x.charges): x.service_date for x in d.lines if x.code and x.service_date}
        for ln in d.lines:
            if ln.service_date is None and d.kind != DocKind.EOB and ln.code and (ln.code.upper(), ln.charges) in dated:
                ln = ln.model_copy(update={"service_date": dated[(ln.code.upper(), ln.charges)]})
            undated = ln.service_date is None and d.kind == DocKind.EOB
            if not ln.code or ln.charges is None or (ln.service_date is None and not undated):
                continue
            key = (ln.service_date, ln.code.upper(), ln.charges)
            if key not in seen:
                seen[key] = ln.line_no
                continue
            same = (by_charge.get((ln.code.upper(), ln.charges)) if undated
                    else by_service.get((ln.service_date, ln.code.upper())))
            if undated and same is None:
                continue  # without a date an insurance statement alone does not make a double
            if same is not None and same.evidence[0].doc_id != d.doc_id:
                same.evidence += [Evidence(doc_id=d.doc_id, line_no=seen[key], note="also twice here"),
                                  Evidence(doc_id=d.doc_id, line_no=ln.line_no)]
                # a doctor's bill often prints no per-line share; the insurance statement does
                if same.amount is None and ln.patient_responsibility and ln.patient_responsibility > 0:
                    same.amount = ln.patient_responsibility
                    same.math["second_patient_responsibility"] = str(ln.patient_responsibility)
                    same.math["amount_from"] = d.doc_id
                    same.detail += f" Your insurance statement shows ${ln.patient_responsibility:,.2f} for the second one."
                continue
            extra = ln.patient_responsibility if ln.patient_responsibility and ln.patient_responsibility > 0 else None
            out.append(f := Finding(
                kind=Kind.E2,
                title=f"Same service billed twice on sheet {d.doc_id[1:]}",
                detail=(f"Lines {seen[key]} and {ln.line_no} are the same code {ln.code} on {ln.service_date:%b %d, %Y} "
                        f"for ${ln.charges:,.2f} each."
                        + (f" You are asked to pay ${extra:,.2f} for the second one." if extra else "")),
                amount=extra,
                evidence=[Evidence(doc_id=d.doc_id, line_no=seen[key]), Evidence(doc_id=d.doc_id, line_no=ln.line_no)],
                math={"code": ln.code, "service_date": ln.service_date.isoformat(), "charges": str(ln.charges),
                      "second_patient_responsibility": str(extra or 0)}))
            by_service[(ln.service_date, ln.code.upper())] = f
            by_charge[(ln.code.upper(), ln.charges)] = f
    return out


def credits(case: Case) -> list[Finding]:
    out = []
    for d in case.documents:
        if d.kind == DocKind.EOB:
            continue
        if d.patient_balance is not None and d.patient_balance < 0:
            amt = abs(d.patient_balance)
            acct = d.account_ref.strip()
            later = [o for o in case.documents if o is not d and acct and o.account_ref.strip() == acct
                     and o.amount_due_coupon and o.amount_due_coupon > 0]
            if later:  # the same account already asks a sum after it: the credit is likely taken off there
                o = later[0]
                out.append(Finding(
                    kind=Kind.E3, severity="borderline", title=f"Check the ${amt:,.2f} credit stays taken off",
                    detail=(f"Sheet {d.doc_id[1:]} shows a ${amt:,.2f} credit. Sheet {o.doc_id[1:]} of the same account asks "
                            f"${o.amount_due_coupon:,.2f}, which looks to include it already. Check that it stays there; "
                            "it is not counted as money back."),
                    evidence=[Evidence(doc_id=d.doc_id), Evidence(doc_id=o.doc_id)],
                    math={"patient_balance": str(d.patient_balance), "applied_on": o.doc_id}))
                continue
            out.append(Finding(
                kind=Kind.E3, title=f"You have a ${amt:,.2f} credit on sheet {d.doc_id[1:]}",
                detail="The balance is in your favor. Ask for a refund instead of leaving it on the account.",
                amount=amt, evidence=[Evidence(doc_id=d.doc_id)], math={"patient_balance": str(d.patient_balance)}))
            continue
        for ln in d.lines:
            if ln.patient_responsibility is not None and ln.patient_responsibility < 0:
                amt = abs(ln.patient_responsibility)
                out.append(Finding(
                    kind=Kind.E3, title=f"${amt:,.2f} you overpaid is still on sheet {d.doc_id[1:]}",
                    detail=(f"Line {ln.line_no} shows a credit of ${amt:,.2f}"
                            f"{' (' + ln.description + ')' if ln.description else ''}. Ask the office to refund it "
                            "or to show it taken off what you owe."),
                    amount=amt, evidence=[Evidence(doc_id=d.doc_id, line_no=ln.line_no)],
                    math={"line_patient_responsibility": str(ln.patient_responsibility)}))
    return out


def two_amounts(case: Case) -> list[Finding]:
    out = []
    for d in case.documents:
        if d.patient_balance is None or d.amount_due_coupon is None:
            continue
        # Statements often show a subtotal per claim next to a coupon for the whole account; that is normal.
        # The error is one part of the sheet saying nothing is owed while another asks for money.
        if (d.patient_balance == 0) == (d.amount_due_coupon == 0):
            continue
        out.append(Finding(
            kind=Kind.E4, title=f"Sheet {d.doc_id[1:]} asks two different amounts",
            detail=(f"It says your balance is ${d.patient_balance:,.2f} and also asks you to pay "
                    f"${d.amount_due_coupon:,.2f}. Do not pay until the office explains which one is right."),
            evidence=[Evidence(doc_id=d.doc_id)],
            math={"patient_balance": str(d.patient_balance), "amount_due_coupon": str(d.amount_due_coupon)}))
    return out


def baby_not_matched(case: Case) -> list[Finding]:
    """A doctors' group asks the family for the baby's care and no insurance statement in the pile covers the
    baby: the plan has not matched the baby yet. The fix is the baby's insurance details, sent now."""
    if any(case.profile.role_of(e.patient) == Role.NEWBORN for e in case.eobs()):
        return []
    out = []
    for d in case.documents:
        if d.kind in (DocKind.EOB, DocKind.ITEMIZED_BILL) or case.profile.role_of(d.patient) != Role.NEWBORN:
            continue
        if not (d.patient_balance and d.patient_balance > 0) or not d.statement_date or not d.provider:
            continue
        if d.kind == DocKind.HOSPITAL_STATEMENT and is_hospital_bill(case, d):
            continue  # the hospital's own bill for the baby is part of B1
        age = (case.today - d.statement_date).days
        out.append(Finding(
            kind=Kind.B2, title=f"Send the baby's insurance details to {d.provider}",
            detail=(f"Sheet {d.doc_id[1:]} from {d.provider}, dated {d.statement_date:%b %d, %Y}, asks ${d.patient_balance:,.2f} "
                    "for the baby's care, and no insurance statement in your papers covers the baby. "
                    + (f"It is {age} days old: any time it gave you has likely passed, so send the baby's insurance "
                       "details now." if age > 30 else "Send the baby's insurance details before it becomes yours to pay.")),
            evidence=[Evidence(doc_id=d.doc_id)],
            math={"patient_balance": str(d.patient_balance), "statement_date": d.statement_date.isoformat(),
                  "days_since": str(age)}))
    return out


def baby_under_mother(case: Case) -> list[Finding]:
    out = []
    for d in case.documents:
        if case.profile.role_of(d.patient) != Role.MOTHER:
            continue
        lines = [ln for ln in d.lines if _is_newborn_code(ln.code)]
        if not lines:
            continue
        # the newborn codes show why; every line denied with the same reason belongs to the baby's stay too
        reasons = {ln.denial_codes.strip() for ln in lines if ln.denial_codes.strip()}
        denied = [ln for ln in d.lines if (ln.denial_codes.strip() in reasons) or
                  (ln in lines and ln.insurance_paid == 0 and (ln.charges or 0) > 0)]
        total = sum((ln.charges or Decimal(0)) for ln in denied)
        codes = ", ".join(sorted({ln.code for ln in lines}))
        n = len(denied)
        why = f" (reason {', '.join(sorted(reasons))})" if reasons else ""
        detail = (f"Sheet {d.doc_id[1:]} lists newborn care ({codes}) for mom"
                  + (f"{' (member ' + d.member_ref + ')' if d.member_ref else ''}. ")
                  + (f"{n} line{'s' if n != 1 else ''} for ${total:,.2f} were denied{why}. " if denied else "")
                  + "Ask the insurer and the provider to bill it under the baby, or add the baby to the plan, "
                    "before the balance moves to you.")
        baby = next((b for b in case.bills() if case.profile.role_of(b.patient) == Role.NEWBORN
                     and b.insurance_paid is not None and b.insurance_paid < 0), None)
        baby_eob = any(case.profile.role_of(e.patient) == Role.NEWBORN for e in case.eobs())
        if baby and not baby_eob:  # a payment on the baby's bill that no statement in the pile shows
            detail += (f" The hospital already bills the baby's account ({'sheet' if baby.doc_id.startswith('S') else 'file'} "
                       f"{baby.doc_id[1:]}) ${baby.patient_balance or 0:,.2f} and shows an insurance credit of "
                       f"${abs(baby.insurance_paid):,.2f} that no insurance statement in your papers explains: "
                       "ask for the statement behind it.")
        out.append(Finding(
            kind=Kind.B1, title="Baby's care is billed under mom's insurance", detail=detail,
            evidence=[Evidence(doc_id=d.doc_id, line_no=ln.line_no, note=ln.denial_codes) for ln in denied or lines]
                     + ([Evidence(doc_id=baby.doc_id, note="baby's bill")] if baby and not baby_eob else []),
            math={"codes": codes, "denied_lines": str(n), "denied_total": str(total)}
                 | ({"baby_bill": baby.doc_id, "baby_balance": str(baby.patient_balance or 0),
                     "baby_insurance_credit": str(abs(baby.insurance_paid))} if baby and not baby_eob else {})))
    return out


FAMILY = {DocKind.EOB: "eob", DocKind.HOSPITAL_STATEMENT: "bill", DocKind.ITEMIZED_BILL: "bill"}


def _sheet_order(d: Document) -> tuple[int, str]:
    digits = re.sub(r"\D", "", d.doc_id)
    return (int(digits) if digits else 0, d.doc_id)


def page_runs(case: Case) -> list[list[Document]]:
    """Paper sheets of one multi-page statement, found by their page numbers in reading order.

    A run goes on while the next sheet has the same page count, a higher page number and a kind that fits
    ("other" fits anything: a continuation page often looks like nothing in particular). Provider, claim and
    date are not compared: an insurance statement prints a different doctor and claim on every page.
    Digital files are not counted: a PDF holds all its pages, and "Page 1 of 4" is only its footer."""
    runs: list[list[Document]] = []
    cur: list[Document] = []
    for d in sorted((d for d in case.documents if d.source != Source.PDF), key=_sheet_order):
        paged = bool(d.page_count and d.page_count > 1 and d.page_no)
        if cur and paged and d.page_count == cur[-1].page_count and d.page_no > cur[-1].page_no and _fits(cur, d):
            cur.append(d)
            continue
        if cur:
            runs.append(cur)
        cur = [d] if paged else []
    if cur:
        runs.append(cur)
    return runs


def _fits(run: list[Document], d: Document) -> bool:
    families = {FAMILY[x.kind] for x in run if x.kind in FAMILY}
    return d.kind not in FAMILY or not families or FAMILY[d.kind] in families


def missing_pages(case: Case) -> list[Finding]:
    out = []
    for docs in page_runs(case):
        count = docs[0].page_count
        have = {d.page_no for d in docs}
        missing = [n for n in range(1, count + 1) if n not in have]
        if not missing:
            continue
        kinds = {d.kind for d in docs}
        label = "insurance statement" if DocKind.EOB in kinds else "statement"
        provider = next((d.provider for d in docs if d.provider and d.kind != DocKind.EOB), "")
        out.append(Finding(
            kind=Kind.P1, title=(f"Page {missing[0]} of {count} is missing" if len(missing) == 1
                                 else f"Pages {', '.join(map(str, missing))} of {count} are missing"),
            detail=(f"This {label}{' from ' + provider if provider else ''} has {count} pages; "
                    f"we have {len(have)}. Totals may be on a missing page. Find or ask for pages "
                    f"{', '.join(map(str, missing))}."),
            evidence=[Evidence(doc_id=d.doc_id, note=f"page {d.page_no} of {count}") for d in docs],
            math={"page_count": str(count), "missing": ",".join(map(str, missing))}))
    return out


def run(case: Case) -> list[Finding]:
    return (duplicates(case) + credits(case) + two_amounts(case) + baby_under_mother(case) + baby_not_matched(case)
            + missing_pages(case))


def overcharge_by_doc(findings: list[Finding]) -> dict[str, Decimal]:
    """Money from E2/E3 per document, so policy discounts are not computed on it twice."""
    out: dict[str, Decimal] = {}
    for f in findings:
        if f.kind == Kind.E2 and f.amount:
            out[f.evidence[0].doc_id] = out.get(f.evidence[0].doc_id, Decimal(0)) + f.amount
    return out
