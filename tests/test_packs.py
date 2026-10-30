"""The look-alike sample packs, read by a perfect reader (the generator's own answers), through the checks.

Each rule fixed after the first run on these packs has a check here, on the pack where it went wrong:
F1 only on the hospital's bills, F2 counted from the hospital's papers, E4 only when one part of a sheet
says nothing is owed, E2 with the amount from the insurance statement. SYNTHETIC data only."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from billradar import checks
from billradar import evaluation as ev
from billradar.case import Case, Profile, Source
from billradar.checks import Kind
from billradar.policies import source
from billradar.reader import to_documents

PACKS = Path(__file__).parents[1] / "eval" / "packs" / "work"
needs_text = pytest.mark.skipif(not all(source.is_cached(p) for p in source.SOURCES),
                                reason="run scripts/fetch_policies.py first")


def case_of(pack: str) -> tuple[Case, ev.Key]:
    d = PACKS / pack
    info = json.loads((d / "profile.json").read_text())
    reading = json.loads((d / "answers" / "reading.json").read_text())
    expected = {x["doc_id"]: (Source.PDF if x["doc_id"].startswith("D") else Source.PAPER) for x in reading["documents"]}
    docs, problems = to_documents(reading, expected)
    assert problems == []
    case = Case(case_id=pack, letterhead=info["letterhead"], policy_id=info["policy_id"],
                today=date.fromisoformat(info["today"]), profile=Profile.model_validate(info["profile"]), documents=docs)
    return case, ev.Key.model_validate_json((d / "key.json").read_text())


def scored(pack: str):
    case, key = case_of(pack)
    report = checks.run(case)
    return report, ev.score(key, ev.from_report(report))


@needs_text
@pytest.mark.parametrize("pack", ["brs1-birth-all-errors", "brs1-birth-newborn-duplicate", "brs1-birth-clean-control"])
def test_perfect_reading_finds_the_key_and_nothing_else(pack):
    report, s = scored(pack)
    assert report.held_back == []
    for cls, cs in s.by_class.items():
        assert (cs.found, cs.false, cs.wrong_amount, cs.no_amount, cs.wrong_deadline) == (cs.n, 0, 0, 0, 0), cls


@needs_text
def test_f1_only_on_the_hospitals_bills_and_doctors_get_a_note():
    report, _ = scored("brs1-birth-all-errors")
    f1 = [f for f in report.findings if f.kind == Kind.F1]
    assert [e.doc_id for f in f1 for e in f.evidence] == ["D1"]
    note = next(n for n in report.no_money if "Doctors' bills" in n.title)
    assert {e.doc_id for e in note.evidence} >= {"S8", "S9"}  # pediatric and anesthesia offices


@needs_text
def test_window_counts_from_the_hospitals_papers():
    report, _ = scored("brs1-birth-all-errors")
    (f2,) = [f for f in report.findings if f.kind == Kind.F2]
    assert f2.evidence[0].doc_id == "D1" and f2.deadline == date(2026, 4, 1)


@needs_text
def test_ledger_subtotals_are_not_two_amounts():
    report, _ = scored("brs1-birth-clean-control")
    assert not [f for f in report.findings if f.kind == Kind.E4]
    report, _ = scored("brs1-birth-all-errors")
    assert [f.evidence[0].doc_id for f in report.findings if f.kind == Kind.E4] == ["S13"]


@needs_text
def test_duplicate_takes_its_amount_from_the_insurance_statement():
    report, _ = scored("brs1-birth-all-errors")
    (e2,) = [f for f in report.findings if f.kind == Kind.E2]
    assert e2.amount == Decimal("196.82") and e2.math["amount_from"] == "S1"
