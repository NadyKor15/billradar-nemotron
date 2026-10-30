"""Pages of one statement as a live model reads them. The packs are SYNTHETIC; the fields come from live
Nemotron reads of the BRS1 sample packs (tests/fixtures/live-reads)."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from billradar import sheet_checks
from billradar.case import Case, DocKind, Document, Person, Profile, Role, Source

READS = json.loads((Path(__file__).parent / "fixtures" / "live-reads" / "nemotron-2026-10-27.json").read_text())["reads"]
EXPECTED_P1 = {  # from each pack's key.json: the insurance statement's page 3 of 4 is not in the pack
    "brs1-birth-all-errors": [("3", ["S1", "S2", "S3"])],
    "brs1-birth-newborn-duplicate": [("3", ["S1", "S2", "S3"])],
    "brs1-birth-clean-control": [],
}


def case_from(read: dict) -> Case:
    docs = [Document(**{k: v for k, v in d.items() if v is not None}) for d in read["documents"]]
    people = [Person(token=p["token"], role=Role(p["role"]), name=p["name"]) for p in read["people"]]
    return Case(case_id="live", policy_id="memorial-2026-05", today=date(2025, 7, 1),
                profile=Profile(household_size=3, annual_income=Decimal(100000), people=people), documents=docs)


@pytest.mark.parametrize("run", sorted(READS))
def test_missing_pages_on_live_reads_match_the_key(run):
    pack = run.split("/")[1]
    found = [(f.math["missing"], [e.doc_id for e in f.evidence]) for f in sheet_checks.missing_pages(case_from(READS[run]))]
    assert found == EXPECTED_P1[pack]


def test_the_old_grouping_was_wrong_on_the_same_reads():  # must differ: the reads really do vary page to page
    docs = case_from(READS["live-1t/brs1-birth-all-errors"]).documents
    eob = [d for d in docs if d.doc_id in ("S1", "S2", "S3")]
    assert len({(d.kind, d.provider) for d in eob}) == 3


def _sheet(doc_id, kind, page, count, source=Source.PAPER):
    return Document(doc_id=doc_id, source=source, kind=kind, page_no=page, page_count=count)


def test_runs_break_on_a_new_statement_and_on_a_different_kind():
    c = case_from({"people": [], "documents": []})
    c.documents = [_sheet("S1", DocKind.HOSPITAL_STATEMENT, 1, 2), _sheet("S2", DocKind.HOSPITAL_STATEMENT, 2, 2),
                   _sheet("S3", DocKind.HOSPITAL_STATEMENT, 1, 2), _sheet("S4", DocKind.EOB, 2, 2),
                   _sheet("D1", DocKind.ITEMIZED_BILL, 1, 4, Source.PDF)]
    found = [(f.math["missing"], [e.doc_id for e in f.evidence]) for f in sheet_checks.missing_pages(c)]
    assert found == [("2", ["S3"]), ("1", ["S4"])]  # the PDF's "Page 1 of 4" footer is not a missing page

