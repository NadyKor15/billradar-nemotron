"""The Laws block and the checks that rest on it. All cases here are SYNTHETIC; service codes are made up."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from billradar import checks, context, laws, policy
from billradar.case import Case, DocKind, Document, Line, Profile, Source
from billradar.checks import Kind
from tests.test_checks import load, needs_text

pytestmark = needs_text


def test_all_13_protections_are_shown_and_every_quote_verifies():
    out = laws.for_case(load("anna-birth"))
    assert out.held_back == []
    assert sorted(c.n for c in out.cards) == list(range(1, 14))
    for c in out.cards:
        assert c.quotes and all(policy.verified(q.ref) for q in c.quotes)
        assert c.quotes[0].url.startswith("https://")


def test_a_protection_with_an_unverified_quote_is_held_back(monkeypatch):
    reg = laws.registry()
    fake = dict(laws.protection("lawsuit-3-years"), refs=["fl-95-11#made-up"])
    monkeypatch.setattr(laws, "registry", lambda: reg | {"protections": [fake]})
    monkeypatch.setattr(policy, "verified", lambda ref: ref != "fl-95-11#made-up")
    out = laws.for_case(load("anna-birth"))
    assert out.cards == [] and out.held_back == ["lawsuit-3-years"]


def test_made_up_quote_does_not_verify():  # must light: the check can say no
    fake = policy.Fragment("nsa-149-420", "fake", 1, "Anesthesiologists may bill any amount they like.")
    assert not policy.verify_fragment(fake).found


def test_florida_nonprofit_hospital_protections_apply_with_dates():
    case = load("anna-birth")
    cards = {c.id: c for c in laws.for_case(case).cards}
    first = laws.first_bill_date(case)
    assert cards["itemized-bill-7-days"].applies == "yes"
    assert cards["fap-240-days"].applies == "yes" and cards["fap-240-days"].deadline == first + timedelta(days=240)
    assert cards["no-collections-120-days"].deadline == first + timedelta(days=120)
    assert cards["estimate-and-dispute"].applies == "no"  # insured
    assert cards["nsa-emergency"].applies == "check"


def test_other_state_and_unknown_nonprofit():
    case = load("anna-birth")
    case.policy_id = "somewhere-else"
    cards = {c.id: c for c in laws.for_case(case).cards}
    assert cards["lawsuit-3-years"].applies == "other_state"
    assert cards["agb-limit"].applies == "check"
    assert "Outside Florida" in laws.for_case(case).what_next


def test_uninsured_get_the_estimate_and_dispute_card():
    case = load("anna-birth", **{"profile.insured": False})
    card = next(c for c in laws.for_case(case).cards if c.id == "estimate-and-dispute")
    assert card.applies == "yes" and card.deadline == laws.first_bill_date(case) + timedelta(days=120)


def test_chips_switch_protections_on():
    case = load("anna-birth", **{"profile.context": ["er_visit", "out_of_network", "not-a-chip"]})
    cards = {c.id: c for c in laws.for_case(case).cards}
    assert cards["nsa-emergency"].applies == "yes" and cards["nsa-ancillary"].applies == "yes"
    assert context.valid(case.profile.context) == ["er_visit", "out_of_network"]
    on = context.rules_on(case.profile.context)
    assert on["laws"] == ["nsa-emergency", "nsa-ancillary"] and "N1" in on["checks"]
    every_law = {x for c in context.CHIPS for x in c.laws}
    assert every_law <= {p["id"] for p in laws.registry()["protections"]}


# --- checks that rest on the laws -------------------------------------------------------------

def _case(docs, insured=True, ctx=None, policy_id="memorial-2026-05") -> Case:
    return Case(case_id="t", letterhead="Lakeview Demo Hospital", policy_id=policy_id, today=date(2026, 3, 1),
                profile=Profile(household_size=3, annual_income=Decimal(250000), insured=insured,
                                context=ctx or []),
                documents=docs)


def _anesthesia_bill(balance="900.00", claim="CLM-1") -> Document:
    return Document(doc_id="S1", source=Source.PAPER, kind=DocKind.HOSPITAL_STATEMENT, patient="PATIENT_1",
                    provider="Sample Anesthesia Associates", account_ref="ACCT_9", statement_date=date(2026, 1, 10),
                    service_from=date(2025, 12, 1), patient_balance=Decimal(balance),
                    lines=[Line(line_no=1, service_date=date(2025, 12, 1), code="ZZ101", charges=Decimal("2400"),
                                claim_ref=claim, description="Anesthesia, out of network")])


def _eob(share="150.00", paid="1100.00", denial="", claim="CLM-1", eob_date=date(2026, 1, 2)) -> Document:
    return Document(doc_id="S2", source=Source.PAPER, kind=DocKind.EOB, patient="PATIENT_1",
                    provider="Sample Health Plan", statement_date=eob_date,
                    lines=[Line(line_no=1, service_date=date(2025, 12, 1), code="ZZ101", charges=Decimal("2400"),
                                insurance_paid=Decimal(paid), patient_responsibility=Decimal(share),
                                claim_ref=claim, denial_codes=denial)])


def test_nsa_out_of_network_specialist_bill_over_the_in_network_share():
    report = checks.run(_case([_anesthesia_bill(), _eob()]))
    n1 = [f for f in report.findings if f.kind == Kind.N1]
    assert len(n1) == 1 and n1[0].amount == Decimal("750.00") and n1[0].severity == "high"
    assert "nsa-149-420#ancillary-no-waiver" in n1[0].laws
    assert report.total_may_come_off == Decimal("750.00")


def test_nsa_needs_a_sign_of_out_of_network():  # must differ: same bill, nothing says out of network
    bill = _anesthesia_bill()
    bill.lines[0].description = "Anesthesia"
    report = checks.run(_case([bill, _eob()]))
    assert not [f for f in report.findings + report.worth_asking if f.kind == Kind.N1]
    report = checks.run(_case([bill, _eob()], ctx=["out_of_network"]))
    assert [f.kind for f in report.findings if f.kind == Kind.N1] == [Kind.N1]


def test_nsa_without_an_insurance_statement_is_borderline_and_has_no_sum():
    report = checks.run(_case([_anesthesia_bill()]))
    assert not [f for f in report.findings if f.kind == Kind.N1]
    ask = [f for f in report.worth_asking if f.kind == Kind.N1]
    assert len(ask) == 1 and ask[0].amount is None and ask[0].severity == "borderline"
    assert report.total_may_come_off == 0


def test_appeal_deadline_from_a_denied_line_that_leaves_money_owed():
    eob = _eob(share="2400.00", paid="0", denial="X17", eob_date=date(2026, 1, 2))
    a1 = [f for f in checks.run(_case([eob])).findings if f.kind == Kind.A1]
    assert len(a1) == 1 and a1[0].deadline == date(2026, 7, 1) == laws.appeal_deadline(date(2026, 1, 2))
    assert a1[0].amount is None and a1[0].laws[0] == laws.APPEAL_REF
    # must differ: nothing owed after the denial, or no denial at all -> no appeal finding
    assert not [f for f in checks.run(_case([_eob(share="0", paid="0", denial="X17")])).findings if f.kind == Kind.A1]
    assert not [f for f in checks.run(_case([_eob()])).findings if f.kind == Kind.A1]


def test_statement_without_itemized_bill_asks_for_it():
    bill = _anesthesia_bill()
    bill.provider, bill.lines[0].description = "Lakeview Demo Hospital", "Room"
    report = checks.run(_case([bill]))
    i1 = [f for f in report.worth_asking if f.kind == Kind.I1]
    assert len(i1) == 1 and i1[0].math["letter"] == "itemized"
    assert i1[0].laws == ["fl-395-301#itemized-on-request", "fl-395-301#itemized-7-days"]
    itemized = bill.model_copy(update={"doc_id": "S3", "kind": DocKind.ITEMIZED_BILL})
    assert not [f for f in checks.run(_case([bill, itemized])).worth_asking if f.kind == Kind.I1]


def test_assistance_findings_carry_the_nonprofit_protections():
    report = checks.run(load("anna-birth"))
    f3 = next(f for f in report.findings if f.kind == Kind.F3)
    assert {"federal-501r-5#agb-limit", "federal-501r-6#refund-overpaid"} <= set(f3.laws)
    f2 = next(f for f in report.findings if f.kind == Kind.F2)
    assert "federal-501r#application-period" in f2.laws
    assert all(f.severity in ("high", "borderline") for f in report.findings + report.worth_asking)


def test_borderline_money_never_reaches_the_total():
    report = checks.run(load("anna-birth"))
    f = report.findings[0].model_copy(update={"severity": "borderline"})
    split = checks.split_borderline(report.model_copy(update={"findings": [f] + report.findings[1:]}))
    moved = split.worth_asking[-1]
    assert moved.amount is None and moved.math["amount_if_confirmed"] == str(f.amount)
    assert split.total_may_come_off == report.total_may_come_off - (f.amount or 0)


@pytest.mark.parametrize("pid", [p["id"] for p in laws.registry()["protections"]])
def test_every_protection_has_plain_words_and_a_citation(pid):
    p = laws.protection(pid)
    assert p["title"] and p["plain"] and p["cite"] and p["refs"]
    for word in ("diagnos", "treatment", "you should take"):
        assert word not in p["plain"].lower()
