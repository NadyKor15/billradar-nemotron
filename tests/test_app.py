import html as htmllib
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from billradar import app as app_module
from billradar import pipeline
from billradar.policies import source
from tests.fakes import QUADS, SIZE, FakeClient, FakeTable, reading_from_fixture, table_photo
from tests.test_pipeline import profile

needs_text = pytest.mark.skipif(not all(source.is_cached(p) for p in source.SOURCES),
                                reason="run scripts/fetch_policies.py first")


def fake_client():
    return FakeClient(FakeTable(QUADS, SIZE, reading=reading_from_fixture({"S6": "S5"})))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "RUNS", tmp_path)
    return TestClient(app_module.app)


@needs_text
def test_case_screen_shows_findings_with_their_evidence(client, tmp_path):
    prof = profile()
    for p in prof.people:
        p.name = {"mother": "Anna", "newborn": "Baby"}[p.role.value]
    run = pipeline.run(fake_client(), [table_photo(QUADS, SIZE)], [], prof, "memorial-2026-05",
                       "Lakeview Demo Hospital", date(2026, 6, 15), "t1")
    pipeline.save(run, tmp_path / "t1")

    html = htmllib.unescape(client.get("/cases/t1").text)
    assert '<span class="gold">$1,100</span><br>may come off your bills.' in html  # whole dollars, rounded down
    assert "Apply by Sep 2, 2026" in html
    assert "<title>Anna's birth case · BillRadar</title>" in html and "Lakeview Demo Hospital" in html
    assert html.count('class="frow serious"') == 3
    assert 'data-kind="F3"' in html and "$1,054.00" in html and "Not applied yet" in html
    assert "Policy p.11" in html and "Federal rule" in html  # chips point to verified quotes
    assert "Checked — no money here" in html
    assert ">replay<" in html  # synthetic answers are labelled on screen
    assert client.get("/cases/t1/overlay/overlay-1.jpg").headers["content-type"] == "image/jpeg"


def test_unknown_or_odd_case_ids_are_404(client):
    assert client.get("/cases/nope").status_code == 404
    assert client.get("/cases/..%2F..%2Fetc").status_code == 404
    assert client.get("/cases/t1/overlay/..%2Fcase.json").status_code == 404


def test_upload_form_lists_hospital_policies_only(client):
    html = client.get("/upload").text
    assert "memorial-2026-05" in html and "baptist-2026-09" in html
    assert "federal-501r" not in html


@needs_text
def test_upload_runs_the_pipeline(client, monkeypatch):
    monkeypatch.setattr(app_module.providers, "make_client", fake_client)
    r = client.post("/cases", data={
        "policy_id": "memorial-2026-05", "letterhead": "Lakeview Demo Hospital", "household_size": "4",
        "annual_income": "85000", "liquid_assets": "6000", "insured": "true", "mother_name": "Anna",
        "baby_name": "Baby"}, files=[("photos", ("table.jpg", table_photo(QUADS, SIZE), "image/jpeg"))],
        follow_redirects=False)
    assert r.status_code == 303
    case_id = re.fullmatch(r"/cases/(c[0-9a-f]{10})", r.headers["location"]).group(1)
    html = client.get(f"/cases/{case_id}").text
    assert "may come off your bills" in html
