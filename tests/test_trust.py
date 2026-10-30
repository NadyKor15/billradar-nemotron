"""Trust screens: the example case, "Is it safe?", deleting a case, the case history, who did what.

SYNTHETIC data only: the example is a fictional sample pack answered from its generator's files.
"""

import html as htmllib
import json
import re
import socket
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from billradar import app as app_module
from billradar import evaluation as ev
from billradar import example, history, view
from billradar.checks import Evidence, Finding, Kind
from billradar.policies import source
from tests.fakes import run_pack
from tests.test_app import fake_client

ROOT = Path(__file__).parents[1]
needs_text = pytest.mark.skipif(not all(source.is_cached(p) for p in source.SOURCES),
                                reason="run scripts/fetch_policies.py first")


@pytest.fixture(scope="session")
def example_runs(tmp_path_factory):
    """The example cases, built once per test session (the private one runs local OCR: slow)."""
    d = tmp_path_factory.mktemp("examples")
    if all(source.is_cached(p) for p in source.SOURCES):
        example.ensure(d)
    return d


@pytest.fixture
def client(tmp_path, monkeypatch, example_runs):
    import shutil
    shutil.copytree(example_runs, tmp_path, dirs_exist_ok=True)
    monkeypatch.setattr(app_module, "RUNS", tmp_path)
    monkeypatch.delenv("BILLRADAR_PUBLIC", raising=False)
    return TestClient(app_module.app)


def _no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the example must not touch the network")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _key():
    return ev.Key.model_validate_json((example.PACK / "key.json").read_text())


@needs_text
def test_example_is_replayed_offline_fast_and_matches_its_key(monkeypatch):
    _no_network(monkeypatch)
    t = time.monotonic()
    run = run_pack(example.PACK, example.EXAMPLE_ID)
    assert time.monotonic() - t < 90  # findings, letters and the screen offline; 18–24 s on default OCR threads, 32–34 s on 2 (a judge's slower machine)
    assert run.replayed and run.case.demo
    s = ev.score(_key(), ev.from_report(run.report))
    assert all(c.found == c.n and not c.false and not c.wrong_amount and not c.wrong_deadline
               for c in s.by_class.values()), ev.table({"example": s})
    assert s.by_class and not s.extra_false


@needs_text
def test_replay_script_still_gives_the_same_result():
    """scripts/replay_pack.py is now a wrapper around billradar.example; its table must still equal the key."""
    out = subprocess.run([sys.executable, "scripts/replay_pack.py", str(example.PACK)], cwd=ROOT,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    key = _key()
    for cls in {i.cls for i in key.items}:
        n = sum(1 for i in key.items if i.cls == cls)
        assert re.search(rf"\| {cls} \| {n} of {n} \|", out.stdout), out.stdout
    assert "false" not in out.stdout.lower() or "0 false" in out.stdout.lower()


@needs_text
def test_example_page_is_labelled_and_never_claims_covering_on_this_pack(client):
    page = htmllib.unescape(client.get("/example").text)
    assert "Example case — all data is made up" in page
    assert "Try with my bills" in page
    assert "Delete this case" not in page  # the example cannot be deleted
    # the models-saw view is the recorded run itself: each covered sheet as the model got it
    assert "/cases/example-private/sent-sheet/S1.jpg" in page
    assert "Photos and PDF pages leave the device like this" in page and "This example is that run." in page
    rows = page.count('class="frow ')
    report = json.loads((app_module.RUNS / "example" / "report.json").read_text())
    assert rows == len(report["findings"]) + len(report["worth_asking"]) + len(report["no_money"])
    # every finding row says which model found and read its sheets, and that it was a replay
    for block in page.split('class="frow ')[1:]:
        block = block.split("</article>")[0]
        assert 'class="model ' in block and ">replay<" in block


@needs_text
def test_safe_page_shows_what_left_the_device(client):
    page = htmllib.unescape(client.get("/safe").text)
    assert "What the models saw" in page and "sent-sheet/S1.jpg" in page  # the sheet as photographed and as sent
    assert client.get("/cases/example-private/overlay/sent-1.jpg").headers["content-type"] == "image/jpeg"
    assert "Open a case to delete it." in page


def test_how_page_names_every_model_and_its_status(client):
    page = htmllib.unescape(client.get("/how").text)
    for m in view.NEMOTRON_STATUS:
        assert m.model in page and m.status in page
    assert "You review and sign every letter." in page


def test_nemotron_build_names_only_nemotron_models(client):
    for path in ("/", "/how", "/safe", "/agents", "/upload", "/example", "/cases/example", "/cases/example-appeal",
                 "/.well-known/agent-card.json"):
        page = htmllib.unescape(client.get(path).text)
        words = re.findall(r"[a-z]+", re.sub(r"<meta\b|\bmetadata\b", " ", page.lower()))
        assert not {"meta", "muse", "spark", "wonder", "glimmer", "llama", "sam"} & set(words), path
    page = htmllib.unescape(client.get("/how").text)
    for m in view.NEMOTRON_STATUS:
        assert m.model in page


def _make_case(client, monkeypatch):
    monkeypatch.setattr(app_module.providers, "make_client", fake_client)
    from tests.fakes import QUADS, SIZE, table_photo
    r = client.post("/cases", data={
        "policy_id": "memorial-2026-05", "letterhead": "Lakeview Demo Hospital", "household_size": "4",
        "annual_income": "85000", "liquid_assets": "6000", "insured": "true", "mother_name": "Anna",
        "baby_name": "Baby", "fictional": "true"},
        files=[("photos", ("table.jpg", table_photo(QUADS, SIZE), "image/jpeg"))], follow_redirects=False)
    assert r.status_code == 303
    return re.fullmatch(r"/cases/(c[0-9a-f]{10})", r.headers["location"]).group(1), r


@needs_text
def test_delete_needs_the_cookie_on_a_public_deploy(client, monkeypatch, tmp_path):
    monkeypatch.setenv("BILLRADAR_PUBLIC", "1")
    case_id, r = _make_case(client, monkeypatch)
    cookie = r.cookies.get(history.cookie_name(case_id))
    assert cookie and "token_sha256" in (tmp_path / case_id / "owner.json").read_text()
    assert cookie not in (tmp_path / case_id / "owner.json").read_text()  # only a hash is kept

    stranger = TestClient(app_module.app)
    assert stranger.post(f"/cases/{case_id}/delete", follow_redirects=False).status_code == 403
    stranger.cookies.set(history.cookie_name(case_id), "not-the-token", path=f"/cases/{case_id}")
    assert stranger.post(f"/cases/{case_id}/delete", follow_redirects=False).status_code == 403
    assert (tmp_path / case_id).exists()

    owner = TestClient(app_module.app)
    owner.cookies.set(history.cookie_name(case_id), cookie, path=f"/cases/{case_id}")
    page = owner.get(f"/cases/{case_id}").text
    assert "Delete this case" in page
    d = owner.post(f"/cases/{case_id}/delete", follow_redirects=False)
    assert d.status_code == 303 and d.headers["location"] == f"/cases?deleted={case_id}"
    assert not (tmp_path / case_id).exists()
    assert owner.get(f"/cases/{case_id}").status_code == 404


def test_examples_can_never_be_deleted_and_odd_ids_are_404(client, tmp_path):
    runs = tmp_path / "other"
    for case_id in example.READ_ONLY:
        (runs / case_id).mkdir(parents=True)
        (runs / case_id / "case.json").write_text("{}")
        assert not history.delete(runs, case_id, None)
        assert (runs / case_id).exists()
    assert client.post("/cases/..%2F..%2Fetc/delete").status_code == 404
    assert client.post("/cases/nope/delete").status_code == 404
    assert not history.delete(runs, "../x", None)


@needs_text
def test_history_lists_runs_on_this_device_but_not_on_a_public_deploy(client, monkeypatch):
    case_id, _ = _make_case(client, monkeypatch)
    page = client.get("/cases").text
    assert f'data-id="{case_id}"' in page and "Anna&#39;s birth case" in page
    summary = client.get(f"/cases/{case_id}/summary.json").json()
    assert summary["case_id"] == case_id and summary["demo"] and summary["sheets"] == 5
    monkeypatch.setenv("BILLRADAR_PUBLIC", "1")
    assert f'data-id="{case_id}"' not in client.get("/cases").text  # strangers do not see other people's cases
    assert client.get("/cases/nope/summary.json").status_code == 404


def _finding(docs):
    return Finding(kind=Kind.E2, title="t", detail="d", evidence=[Evidence(doc_id=d) for d in docs])


def test_model_labels_come_from_the_run_not_hard_wired():
    ran = {"sheets": 1.0, "read": 2.0}
    f = _finding(["S1", "D1"])
    assert [t.label for t in view.models_for(f, {"replayed": True, "timings": ran})] == [
        "Nemotron 3.5 Super VL · found & read"]
    assert all(t.replayed for t in view.models_for(f, {"replayed": True, "timings": ran}))
    assert [t.label for t in view.models_for(_finding(["D1"]), {"timings": ran})] == [
        "Nemotron 3.5 Super VL · read"]  # a PDF: no photo
    other = {"provider": "other", "models": {"find": "Finder X", "read": "Reader Y"}, "replayed": False, "timings": ran}
    assert [t.label for t in view.models_for(f, other)] == ["Finder X", "Reader Y"]
    # a case whose papers were typed in (no photo, no model reading): no model is credited
    assert view.models_for(f, {"timings": {}}) == []
    # the reviewer appears only when it really answered
    assert "Nemotron 3 Ultra" not in [t.label for t in view.models_for(f, {"checker": {"issues": []}, "timings": ran})]
    reviewed = {"checker": {"issues": [{"what": "Nemotron-3-Ultra-550b-a55b: wording"}]}, "timings": ran}
    assert "Nemotron 3 Ultra" in [t.label for t in view.models_for(f, reviewed)]


def test_borderline_findings_show_no_amount():
    from decimal import Decimal

    from billradar.case import Case, Profile
    case = Case(case_id="x", policy_id="memorial-2026-05", today=date(2026, 7, 20),
                profile=Profile(household_size=2, annual_income=Decimal(1)), documents=[])
    f = _finding(["S1"]).model_copy(update={"amount": Decimal("312.00"), "severity": "borderline"})
    r = view.row(f, case, "memorial-2026-05", {})
    assert r.level == "worth checking" and "312" not in r.value and r.value == "No amount yet"
    r2 = view.row(f.model_copy(update={"severity": None}), case, "memorial-2026-05", {})
    assert r2.level == "serious" and r2.value == "$312.00"


def test_upload_without_live_calls_or_a_recording_says_so(client, monkeypatch):
    """A public deploy has no keys yet: a new upload must not fake a result."""
    from billradar.nemotron_client import NemotronClient
    from tests.fakes import QUADS, SIZE, table_photo
    monkeypatch.setattr(app_module.providers, "make_client", lambda: NemotronClient(record_to=None, journal=None))
    r = client.post("/cases", data={"policy_id": "memorial-2026-05", "household_size": "3", "annual_income": "50000",
                                    "fictional": "true"},
                    files=[("photos", ("t.jpg", table_photo(QUADS, SIZE), "image/jpeg"))], follow_redirects=False)
    assert r.status_code == 503
    assert "starts when the model calls are switched on" in r.text and 'href="/example"' in r.text


@needs_text
def test_letter_page_lists_letters_and_marks_quotes(client):
    page = htmllib.unescape(client.get("/example").text)
    kind = re.search(r'href="/cases/example/letters/([a-z-]+)"', page).group(1)
    letter = htmllib.unescape(client.get(f"/cases/example/letters/{kind}").text)
    assert "Letters and appeal" in letter and 'id="copy"' in letter and 'id="save"' in letter
    assert "SAMPLE — FICTIONAL DATA" in letter
    kinds = set(re.findall(r'href="/cases/example/letters/([a-z0-9-]+)[?"]', page))
    calls = letter.count('class="lthumb call"')
    assert letter.count('class="lcard') - calls == len(kinds) and calls >= 1



def test_agents_entry_is_mounted_after_the_screens(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "RUNS", tmp_path)
    with TestClient(app_module.app) as c:  # runs the lifespan once, as in production
        assert c.get("/agents/healthz").json()["mcp"] == "/mcp"
        card = c.get("/.well-known/agent-card.json")
        assert card.status_code == 200 and "skills" in card.text
        page = c.get("/agents").text
        assert "For AI agents" in page and "/mcp" in page and "brs1-birth-all-errors" in page
        assert c.get("/how").status_code == 200  # the screens still win over the mount


def test_example_receipt_on_nemotron(client, monkeypatch):
    page = htmllib.unescape(client.get("/cases/example").text)
    assert 'id="receipt"' in page and "when this run was live" in page
    assert "Nemotron 3.5 Super VL" in page and "Nemotron 3 Ultra" in page


def test_pdf_files_show_their_first_page(client, monkeypatch):
    page = client.get("/cases/example").text
    assert "/cases/example/file/D1.jpg" in page
    r = client.get("/cases/example/file/D1.jpg")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert client.get("/cases/example/file/..%2Fcase.json").status_code == 404


def test_example_second_read_replays(client, monkeypatch, tmp_path):
    """Changing a letter or a finding changes the review request; then its recording must be made again
    (one live Ultra call per example run), or the example silently loses the second read."""
    import json as _json
    client.get("/cases/example")
    for case_id in ("example", "example-private"):
        checker = _json.loads((app_module.RUNS / case_id / "problems.json").read_text())["checker"]
        assert not [i for i in checker["issues"] if "review did not run" in i["what"]], case_id


def test_one_chip_when_one_model_finds_and_reads():
    ran = {"sheets": 1.0, "read": 2.0}
    info = {"provider": "nebius", "replayed": True, "timings": ran}
    labels = [t.label for t in view.models_for(_finding(["S1"]), info)]
    assert labels == ["Nemotron 3.5 Super VL · found & read"]
