"""The five paper-only classes, each on a small SYNTHETIC case one case per pattern."""

from datetime import date
from decimal import Decimal

import pytest

from billradar import checks, sheet_checks
from billradar.case import Case, DocKind, Document, Line, Person, Profile, Role, Source
from billradar.checks import Kind
from billradar.policies import source

needs_text = pytest.mark.skipif(not all(source.is_cached(p) for p in source.SOURCES),
                                reason="run scripts/fetch_policies.py first")
D = date(2025, 8, 4)


def make(*docs: Document, policy_id: str = "memorial-2026-05", income: str = "85000") -> Case:
    prof = Profile(household_size=4, annual_income=Decimal(income), liquid_assets=Decimal(6000), insured=True,
                   people=[Person(token="PATIENT_1", role=Role.MOTHER), Person(token="PATIENT_2", role=Role.NEWBORN)])
    return Case(case_id="t", policy_id=policy_id, today=date(2026, 10, 26), profile=prof, documents=list(docs))


def line(n, code, charges, resp, **kw) -> Line:
    return Line(line_no=n, service_date=kw.pop("service_date", D), code=code, charges=Decimal(charges),
                patient_responsibility=Decimal(resp) if resp is not None else None, **kw)


def doc(doc_id, kind, patient="PATIENT_1", **kw) -> Document:
    return Document(doc_id=doc_id, source=Source.PAPER, kind=kind, patient=patient, **kw)


def kinds(findings):
    return [f.kind for f in findings]


def test_duplicate_anesthesia_line_counted_once_across_bill_and_eob():
    bill = doc("S1", DocKind.HOSPITAL_STATEMENT, provider="Sample Anesthesia Group", patient_balance=Decimal("1100"),
               lines=[line(1, "01960", "2200.00", "550.00"), line(2, "01960", "2200.00", "550.00")])
    eob = doc("S2", DocKind.EOB, lines=[line(1, "01960", "2200.00", "550.00"), line(2, "01960", "2200.00", "550.00")])
    found = sheet_checks.duplicates(make(bill, eob))
    assert kinds(found) == [Kind.E2]
    assert found[0].amount == Decimal("550.00")
    assert {e.doc_id for e in found[0].evidence} == {"S1", "S2"}


def test_same_code_on_different_days_is_not_a_duplicate():
    bill = doc("S1", DocKind.HOSPITAL_STATEMENT, lines=[
        line(1, "99213", "300.00", "60.00"), line(2, "99213", "300.00", "60.00", service_date=date(2025, 8, 5))])
    assert sheet_checks.duplicates(make(bill)) == []


def test_credit_in_parentheses_is_money_back():
    bill = doc("S1", DocKind.HOSPITAL_STATEMENT, provider="Sample OB Group", patient_balance=Decimal("410.00"),
               lines=[line(1, "59409", "4200.00", "435.00"),
                      line(2, "76801", "0.00", "-25.00", description="Copay overpaid")])
    (f,) = sheet_checks.credits(make(bill))
    assert f.kind == Kind.E3 and f.amount == Decimal("25.00") and "Copay overpaid" in f.detail


def test_negative_balance_is_a_credit():
    bill = doc("S1", DocKind.HOSPITAL_STATEMENT, patient_balance=Decimal("-40.00"))
    (f,) = sheet_checks.credits(make(bill))
    assert f.amount == Decimal("40.00")


def test_eob_negatives_are_reversals_not_credits():
    eob = doc("S1", DocKind.EOB, lines=[line(1, "0110", "-5000.00", "-500.00", denial_codes="X42")])
    assert sheet_checks.credits(make(eob)) == []


def test_zero_balance_but_pay_this_amount():
    bill = doc("S1", DocKind.HOSPITAL_STATEMENT, patient_balance=Decimal("0.00"), amount_due_coupon=Decimal("1385.00"))
    (f,) = sheet_checks.two_amounts(make(bill))
    assert f.kind == Kind.E4 and "$0.00" in f.detail and "$1,385.00" in f.detail and f.amount is None


def test_newborn_codes_under_mothers_id():
    eob = doc("S1", DocKind.EOB, member_ref="[MEMBER_1]", lines=[
        line(1, "99460", "1800.00", "1800.00", denial_codes="X17"), line(2, "99462", "900.00", "900.00", denial_codes="X17"),
        line(3, "59409", "5000.00", "300.00")])
    (f,) = sheet_checks.baby_under_mother(make(eob))
    assert f.kind == Kind.B1 and f.math["codes"] == "99460, 99462" and f.math["denied_lines"] == "2"
    assert "[MEMBER_1]" in f.detail


def test_newborn_codes_under_babys_id_are_fine():
    eob = doc("S1", DocKind.EOB, patient="PATIENT_2", lines=[line(1, "0170", "1200.00", "0.00")])
    assert sheet_checks.baby_under_mother(make(eob)) == []


def test_missing_pages_of_one_statement():
    pages = [doc(f"S{i}", DocKind.EOB, provider="Sample Health Plan", claim_ref="CL-9", page_no=n, page_count=5)
             for i, n in ((1, 2), (2, 3), (3, 5))]
    (f,) = sheet_checks.missing_pages(make(*pages))
    assert f.kind == Kind.P1 and f.math["missing"] == "1,4" and f.title == "Pages 1, 4 of 5 are missing"


@needs_text
def test_duplicate_extra_is_not_discounted_again():
    # Mom's bill has a duplicate line and no financial assistance: the policy discount applies to
    # the balance after the duplicate is taken off, so the same dollars are not counted twice.
    bill = doc("S1", DocKind.HOSPITAL_STATEMENT, patient_balance=Decimal("1100.00"), statement_date=date(2025, 8, 11),
               service_from=D, service_to=D, lines=[line(1, "01960", "2200.00", "550.00"), line(2, "01960", "2200.00", "550.00")])
    r = checks.run(make(bill))
    e2 = next(f for f in r.findings if f.kind == Kind.E2)
    f1 = next(f for f in r.findings if f.kind == Kind.F1 and "fpl_pct" in f.math)
    assert e2.amount == Decimal("550.00")
    assert f1.amount == Decimal("467.50")  # 85% of (1100 - 550)
    assert r.total_may_come_off == Decimal("1017.50")


@needs_text
def test_statement_and_itemized_bill_of_one_account_count_once():
    # Shaped like the first look-alike pack: hospital statement and itemized bill, same account, no assistance.
    stmt = Document(doc_id="D1", source=Source.PDF, kind=DocKind.HOSPITAL_STATEMENT, patient="PATIENT_1",
                    account_ref="[ACCT_1]", patient_balance=Decimal("5040.00"), statement_date=date(2025, 9, 12),
                    service_from=date(2025, 8, 4), service_to=date(2025, 8, 6))
    itemized = stmt.model_copy(update={"doc_id": "D2", "kind": DocKind.ITEMIZED_BILL, "statement_date": date(2025, 8, 20)})
    r = checks.run(make(stmt, itemized, income="57500").model_copy(
        update={"profile": make(stmt, income="57500").profile.model_copy(update={"household_size": 3})}))
    f1 = [f for f in r.findings if f.kind == Kind.F1]
    assert len(f1) == 1 and f1[0].evidence[0].doc_id == "D1"
    assert f1[0].amount == Decimal("4536.00")  # 90% at 210.5% of the guideline for 3, counted once


def test_a_twin_line_read_without_a_date_still_makes_a_double():
    """S10 prints 01960 $4,944.00 twice; a reading that dates one line and not the other must not lose the double."""
    from datetime import date
    from decimal import Decimal
    from billradar.case import Case, Document, DocKind, Line, Source
    from billradar.sheet_checks import duplicates
    from billradar import example
    import json
    info = json.loads((example.PACK / "profile.json").read_text())
    from billradar.case import Profile
    def case(second_charge):
        lines = [Line(line_no=1, code="01960", service_date=date(2025, 2, 11), charges=Decimal("4944.00")),
                 Line(line_no=2, code="01960", charges=Decimal(second_charge))]
        doc = Document(doc_id="S10", source=Source.PAPER, kind=DocKind.ITEMIZED_BILL, lines=lines)
        return Case(case_id="t", policy_id=info["policy_id"], today=date(2026, 3, 16),
                    profile=Profile.model_validate(info["profile"]), documents=[doc])
    found = duplicates(case("4944.00"))
    assert [f.kind.value for f in found] == ["E2"] and found[0].math["service_date"] == "2025-02-11"
    assert duplicates(case("4945.00")) == []  # must differ: another charge is another line
