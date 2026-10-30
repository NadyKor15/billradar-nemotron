"""The example case ("See an example", "Try it yourself"): fictional papers, replayed answers, no keys.

"example" and "example-private" are both a replay of a real run: the pack below in private mode, read by
Nemotron 3.5 Super VL on Nebius Token Factory and reviewed by Nemotron 3 Ultra (BILLRADAR_SECOND_READ=1),
recorded in tests/fixtures/recordings-nebius. A request with no recording is an error, never faked.

  example-appeal   a claim the fictional Sample Health Plan denied (tests/fixtures/cases/sample-appeal.json):
                   the insurance appeal, the insurer's call cheat sheet and the appeal tracker. No model call.

The appeal example and the drawn demo table take their fictional people from tests/, which is why the repo
root must be importable (it is when the app runs from the repo).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from billradar import pipeline, providers
from billradar.case import Profile
from billradar.nemotron_client import NemotronClient, ReplayTransport

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "eval" / "packs" / "work" / "brs1-birth-all-errors"
EXAMPLE_ID = "example"
PRIVATE_ID = "example-private"
APPEAL_ID = "example-appeal"
READ_ONLY = {EXAMPLE_ID, PRIVATE_ID, APPEAL_ID}


def _tests_importable():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def run_recorded(pack: Path, case_id: str) -> pipeline.CaseRun:
    """The Nemotron example: the recorded live run, replayed. A request with no recording is an error, never faked."""
    info = json.loads((pack / "profile.json").read_text())
    photos = sorted(p for p in pack.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    client = NemotronClient(transport=ReplayTransport(), record_to=None, journal=None)
    review = os.environ.get(pipeline.SECOND_READ)
    os.environ[pipeline.SECOND_READ] = "1"  # the run was recorded with the second read on
    try:
        return _run(client, pack, info, photos, case_id, private=True)
    finally:
        if review is None:
            os.environ.pop(pipeline.SECOND_READ, None)
        else:
            os.environ[pipeline.SECOND_READ] = review


def _run(client, pack: Path, info: dict, photo_paths: list[Path], case_id: str | None, private: bool) -> pipeline.CaseRun:
    photos = [p.read_bytes() for p in photo_paths]
    pdfs = [(p.name, p.read_bytes()) for p in sorted(pack.glob("*.pdf"))]
    run = pipeline.run(client, photos, pdfs, Profile.model_validate(info["profile"]), info["policy_id"],
                       info.get("letterhead", ""), date.fromisoformat(info["today"]), case_id or pack.name,
                       fictional=bool(info.get("fictional")), private=private)
    run.case.demo = True
    return run


# ---- the drawn 5-sheet table (was scripts/demo_synthetic.py) ----

SIZE = (2400, 1700)
SHEETS = [  # (center x, center y, angle) for 5 letter-size sheets, plus what is printed on each
    ((480, 450, -6), "PATIENT STATEMENT", "Patient: Anna Sample", "Balance due $676.80"),
    ((1180, 430, 4), "PATIENT STATEMENT", "Patient: Baby Sample", "Balance due $1,240.00"),
    ((1880, 460, -3), "FIRST STATEMENT AFTER DISCHARGE", "Patient: Anna Sample", "Insurance pending"),
    ((760, 1250, 5), "EXPLANATION OF BENEFITS", "THIS IS NOT A BILL", "Member: Anna Sample"),
    ((1560, 1240, -4), "EXPLANATION OF BENEFITS", "THIS IS NOT A BILL", "Member: Baby Sample"),
]
W, H = 520, 672
DEMO_PEOPLE = [  # fictional family; stays local, the model only sees PATIENT_1 / PATIENT_2
    {"token": "PATIENT_1", "role": "mother", "name": "Anna", "birth_date": "03/14/1994", "relationship": "Self",
     "employer": "Sample Bakery LLC"},
    {"token": "PATIENT_2", "role": "newborn", "name": "Baby", "birth_date": "08/04/2025", "relationship": "Daughter"},
    {"token": "P3", "role": "other", "name": "Mark", "birth_date": "07/02/1990", "relationship": "Spouse",
     "employer": "Sample Auto Repair"},
    {"token": "P4", "role": "other", "name": "Leo", "birth_date": "09/30/2023", "relationship": "Son"},
]
DEMO_LOCAL = {"street": "100 Example Street", "city": "Hollywood", "state": "FL", "zip": "33021",
              "phone": "(555) 010-0000", "rent": "1850", "electricity": "140", "phone_bill": "60",
              "checking": "2400", "savings": "3600", "tokens": {"ACCT_1": "LDH-000123", "ACCT_2": "LDH-000124"}}


def font(size: int, bold: bool = False):
    for name in (["Arial Bold.ttf", "Helvetica-Bold.ttf"] if bold else ["Arial.ttf", "Helvetica.ttc"]):
        for base in ("/System/Library/Fonts/Supplemental/", "/System/Library/Fonts/", "/Library/Fonts/"):
            try:
                return ImageFont.truetype(base + name, size)
            except OSError:
                continue
    return ImageFont.load_default()


def draw_sheet(title: str, who: str, last: str) -> Image.Image:
    s = Image.new("RGBA", (W, H), (250, 249, 244, 255))
    d = ImageDraw.Draw(s)
    d.text((28, 24), "LAKEVIEW DEMO HOSPITAL", font=font(22, True), fill=(30, 60, 70))
    d.text((28, 54), title, font=font(17, True), fill=(40, 40, 40))
    d.text((28, 84), who, font=font(15), fill=(60, 60, 60))
    for i in range(14):
        y = 130 + i * 30
        d.line((28, y, W - 120, y), fill=(150, 150, 150), width=2)
        d.text((W - 105, y - 10), f"{(i * 137) % 900 + 40}.00", font=font(13), fill=(90, 90, 90))
    d.text((28, H - 100), last, font=font(18, True), fill=(30, 30, 30))
    d.text((W // 2 - 120, H // 2 - 40), "SAMPLE", font=font(64, True), fill=(220, 60, 60, 90))
    return s


def table() -> tuple[bytes, list[np.ndarray]]:
    _tests_importable()
    from tests.fakes import sheet_quad
    rng = np.random.default_rng(3)
    wood = np.zeros((SIZE[1], SIZE[0], 3), dtype=np.float32)
    grain = np.sin(np.linspace(0, 60, SIZE[1]))[:, None] * 10 + rng.normal(0, 6, (SIZE[1], SIZE[0]))
    wood[..., 0], wood[..., 1], wood[..., 2] = 132 + grain, 92 + grain * 0.8, 58 + grain * 0.6
    bg = Image.fromarray(np.clip(wood, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
    quads = []
    for (cx, cy, ang), *text in SHEETS:
        sheet = draw_sheet(*text).rotate(-ang, expand=True, resample=Image.Resampling.BICUBIC)
        shadow = Image.new("RGBA", sheet.size, (0, 0, 0, 0))
        shadow.paste((0, 0, 0, 70), mask=sheet.split()[3])
        bg.paste(shadow.filter(ImageFilter.GaussianBlur(10)), (int(cx - sheet.width / 2 + 10), int(cy - sheet.height / 2 + 14)), shadow.filter(ImageFilter.GaussianBlur(10)))
        bg.paste(sheet, (int(cx - sheet.width / 2), int(cy - sheet.height / 2)), sheet)
        quads.append(sheet_quad(cx, cy, W, H, ang))
    buf = io.BytesIO()
    bg.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), quads


def run_demo_synthetic(case_id: str = "demo-synthetic") -> pipeline.CaseRun:
    _tests_importable()
    from tests.fakes import FakeClient, FakeTable, reading_from_fixture

    photo, quads = table()
    fixture = json.loads((ROOT / "tests/fixtures/cases/anna-birth.json").read_text())
    prof = Profile.model_validate(fixture["profile"] | {"people": DEMO_PEOPLE, "local": DEMO_LOCAL})
    client = FakeClient(FakeTable(quads, SIZE, reading_from_fixture({"S6": "S5"})))
    run = pipeline.run(client, [photo], [], prof, fixture["policy_id"], fixture["letterhead"],
                       date.fromisoformat(fixture["today"]), case_id, fictional=True, private=True)
    run.case.demo = True
    return run


def run_appeal_example(case_id: str = APPEAL_ID) -> pipeline.CaseRun:
    """A case with no photos: the family's papers as already read (SYNTHETIC), checked like any other."""
    _tests_importable()
    from billradar import checks
    from billradar.case import Case
    from tests.test_appeal import LOCAL, PEOPLE

    raw = json.loads((ROOT / "tests/fixtures/cases/sample-appeal.json").read_text())
    raw.pop("_note", None)
    raw["profile"]["people"], raw["profile"]["local"] = PEOPLE, LOCAL
    case = Case.model_validate(raw | {"case_id": case_id, "demo": True})
    report, verdict = pipeline.check_and_hold(case, checks.run(case))
    return pipeline.CaseRun(case, report, [], [], verdict=verdict)


def _save_once(runs: Path, case_id: str, build) -> None:
    out = runs / case_id
    if (out / "case.json").exists():
        built_on = json.loads((out / "problems.json").read_text()).get("provider", "") if (out / "problems.json").exists() else ""
        if built_on == providers.name() or case_id == APPEAL_ID:  # the appeal example calls no model
            return
        shutil.rmtree(out)  # built by an older build: build again
    tmp = runs / f".build-{case_id}-{os.getpid()}"
    pipeline.save(build(), tmp)
    try:
        tmp.rename(out)  # another request may have finished first; then ours is dropped
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure(runs: Path) -> None:
    """Build the two example runs once; later calls only check they are there."""
    runs.mkdir(parents=True, exist_ok=True)
    _save_once(runs, EXAMPLE_ID, lambda: run_recorded(PACK, EXAMPLE_ID))
    _save_once(runs, PRIVATE_ID, lambda: run_recorded(PACK, PRIVATE_ID))
    _save_once(runs, APPEAL_ID, lambda: run_appeal_example(APPEAL_ID))
