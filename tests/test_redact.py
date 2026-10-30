"""Covering private values on the device. Every test checks both sides: the value is readable
before (so the check is not blind) and unreadable after. All text here is SYNTHETIC."""

from functools import cache

import pytest
from PIL import Image, ImageDraw, ImageFont

from billradar import redact
from billradar.redact import Secret, Vault, compact, leaks, ocr
from tests.fakes import FakeClient

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@cache
def font(size: int, bold: bool = False):
    name = "Arial Bold.ttf" if bold else "Arial.ttf"
    for base in ("/System/Library/Fonts/Supplemental/", "/Library/Fonts/", "/usr/share/fonts/truetype/dejavu/"):
        try:
            return ImageFont.truetype(base + name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def sheet(lines: list[str]) -> Image.Image:
    img = Image.new("RGB", (1275, 160 + 90 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    d.text((90, 60), "LAKEVIEW DEMO HOSPITAL - SAMPLE", font=font(36, True), fill="black")
    for i, t in enumerate(lines):
        d.text((90, 150 + 90 * i), t, font=font(28), fill="black")
    return img


PRIVATE = {
    "Patient: Anna Sample": ("PATIENT", "Anna Sample"),
    "Account: LDH-000123": ("ACCT", "LDH-000123"),
    "Member ID: XQZ123456789": ("MEMBER", "XQZ123456789"),
    "SSN: 123-45-6789": ("SSN", "123-45-6789"),
    "DOB 03/14/1994": ("DOB", "03/14/1994"),
    "Address: 100 Example Street, Hollywood FL 33021": ("ADDR", "100 Example Street, Hollywood FL 33021"),
}
KEEP = ["08/04/2025 59409 Delivery $2,887.50", "Patient responsibility $2,575.10", "Balance due $676.80"]


def text_of(img) -> str:
    return " ".join(compact(line.text) for line in ocr(img))


def test_private_values_are_readable_before_and_covered_after():
    img = sheet(list(PRIVATE) + KEEP)
    controls = [Secret(kind, value, "") for kind, value in PRIVATE.values()]
    assert leaks(img, controls) == controls  # must light: OCR reads every one of them before

    vault = Vault()
    r = redact.redact(img, vault)
    assert leaks(r.image, controls) == []
    assert {s.kind for s in r.secrets} >= {"PATIENT", "ACCT", "MEMBER", "SSN", "DOB", "ADDR"}

    after = text_of(r.image)
    for keep in ("2887.50", "59409", "08042025", "patientresponsibility2575.10", "676.80"):
        assert compact(keep) in after, keep  # dates of service, codes and amounts stay readable
    assert "acct1" in after  # the token is drawn where the account number was


def test_same_value_gets_the_same_token_on_every_sheet():
    vault = Vault()
    a = redact.redact(sheet(["Account: LDH-000123", "Total $10.00"]), vault)
    b = redact.redact(sheet(["Account #: LDH-000123", "Claim #: CL-55120"]), vault)
    token_a = next(s.token for s in a.secrets if s.kind == "ACCT")
    token_b = next(s.token for s in b.secrets if s.kind == "ACCT")
    assert token_a == token_b == "[ACCT_1]"
    assert any(s.token == "[CLAIM_1]" for s in b.secrets)


def test_names_the_family_gave_us_are_covered_anywhere():
    img = sheet(["Dear Anna Sample,", "Your baby Mia Sample was seen on 08/04/2025."])
    controls = [Secret("PATIENT", "Anna Sample", ""), Secret("PATIENT", "Mia Sample", "")]
    assert len(leaks(img, controls)) == 2
    r = redact.redact(img, Vault(known=[("PATIENT", "Anna Sample"), ("PATIENT", "Mia Sample")]))
    assert leaks(r.image, controls) == []
    assert "08042025" in text_of(r.image)


def test_headers_without_numbers_are_left_alone():
    img = sheet(["Account Summary", "Claim Totals", "Patient responsibility $40.00"])
    r = redact.redact(img, Vault())
    assert r.secrets == []


def test_what_sam_gets_cannot_be_read():
    img = sheet(list(PRIVATE) + KEEP)
    controls = [Secret(kind, value, "") for kind, value in PRIVATE.values()]
    big = img.resize((img.width * 2, img.height * 2))
    assert leaks(big, controls) == controls  # must light on the big photo
    small = redact.send_copy(big, max_side=900)
    assert leaks(small, controls) == []
    assert max(small.size) == 900
    checked = redact.send_copy(big, max_side=900, secrets=controls)  # the checked path never returns a leak
    assert leaks(checked, controls) == []


def _demo_module():
    from billradar import example  # the drawn table that used to live in scripts/demo_synthetic.py
    return example


def test_private_pipeline_sends_nothing_readable():
    import base64
    import io
    import json
    from datetime import date
    from pathlib import Path

    from billradar import pipeline
    from billradar.case import Profile
    from tests.fakes import FakeTable, reading_from_fixture

    demo = _demo_module()
    photo, quads = demo.table()
    fixture = json.loads((Path(__file__).parent / "fixtures/cases/anna-birth.json").read_text())
    people = [dict(p, name=n) for p, n in zip(demo.DEMO_PEOPLE, ["Anna Sample", "Baby Sample", "Mark Sample", "Leo Sample"])]
    prof = Profile.model_validate(fixture["profile"] | {"people": people, "local": demo.DEMO_LOCAL})
    controls = [Secret("PATIENT", "Anna Sample", ""), Secret("PATIENT", "Baby Sample", "")]

    original = Image.open(io.BytesIO(photo))
    assert leaks(original, controls), "must light: the names are readable on the original photo"

    fake = FakeTable(quads, demo.SIZE, reading=reading_from_fixture({"S6": "S5"}))
    client = FakeClient(fake)
    run = pipeline.run(client, [photo], [("portal.pdf", b"%PDF-1.4 fake")], prof, "memorial-2026-05",
                       "Lakeview Demo Hospital", date(2026, 6, 15), "p1", fictional=True, private=True)

    def images(body):
        for item in body["input"]:
            for part in item.get("content", []) if isinstance(item.get("content"), list) else []:
                if part.get("type") == "input_image":
                    yield Image.open(io.BytesIO(base64.b64decode(part["image_url"].split(",", 1)[1])))
                assert part.get("type") != "input_file", "no PDF may leave in private mode"

    # must light on the same channel: without private mode the names ARE readable on what the reader gets
    plain = FakeTable(quads, demo.SIZE, reading=reading_from_fixture({"S6": "S5"}))
    pipeline.run(FakeClient(plain), [photo], [], prof, "memorial-2026-05",
                 fictional=True)
    assert any(leaks(img, controls) for img in images(plain.read_bodies[0]))

    sent = [img for body in fake.sheet_bodies + fake.read_bodies for img in images(body)]
    assert len(sent) == 1 + 5
    for img in sent:
        assert leaks(img, controls) == []
    assert run.redacted and any("not sent" in p for p in run.problems)
    assert run.case.profile.local.tokens.get("PATIENT_1") == "Anna Sample"
    # the screen keeps the sheets as photographed (they never leave the device); the covered cuts went to the models
    assert set(run.originals) == {s.doc_id for s in run.sheets}
    assert any(leaks(img, controls) for img in run.originals.values())


def test_a_labelled_name_keeps_the_persons_token():
    vault = Vault(seed={("PATIENT", "Anna"): "[PATIENT_1]", ("PATIENT", "Mia"): "[PATIENT_2]"})
    r = redact.redact(sheet(["Patient: Anna Sample", "Member: Mia Sample", "Guarantor: Mark Sample"]), vault)
    tokens = {compact(s.value): s.token for s in r.secrets}  # OCR may drop spaces: "MarkSample"
    assert tokens["anna"] == "[PATIENT_1]" and tokens["mia"] == "[PATIENT_2]"
    assert tokens["marksample"] == "[PATIENT_3]"  # someone new gets the next free number


def test_screen_can_show_what_the_models_saw(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from billradar import app as app_module
    demo = _demo_module()
    monkeypatch.setattr(app_module, "RUNS", tmp_path)
    photo, quads = demo.table()
    import json
    from datetime import date
    from pathlib import Path

    from billradar import pipeline
    from billradar.case import Profile
    from tests.fakes import FakeTable, reading_from_fixture
    fixture = json.loads((Path(__file__).parent / "fixtures/cases/anna-birth.json").read_text())
    prof = Profile.model_validate(fixture["profile"] | {"people": demo.DEMO_PEOPLE, "local": demo.DEMO_LOCAL})
    fake = FakeTable(quads, demo.SIZE, reading=reading_from_fixture({"S6": "S5"}))
    run = pipeline.run(FakeClient(fake), [photo], [], prof, "memorial-2026-05",
                       "Lakeview Demo Hospital", date(2026, 6, 15), "v1", fictional=True, private=True)
    pipeline.save(run, tmp_path / "v1")
    html = TestClient(app_module.app).get("/cases/v1").text
    assert "What the models saw" in html and "/cases/v1/sent-sheet/S1.jpg" in html
    assert "hidden before anything leaves this device" in html
    sent = Image.open(tmp_path / "v1" / "sent-1.jpg")  # built from exactly what left the device
    assert leaks(sent, [Secret("PATIENT", "Anna Sample", ""), Secret("PATIENT", "Baby Sample", "")]) == []
    assert sent.width > sent.height  # the blurred photo plus one tile per sheet


def test_a_name_printed_last_name_first_is_covered_too():
    vault = Vault(seed={("PATIENT", "Anna Mia Sample"): "[PATIENT_1]"})
    img = sheet(["Statement for SAMPLE, ANNA MIA", "Guarantor copy: Sample,Anna", "Anna Sample visit 08/04/2025"])
    controls = [Secret("PATIENT", v, "") for v in ["Sample Anna Mia", "Sample Anna", "Anna Sample"]]
    assert leaks(img, controls) == controls  # must light: all three forms are readable before
    r = redact.redact(img, vault)
    assert leaks(r.image, controls) == []
    assert {s.token for s in r.secrets} == {"[PATIENT_1]"}  # one person, one token
    assert "08042025" in text_of(r.image)  # the date stays readable


def test_opencv_runs_sequentially_for_ocr():
    import cv2
    ocr(sheet(["Total $10.00"]))
    assert cv2.getNumThreads() == 1  # sequential: no parallel resize stripes
