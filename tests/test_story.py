"""The scroll story on the home page: the example case's own numbers, and every end state shown without JS."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from billradar import app as app_module

STATIC = Path(app_module.__file__).parent / "web" / "static"


def home() -> str:
    r = TestClient(app_module.app).get("/")
    assert r.status_code == 200
    return r.text


def test_story_tells_the_example_case():
    t = home()
    assert t.count('class="ep ep') == 4
    from billradar import app as a, example
    _, _, report = a._load(example.EXAMPLE_ID)
    whole = int(report.total_may_come_off)
    assert f'<p class="big" data-to="{whole}">${whole:,}</p>' in t  # the report's total; JS only counts up to it
    cards = re.findall(r'<li style="--i:\d"><svg class="ic".*?</svg>([^<]+)</li>', t)
    assert cards == ["Billed twice", "Baby on mom&#39;s insurance", "Financial help you qualify for"]
    assert "Seaside Regional Hospital" in t and "Daniela Castillo" in t
    assert "[PATIENT_1]" in t and "[ACCT_1]" in t


def test_letter_is_short_and_whole_for_screen_readers():
    t = home()
    sr = re.search(r'<span class="sr">([^<]+)</span>', t).group(1)
    typed = re.search(r'<span class="typed" aria-hidden="true">([^<]+)</span>', t).group(1)
    assert sr == typed  # no JS: the eyes get the whole letter too
    assert not re.search(r"\d; ", sr) and re.search(r"· and \d+ more accounts?\b", sr)  # the two accounts episode 2 shows, then how many more
    assert "5057388214" in sr and "5057388228" in sr  # the same account numbers as the bill above (ACCT_1, ACCT_2)
    assert "395.301" in sr


def test_end_states_without_js_and_with_reduced_motion():
    css = (STATIC / "story.css").read_text()
    js = (STATIC / "story.js").read_text()
    assert ".ep { --p: 1;" in css  # every episode starts at its end; only story.js (class "live") rewinds it
    assert re.search(r"prefers-reduced-motion: reduce\) \{[^}]*--p: 1 !important", css)
    assert 'matchMedia("(prefers-reduced-motion: reduce)").matches) return' in js
    assert "stroke-dashoffset: calc(1 - clamp(0, (var(--p)" in css  # the signature is drawn at --p = 1


def test_plates_get_hashed_addresses():
    t = home()
    srcs = re.findall(r'(?:src|srcset)="(/static/story/p\d-\d+\.webp\?v=[0-9a-f]{10})"', t)
    assert len(srcs) == 8


def test_boxes_and_request_come_from_recordings():
    import json
    from billradar import app as a
    found = json.loads((STATIC / "story" / "found.json").read_text())  # scripts/story_assets.py
    rec = Path(__file__).parent / "fixtures" / "recordings-nebius"
    assert (rec / found["find_recording"]).exists() and (rec / found["sent"]["recording"]).exists()
    assert len(found["boxes"]) == 4
    assert '"name": ""' in found["sent"]["excerpt"] and "PATIENT_1" in found["sent"]["excerpt"]  # no name left the device
    t = home()
    assert t.count('class="sheet2"') == 4 and t.count("<mark>") >= 3
    import json
    rows = json.loads((Path(a.__file__).parents[1] / "runs" / "example" / "receipt.json").read_text())["rows"]
    assert f'data-to="{len(rows)}"' in t  # the receipt of the example: as many calls as it lists


def test_try_block_points_to_the_example_and_the_branch():
    t = home()
    assert '<a class="btn gold" href="/example">Explore the example case</a>' in t
    from billradar import links
    assert links.tree() in t
