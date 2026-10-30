"""Published cash prices (CMS Hospital Price Transparency). The price file here is SYNTHETIC: a made-up
hospital in the CMS 3.0 'tall' CSV layout, made-up codes. The real files are checked only if cached."""

import csv
import io
import json
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from billradar import checks, prices, verifier
from billradar.case import Case, DocKind, Document, Line, Profile, Source
from billradar.checks import Kind
from tests.test_checks import needs_text

HEADER = ["description", "code|1", "code|1|type", "code|2", "code|2|type", "code|3", "code|3|type", "billing_class",
          "setting", "drug_unit_of_measurement", "drug_type_of_measurement", "standard_charge|gross",
          "standard_charge|discounted_cash", "payer_name", "plan_name", "modifiers", "standard_charge|negotiated_dollar",
          "standard_charge|negotiated_percentage", "standard_charge|negotiated_algorithm", "median_amount",
          "10th_percentile", "90th_percentile", "count", "standard_charge|min", "standard_charge|max",
          "standard_charge|methodology", "additional_generic_notes"]


def _row(desc, cdm, rc, hcpcs, setting, gross, cash, payer="Sample Payer", plan="Sample Plan", neg="101.37"):
    r = dict.fromkeys(HEADER, "")
    r.update({"description": desc, "code|1": cdm, "code|1|type": "CDM", "code|2": rc, "code|2|type": "RC",
              "code|3": hcpcs, "code|3|type": "HCPCS" if hcpcs else "", "billing_class": "facility",
              "setting": setting, "standard_charge|gross": gross, "standard_charge|discounted_cash": cash,
              "payer_name": payer, "plan_name": plan, "standard_charge|negotiated_dollar": neg,
              "standard_charge|methodology": "fee schedule"})
    return [r[h] for h in HEADER]


def mrf_text() -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["hospital_name", "last_updated_on", "version", "location_name", "hospital_address"])
    w.writerow(["Lakeview Demo Hospital (SAMPLE)", "2026-05-27", "3.0.0", "Lakeview Demo Hospital", "1 Sample Way"])
    w.writerow(HEADER)
    w.writerow(_row("HC Sample Lab Panel", "00000000001", "300", "ZZ101", "outpatient", "200.00000", "140.00000"))
    w.writerow(_row("HC Sample Lab Panel", "00000000001", "300", "ZZ101", "outpatient", "200.00000", "140.00000",
                    payer="Other Payer"))  # same price under another payer: one entry
    w.writerow(_row("HC Sample Scan", "00000000002", "320", "ZZ202", "outpatient", "900.00000", "630.00000"))
    w.writerow(_row("HC Sample Scan Portable", "00000000003", "320", "ZZ202", "outpatient", "950.00000", "665.00000"))
    w.writerow(_row("HC No Cash Listed", "00000000004", "300", "ZZ303", "outpatient", "50.00000", ""))
    return out.getvalue()


@pytest.fixture
def cached(tmp_path, monkeypatch):
    raw = tmp_path / "sample.zip"
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("sample_standardcharges.csv", mrf_text())
    monkeypatch.setattr(prices, "CACHE", tmp_path)
    monkeypatch.setattr(prices, "index_path", lambda pid: tmp_path / f"{pid}.index.json")
    data = prices.build_index(raw, "memorial-2026-05", "Lakeview Demo Hospital", "https://example.org/sample.zip")
    (tmp_path / "memorial-2026-05.index.json").write_text(json.dumps(data))
    prices.load.cache_clear()
    yield data
    prices.load.cache_clear()


def test_index_keeps_one_entry_per_price_and_skips_rows_without_cash(cached):
    assert cached["last_updated_on"] == "2026-05-27" and cached["version"] == "3.0.0"
    assert len(cached["prices"]["HCPCS:ZZ101"]) == 1 and len(cached["prices"]["HCPCS:ZZ202"]) == 2
    assert "HCPCS:ZZ303" not in cached["prices"] and "CDM:00000000001" in cached["prices"]
    assert not any(k.startswith("RC:") for k in cached["prices"])  # one revenue code covers many services


def test_a_charge_matches_one_price_by_units_or_is_ambiguous(cached):
    idx = prices.load("memorial-2026-05")
    p, units = prices.match(idx, "ZZ101", Decimal("400.00"))
    assert (p.cash, units, p.row) == (Decimal("140.00"), 2, 1)
    assert prices.match(idx, "ZZ202", Decimal("900.00"))[0].description == "HC Sample Scan"
    assert prices.match(idx, "ZZ202", Decimal("123.45")) == "ambiguous"
    assert prices.match(idx, "ZZ999", Decimal("1")) is None


def test_not_a_cms_tall_file_is_refused(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\nx,y\n")
    with pytest.raises(ValueError):
        prices.build_index(bad, "x")


def _case(lines, insured=False, discount=None) -> Case:
    bill = Document(doc_id="S1", source=Source.PAPER, kind=DocKind.ITEMIZED_BILL, patient="PATIENT_1",
                    provider="Lakeview Demo Hospital", account_ref="ACCT_1", statement_date=date(2026, 3, 1),
                    service_from=date(2026, 2, 20), patient_balance=sum((ln.charges for ln in lines), Decimal(0)),
                    fa_discount=discount, lines=lines)
    return Case(case_id="c", letterhead="Lakeview Demo Hospital", policy_id="memorial-2026-05", today=date(2026, 3, 10),
                profile=Profile(household_size=2, annual_income=Decimal(500000), insured=insured), documents=[bill])


def _lines():
    return [Line(line_no=1, code="ZZ101", charges=Decimal("400.00"), patient_responsibility=Decimal("400.00")),
            Line(line_no=2, code="ZZ202", charges=Decimal("123.45")),
            Line(line_no=3, code="ZZ999", charges=Decimal("77.00"))]


@needs_text
def test_uninsured_line_at_gross_gets_the_published_cash_price(cached):
    report = checks.run(_case(_lines()))
    c1 = [f for f in report.findings if f.kind == Kind.C1]
    assert len(c1) == 1 and c1[0].amount == Decimal("120.00") and c1[0].severity == "high"
    m = c1[0].math
    assert (m["units"], m["cash"], m["mrf_updated"], m["mrf_row"]) == ("2", "140.00", "2026-05-27", "1")
    assert "prices as of the hospital's publication date, May 27, 2026" in c1[0].detail
    ask = [f for f in report.worth_asking if f.kind == Kind.C1]
    assert len(ask) == 1 and ask[0].amount is None and "$630.00 to $665.00" in ask[0].detail
    assert report.total_may_come_off == Decimal("120.00")


@needs_text
def test_no_cash_check_with_insurance_or_a_discount_already_on_the_bill(cached):  # must differ
    assert not [f for f in checks.run(_case(_lines(), insured=True)).findings if f.kind == Kind.C1]
    assert not [f for f in checks.run(_case(_lines(), discount=Decimal("-50.00"))).findings if f.kind == Kind.C1]


@needs_text
def test_no_index_means_no_cash_finding_not_a_guess(tmp_path, monkeypatch):
    monkeypatch.setattr(prices, "index_path", lambda pid: tmp_path / "none.json")
    prices.load.cache_clear()
    try:
        assert not [f for f in checks.run(_case(_lines())).findings + checks.run(_case(_lines())).worth_asking
                    if f.kind == Kind.C1]
    finally:
        prices.load.cache_clear()


@needs_text
def test_the_checker_recomputes_the_cash_saving_from_the_file(cached):
    c = _case(_lines())
    r = checks.run(c)
    name = next(v for v in verifier.subjects(r).values() if v.startswith("C1 #1"))  # "C1 #1 on S1"
    assert verifier.check_findings(c, r).errors_for(name) == []
    f = next(f for f in r.findings if f.kind == Kind.C1)
    f.amount = Decimal("150.00")
    v = verifier.check_findings(c, r)
    assert [i.what for i in v.errors_for(name)] == ["amount 150.00 but recomputed from the sheets: 120.00"]


@pytest.mark.skipif(prices.load("memorial-2026-05") is None, reason="run scripts/fetch_prices.py first")
def test_the_demo_hospitals_real_file_publishes_a_cash_price_for_a_lab_code():
    idx = prices.load("memorial-2026-05")
    assert idx.version.startswith("3.") and idx.last_updated_on
    p, units = prices.match(idx, "85610", Decimal("497.00"))
    assert units == 1 and p.cash < p.gross


# --- the plan's published rate -------- --------------------------------------------------

def plan_mrf_text() -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["hospital_name", "last_updated_on", "version"])
    w.writerow(["Lakeview Demo Hospital (SAMPLE)", "2026-05-27", "3.0.0"])
    w.writerow(HEADER)

    def drg(dollar, method, plan="Sample Plan"):
        r = _row("SAMPLE DELIVERY STAY", "", "", "", "inpatient", "", "", plan=plan, neg=dollar)
        r[HEADER.index("code|1")], r[HEADER.index("code|1|type")] = "901", "MS-DRG"
        r[HEADER.index("standard_charge|methodology")] = method
        return r
    w.writerow(drg("6100.00", "case rate"))
    w.writerow(drg("9999.00", "case rate", plan="Another Plan"))  # not the family's plan: never used
    lab = _row("HC Sample Lab Panel", "00000000001", "300", "ZZ101", "outpatient", "200.00", "140.00", neg="88.10")
    lab[HEADER.index("standard_charge|methodology")] = "percent of total billed charges"
    w.writerow(lab)
    return out.getvalue()


@pytest.fixture
def plan_cached(tmp_path, monkeypatch):
    raw = tmp_path / "plan.csv"
    raw.write_text(plan_mrf_text())
    monkeypatch.setattr(prices, "index_path", lambda pid: tmp_path / f"{pid}.index.json")
    data = prices.build_index(raw, "memorial-2026-05", "", "https://example.org/plan.csv",
                              plans=[("Sample Payer", "Sample Plan")])
    (tmp_path / "memorial-2026-05.index.json").write_text(json.dumps(data))
    prices.load.cache_clear()
    yield data
    prices.load.cache_clear()


def _eob_case(lines, plan=True, insured=True):
    from billradar.case import Plan
    eob = Document(doc_id="S2", source=Source.PAPER, kind=DocKind.EOB, patient="PATIENT_1",
                   provider="Lakeview Demo Hospital", statement_date=date(2026, 3, 1), lines=lines)
    return Case(case_id="p", letterhead="Lakeview Demo Hospital", policy_id="memorial-2026-05", today=date(2026, 3, 10),
                profile=Profile(household_size=2, annual_income=Decimal(500000), insured=insured,
                                plan=Plan(payer="Sample Payer", plan_name="Sample Plan") if plan else None),
                documents=[eob])


def _drg_line(share="8000.00"):
    return Line(line_no=1, code="DRG 901", charges=Decimal("15000.00"), adjustment=Decimal("-7000.00"),
                insurance_paid=Decimal("0"), patient_responsibility=Decimal(share))


def test_index_keeps_only_the_chosen_plans_rates(plan_cached):
    rates = plan_cached["rates"]["sample payer|sample plan"]
    assert rates["MS-DRG:901"] == [["SAMPLE DELIVERY STAY", "inpatient", "6100.00", "case rate", 1]]
    assert "another plan" not in json.dumps(plan_cached["rates"]).lower()


@needs_text
def test_deductible_on_a_case_rate_stay_gives_the_excess(plan_cached):
    r = checks.run(_eob_case([_drg_line()]))
    c2 = [f for f in r.findings if f.kind == Kind.C2]
    assert len(c2) == 1 and c2[0].amount == Decimal("1900.00") and c2[0].severity == "high"
    assert (c2[0].math["rate"], c2[0].math["allowed"], c2[0].math["methodology"]) == ("6100.00", "8000.00", "case rate")
    assert "prices as of the hospital's publication date, May 27, 2026" in c2[0].detail
    c = _eob_case([_drg_line()])
    r = checks.run(c)
    name = next(v for v in verifier.subjects(r).values() if v.startswith("C2 #1"))
    assert verifier.check_findings(c, r).errors_for(name) == []


@needs_text
def test_coinsurance_or_a_percent_rate_is_borderline_without_a_sum(plan_cached):
    r = checks.run(_eob_case([_drg_line(share="1600.00")]))
    ask = [f for f in r.worth_asking if f.kind == Kind.C2]
    assert len(ask) == 1 and ask[0].amount is None and ask[0].math["amount_if_confirmed"] == "380.00"
    lab = Line(line_no=2, code="ZZ101", charges=Decimal("200.00"), adjustment=Decimal("-50.00"),
               patient_responsibility=Decimal("150.00"))
    r = checks.run(_eob_case([lab]))
    assert [f.severity for f in r.worth_asking if f.kind == Kind.C2] == ["borderline"]
    assert "estimate the hospital computes" in next(f for f in r.worth_asking if f.kind == Kind.C2).detail


@needs_text
def test_no_plan_rate_check_without_a_plan_or_insurance_or_when_allowed_is_at_the_rate(plan_cached):  # must differ
    assert not [f for f in checks.run(_eob_case([_drg_line()], plan=False)).findings if f.kind == Kind.C2]
    assert not [f for f in checks.run(_eob_case([_drg_line()], insured=False)).findings if f.kind == Kind.C2]
    at_rate = Line(line_no=1, code="DRG 901", charges=Decimal("15000.00"), adjustment=Decimal("-8900.00"),
                   patient_responsibility=Decimal("6100.00"))
    assert not [f for f in checks.run(_eob_case([at_rate])).findings + checks.run(_eob_case([at_rate])).worth_asking
                if f.kind == Kind.C2]


@pytest.mark.skipif(prices.load("memorial-2026-05") is None, reason="run scripts/fetch_prices.py first")
def test_the_demo_plans_real_case_rate_for_a_birth():
    idx = prices.load("memorial-2026-05")
    rates = idx.plan_rates("Aetna", "Qualified Health Plan", "MS-DRG", "807")
    assert len(rates) == 1 and rates[0].methodology == "case rate" and rates[0].dollar > 0


@pytest.mark.skipif(prices.load("memorial-2026-05") is None, reason="run scripts/fetch_prices.py first")
def test_demo_case_shows_the_plan_excess_from_the_real_rate():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("demo_price", Path(__file__).parents[1] / "scripts" / "demo_price.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    rate = prices.load("memorial-2026-05").plan_rates("Aetna", "Qualified Health Plan", "MS-DRG", "807")[0].dollar
    report = checks.run(demo.demo_case(rate))
    c2 = next(f for f in report.findings if f.kind == Kind.C2)
    assert c2.amount == Decimal("2000.00") and c2.math["rate"] == str(rate) and c2.math["plan_payer"] == "Aetna"
    assert "Example: a fictional family" in demo.NOTE
