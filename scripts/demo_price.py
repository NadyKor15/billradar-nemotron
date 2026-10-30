"""Build runs/demo-price: a FICTIONAL insured family checked against a REAL plan's published rate.

Compare with a real plan as if it were the family's plan. The family, the hospital
statement and the insurance statement here are made up (SAMPLE); the plan (Aetna Qualified Health Plan) and its
case rate for a vaginal delivery (MS-DRG 807) come from the policy hospital's public price file, read from
.cache/mrf (run scripts/fetch_prices.py first). No Aetna document is imitated.

python scripts/demo_price.py && uvicorn billradar.app:app  ->  /cases/demo-price
"""

from __future__ import annotations

import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from billradar import checks, pipeline, prices  # noqa: E402
from billradar.case import Case, DocKind, Document, Line, Person, Plan, Profile, Role, Source  # noqa: E402

PLAN = Plan(payer="Aetna", plan_name="Qualified Health Plan")
NOTE = "Example: a fictional family, with the real Aetna Qualified Health Plan rates from {hospital}'s public price file."


def demo_case(rate: Decimal) -> Case:
    """Insurance statement for the birth stay: the plan allowed $2,000.00 more than its published case rate,
    and a high-deductible plan left all of the allowed amount to the family."""
    allowed = rate + Decimal("2000.00")
    charges = Decimal("21480.00")
    eob = Document(
        doc_id="S1", source=Source.PAPER, kind=DocKind.EOB, patient="PATIENT_1", provider="Lakeview Demo Hospital",
        claim_ref="CLM-SAMPLE-1", statement_date=date(2026, 3, 9), service_from=date(2026, 2, 16),
        service_to=date(2026, 2, 18),
        lines=[Line(line_no=1, service_date=date(2026, 2, 16), code="DRG 807",
                    description="Inpatient stay, vaginal delivery (SAMPLE)", charges=charges,
                    adjustment=-(charges - allowed), insurance_paid=Decimal("0.00"), patient_responsibility=allowed,
                    claim_ref="CLM-SAMPLE-1")])
    bill = Document(
        doc_id="S2", source=Source.PAPER, kind=DocKind.HOSPITAL_STATEMENT, patient="PATIENT_1",
        provider="Lakeview Demo Hospital", account_ref="ACCT_1", statement_date=date(2026, 3, 20),
        service_from=date(2026, 2, 16), service_to=date(2026, 2, 18), first_after_discharge=True,
        patient_balance=allowed, total_charges=charges)
    people = [Person(token="PATIENT_1", role=Role.MOTHER, name="Sample Parent"),
              Person(token="PATIENT_2", role=Role.NEWBORN, name="Sample Baby")]
    return Case(case_id="demo-price", letterhead="Lakeview Demo Hospital", policy_id="memorial-2026-05",
                today=date(2026, 4, 1), demo=True,
                profile=Profile(household_size=3, annual_income=Decimal(158000), insured=True, plan=PLAN,
                                people=people, context=["birth", "hdhp"]),
                documents=[eob, bill])


def main() -> int:
    index = prices.load("memorial-2026-05")
    if index is None:
        print("no price index: run scripts/fetch_prices.py first")
        return 1
    rates = index.plan_rates(PLAN.payer, PLAN.plan_name, "MS-DRG", "807")
    if len(rates) != 1:
        print(f"expected one published rate for DRG 807, found {len(rates)}")
        return 1
    case = demo_case(rates[0].dollar)
    note = NOTE.format(hospital=index.hospital)
    report, verdict = pipeline.check_and_hold(case, checks.run(case))
    out = ROOT / "runs" / "demo-price"
    out.mkdir(parents=True, exist_ok=True)
    (out / "case.json").write_text(case.model_dump_json(indent=2))
    (out / "report.json").write_text(report.model_dump_json(indent=2))
    (out / "problems.json").write_text(json.dumps({
        "problems": [], "timings": {}, "replayed": True, "redacted": False, "note": note,
        "checker": {"checked": verdict.checked, "ok": verdict.ok, "issues": [i.__dict__ for i in verdict.issues]},
    }, indent=2))
    for f in report.findings + report.worth_asking:
        print(f"{f.kind.value} {f.severity} {f.amount}: {f.title}")
    print(f"total ${report.total_may_come_off} · {note}\nsaved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
