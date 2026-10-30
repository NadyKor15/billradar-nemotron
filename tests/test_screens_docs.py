"""Screens for letters in three languages, Copy/Save/PDF, the PDF report, the call cheat sheet and the voice note.

The example with a denied claim is SYNTHETIC (tests/fixtures/cases/sample-appeal.json). No live model calls:
a translation or a voice note without a recording must say so, never show something made up.
"""

import html as htmllib
import io
import re
import wave

import pytest
from fastapi.testclient import TestClient

from billradar import app as app_module
from billradar.model_api import ReplayMissing
from billradar.policies import source
from tests.test_trust import example_runs  # noqa: F401  (session fixture)

needs_text = pytest.mark.skipif(not all(source.is_cached(p) for p in source.SOURCES),
                                reason="run scripts/fetch_policies.py first")


@pytest.fixture
def client(tmp_path, monkeypatch, example_runs):
    import shutil
    shutil.copytree(example_runs, tmp_path, dirs_exist_ok=True)
    monkeypatch.setattr(app_module, "RUNS", tmp_path)
    monkeypatch.delenv("BILLRADAR_PUBLIC", raising=False)
    monkeypatch.delenv("BILLRADAR_LIVE", raising=False)
    return TestClient(app_module.app)


def _appeal_kind(client) -> str:
    page = client.get("/cases/example-appeal").text
    return re.search(r'/cases/example-appeal/letters/(appeal-[a-z0-9-]+)"', page).group(1)


@needs_text
def test_case_shows_appeal_call_sheets_and_pdf_report(client):
    page = htmllib.unescape(client.get("/cases/example-appeal").text)
    assert "Insurance appeal" in page and "Before you call" not in page
    assert page.count("/cases/example-appeal/call/") == 2  # the hospital and the insurer
    for lang in ("en", "ru", "es"):
        assert f"/cases/example-appeal/report.pdf?lang={lang}" in page
    assert client.get("/cases/example-appeal/report.pdf?lang=ru").content.startswith(b"%PDF-")
    assert client.get("/cases/example-appeal/report.pdf?lang=xx").status_code == 400


@needs_text
def test_appeal_letter_shows_deadline_and_never_fakes_a_translation(client, monkeypatch):
    kind = _appeal_kind(client)
    en = htmllib.unescape(client.get(f"/cases/example-appeal/letters/{kind}").text)
    assert "Deadline:" in en and "days from denial" in en and "Stops before Submit" in en
    assert "Plan document p." in en  # quotes from her plan, with the page
    assert '<span class="quoted">“' in en  # only the quoted words are marked
    ru = htmllib.unescape(client.get(f"/cases/example-appeal/letters/{kind}?lang=ru").text)
    # the recorded translation is for understanding: the page says the English letter is the one sent
    assert "Отправляется письмо на английском" in ru and app_module.NOT_LIVE not in ru

    def no_recording(*a, **k):
        raise ReplayMissing("no recording for this request")

    # no recording of a translation: the page says so and shows the English letter only
    monkeypatch.setattr(app_module.i18n, "letter", no_recording)
    ru = htmllib.unescape(client.get(f"/cases/example-appeal/letters/{kind}?lang=ru").text)
    assert app_module.NOT_LIVE in ru and "letter translation" not in ru
    assert client.get(f"/cases/example-appeal/letters/{kind}?lang=de").status_code == 400


@needs_text
def test_copy_save_and_pdf_give_the_letter_to_send(client):
    kind = _appeal_kind(client)
    text = client.get(f"/cases/example-appeal/letters/{kind}/text").text
    assert text.startswith("SAMPLE - FICTIONAL DATA") and "Sincerely," in text
    page = client.get(f"/cases/example-appeal/letters/{kind}").text
    assert f'data-src="/cases/example-appeal/letters/{kind}/text?lang=en"' in page  # Copy fetches the same text
    assert client.get(f"/cases/example-appeal/letters/{kind}/pdf").content.startswith(b"%PDF-")


@needs_text
def test_call_cheat_sheets_and_the_voice_note_without_a_recording(client):
    for to, who in (("hospital", "Lakeview Demo Hospital"), ("insurer", "Sample Health Plan")):
        page = htmllib.unescape(client.get(f"/cases/example-appeal/call/{to}").text)
        assert f"Before you call {who}" in page and "Say this first" in page and "Ask for" in page
    assert client.get("/cases/example-appeal/call/dentist").status_code == 404
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(b"\0\0" * 16000)
    r = client.post("/cases/example-appeal/call/hospital/note?lang=en", content=buf.getvalue())
    assert r.status_code == 400 and "no speech model" in r.json()["error"]  # never a made-up transcript
    assert client.post("/cases/example-appeal/call/hospital/note?lang=ru", content=buf.getvalue()).status_code == 400
    assert client.post("/cases/example-appeal/call/hospital/note?lang=en", content=b"not a wav").status_code in (400, 503)


@needs_text
def test_appeal_tracker_owner_marks_sent_examples_are_read_only(client, monkeypatch, tmp_path):
    page = htmllib.unescape(client.get("/cases/example-appeal/appeals").text)
    assert "Track your appeals and letters" in page and "last day to appeal" in page
    assert "Examples are read-only" in page
    key = re.search(r'href="/cases/[^"?]+/appeals\?key=(appeal-[a-z0-9-]+)"', page).group(1)  # absolute: the static copy crawls it
    assert client.post(f"/cases/example-appeal/appeals/{key}/sent", data={"on": "2026-10-28"},
                       follow_redirects=False).status_code == 403
    ics = client.get("/cases/example-appeal/appeals.ics").text
    assert "Last day to appeal" in ics and "TRIGGER:-P7D" in ics

    # her own case (made on this device): she marks it sent; the tracker counts the days
    import shutil
    shutil.copytree(tmp_path / "example-appeal", tmp_path / "mine")
    r = client.post(f"/cases/mine/appeals/{key}/sent", data={"on": "2026-10-20", "confirmation": "A-123"},
                    follow_redirects=False)
    assert r.status_code == 303
    after = htmllib.unescape(client.get(f"/cases/mine/appeals?key={key}").text)
    assert "A-123" in after and "Got a reply? Take a photo" in after
    monkeypatch.setenv("BILLRADAR_PUBLIC", "1")  # on a public deploy a stranger cannot change it
    assert client.post(f"/cases/mine/appeals/{key}/sent", data={"on": "2026-10-21"},
                       follow_redirects=False).status_code == 403


@needs_text
def test_findings_show_confidence_and_plain_lines_and_ask_never_fakes_an_answer(client):
    page = htmllib.unescape(client.get("/example").text)
    assert "% sure" in page  # the checker's confidence on findings
    assert "What these lines mean" in page and "Your share" in page
    assert 'id="ask"' in page
    r = client.post("/cases/example/ask", json={"question": "Why is the baby's bill on my insurance?"})
    assert r.status_code == 200
    j = r.json()
    if j["reason"] == "no_model":  # no live call and no recording: says so, no made-up answer, no citations
        assert j["refused"] and "not connected" in j["text"] and j["citations"] == []
    assert client.post("/cases/example/ask", json={"question": ""}).status_code == 400
