import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from billradar import checks, fpl, policy
from billradar.case import Case, parse_date, parse_money
from billradar.checks import Kind
from billradar.policies import source

FIX = Path(__file__).parent / "fixtures" / "cases"
needs_text = pytest.mark.skipif(
    not all(source.is_cached(p) for p in source.SOURCES),
    reason="run scripts/fetch_policies.py first",
)


def load(name: str, **changes) -> Case:
    raw = json.loads((FIX / f"{name}.json").read_text())
    for key, value in changes.items():
        target = raw
        *path, last = key.split(".")
        for p in path:
            target = target[int(p)] if p.isdigit() else target[p]
        target[last] = value
    return Case.model_validate(raw)


def by_kind(report, kind):
    return [f for f in report.findings if f.kind == kind]


# --- parsing --------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("$3,215.40", Decimal("3215.40")),
    ("(312.40)", Decimal("-312.40")),
    ("-$12", Decimal("-12.00")),
    ("1,240.00 CR", Decimal("-1240.00")),
    ("", None),
    ("N/A", None),
    ("1.2.3", None),
])
def test_parse_money(text, expected):
    assert parse_money(text) == expected


def test_parse_date_never_guesses():
    assert parse_date("2025-08-11") == date(2025, 8, 11)
    assert parse_date("8/11/2025") == date(2025, 8, 11)
    assert parse_date("May 19") is None
    assert parse_date("2026-02-30") is None


def test_fpl_2026():
    assert fpl.guideline(4) == 33000
    assert fpl.guideline(10) == 55720 + 2 * 5680
    assert fpl.percent_of_guideline(Decimal(85000), 4) == Decimal("257.6")


# --- policy quotes ----------------------------------------------------------------------------

@needs_text
@pytest.mark.parametrize("policy_id", policy.available())
def test_every_policy_quote_is_on_its_page(policy_id):
    for frag in policy.load(policy_id).fragments.values():
        check = policy.verify_fragment(frag)
        assert check.found, f"{frag.ref}: {check.detail}"


@needs_text
def test_quote_check_rejects_a_made_up_quote():
    fake = policy.Fragment("memorial-2026-05", "fake", 5, "Patients get free ice cream every Tuesday.")
    assert not policy.verify_fragment(fake).found


@needs_text
def test_quote_check_catches_wrong_page():
    real = policy.load("memorial-2026-05").fragment("window")
    moved = policy.Fragment(real.policy_id, real.fragment_id, 2, real.quote)
    check = policy.verify_fragment(moved)
    assert not check.found and check.found_on_page == 5


def test_memorial_tiers():
    pol = policy.load("memorial-2026-05")
    assert pol.tier_for(Decimal("200.0")).discount_pct == 100
    assert pol.tier_for(Decimal("250.9")).discount_pct == 90
    assert pol.tier_for(Decimal("257.6")).discount_pct == 85
    assert pol.tier_for(Decimal("400.0")).discount_pct == 83
    assert pol.tier_for(Decimal("401.0")) is None


# --- the demo case ----------------------------------------------------------------------------

@needs_text
def test_anna_birth_case():
    r = checks.run(load("anna-birth"))
    assert r.fpl_pct == Decimal("257.6")

    (e1,) = by_kind(r, Kind.E1)
    assert e1.math["difference"] == "312.40"
    assert e1.amount == Decimal("46.86")  # 312.40 * 676.80 / 4512.00: S1 already has 85% off

    (f3,) = by_kind(r, Kind.F3)
    assert f3.amount == Decimal("1054.00")  # 85% of the baby's 1,240.00
    assert {e.doc_id for e in f3.evidence} == {"S1", "S2"}

    (f2,) = by_kind(r, Kind.F2)
    assert f2.deadline == date(2026, 9, 2)  # 12 months after the last insurance payment, Sep 2 2025
    assert f2.math["federal: 240 days after first bill"] == "2026-04-08"

    assert r.total_may_come_off == Decimal("1100.86")
    assert not r.held_back
    assert len(r.no_money) == 2  # S1 line 1 and S2 line 1 match the insurance statements


@needs_text
def test_no_discount_anywhere_is_f1_on_each_bill():
    r = checks.run(load("anna-birth", **{"documents.0.fa_discount": None, "documents.0.patient_balance": "4512.00"}))
    f1 = by_kind(r, Kind.F1)
    assert {f.evidence[0].doc_id for f in f1} == {"S1", "S2"}
    e1 = by_kind(r, Kind.E1)[0]
    assert e1.amount == Decimal("312.40")  # no discount on S1 now: the whole difference counts
    s1 = next(f for f in f1 if f.evidence[0].doc_id == "S1")
    assert s1.amount == Decimal("3569.66")  # 85% of (4512.00 - 312.40)


@needs_text
def test_baptist_does_not_cover_insured_balances():
    r = checks.run(load("anna-birth", policy_id="baptist-2026-09"))
    assert not by_kind(r, Kind.F1) and not by_kind(r, Kind.F3) and not by_kind(r, Kind.F2)
    assert by_kind(r, Kind.E1)  # the insurance mismatch is still real
    nm = [f for f in r.no_money if f.policy_refs == ["baptist-2026-09#uninsured-only"]]
    assert len(nm) == 1


@needs_text
def test_income_above_policy_limit():
    r = checks.run(load("anna-birth", **{"profile.annual_income": "200000"}))
    assert not by_kind(r, Kind.F3)
    assert any("above this policy's limit" in f.title for f in r.no_money)


@needs_text
def test_small_balances_under_memorial_minimum():
    raw = {"documents.0.patient_balance": "120.00", "documents.1.patient_balance": "170.00",
           "documents.1.lines.0.patient_responsibility": "170.00", "documents.4.lines.0.patient_responsibility": "170.00"}
    r = checks.run(load("anna-birth", **raw))
    assert not by_kind(r, Kind.F3)
    assert any("minimum" in f.title for f in r.no_money)


@needs_text
def test_free_care_level_asks_about_savings():
    r = checks.run(load("anna-birth", **{"profile.annual_income": "60000", "profile.liquid_assets": "25000"}))
    assert not by_kind(r, Kind.F3)
    assert any("savings" in f.title for f in r.no_money)


@needs_text
def test_income_cap_adds_a_finding():
    # Income at 391% of the guideline: 83% off still leaves more than 10% of income on a big bill.
    raw = {"profile.annual_income": "129000", "documents.1.patient_balance": "80000.00",
           "documents.1.lines.0.patient_responsibility": "80000.00",
           "documents.4.lines.0.patient_responsibility": "80000.00"}
    r = checks.run(load("anna-birth", **raw))
    cap = [f for f in r.findings if "Capped" in f.title]
    assert cap and cap[0].math["cap"] == "12900.00"


def test_unverified_quote_is_held_back(monkeypatch):
    monkeypatch.setattr(policy, "verified", lambda ref: not ref.startswith("memorial"))
    r = checks.run(load("anna-birth"))
    assert {f.kind for f in r.findings} == {Kind.E1}
    assert {f.kind for f in r.held_back} == {Kind.F3, Kind.F2}


@needs_text
def test_guideline_year_comes_from_the_policy_not_from_today():
    r = checks.run(load("anna-birth", today="2025-04-21"))  # no 2025 table: the policy says 2026
    assert r.fpl_pct == Decimal("257.6")


def test_finding_and_profile_contract_defaults():
    from billradar.case import Profile
    from billradar.checks import Finding, Kind

    f = Finding(kind=Kind.E1, title="t", detail="d")
    assert (f.severity, f.confidence, f.laws, f.explain) == (None, None, [], None)
    g = Finding(kind=Kind.E1, title="t", detail="d", severity="borderline", confidence=0.4,
                laws=["federal-501r#application-period"], explain="plain words")
    assert Finding.model_validate_json(g.model_dump_json()) == g
    assert Profile(household_size=1, annual_income=1).context == []


def test_an_account_billed_by_a_doctors_office_is_not_the_hospitals():
    """S12 prints the hospital as the place of service, S13 of the same account is the OB/GYN office's coupon."""
    from billradar.case import Document, DocKind, Source
    from billradar.checks import one_office_per_account
    Document = __import__("functools").partial(Document, source=Source.PAPER)
    a = Document(doc_id="S12", kind=DocKind.HOSPITAL_STATEMENT, provider="Seaside Regional Hospital (IP) 300", account_ref="[ACCT_9]")
    b = Document(doc_id="S13", kind=DocKind.HOSPITAL_STATEMENT, provider="300_Women First OB/GYN Care, LLC", account_ref="[ACCT_9]")
    c = Document(doc_id="D1", kind=DocKind.HOSPITAL_STATEMENT, provider="Seaside Regional Hospital", account_ref="[ACCT_1]")
    out = one_office_per_account([a, b, c], "Seaside Regional Hospital")
    assert [d.provider for d in out] == [b.provider, b.provider, c.provider]  # the hospital's own account stays
