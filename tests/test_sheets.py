"""Sheets on a table photo: Super VL's boxes (SYNTHETIC answers) and the local geometry around them."""

import json

import numpy as np
import pytest

from billradar import sheets, sheets_vl
from billradar.model_api import ModelError
from tests.fakes import QUADS, SIZE, FakeClient, FakeTable, sheet_quad, table_photo


def find(fake, tmp_path=None, photo=None):
    client = FakeClient(fake, journal=tmp_path / "requests.jsonl" if tmp_path else None)
    return sheets_vl.find_sheets(client, photo or table_photo(fake.quads, SIZE))


def test_finds_every_sheet_in_reading_order(tmp_path):
    res = find(FakeTable(QUADS), tmp_path)
    assert [s.doc_id for s in res.sheets] == ["S1", "S2", "S3", "S4", "S5"]
    centers = [((s.box[0] + s.box[2]) / 2, (s.box[1] + s.box[3]) / 2) for s in res.sheets]
    for got, want in zip(centers, [q.mean(axis=0) for q in QUADS]):
        assert np.hypot(got[0] - want[0], got[1] - want[1]) < 25
    assert res.sent_size[0] <= sheets.SEND_MAX_SIDE and res.original_size == SIZE


def test_sheets_are_straightened_to_paper_shape(tmp_path):
    for s in find(FakeTable(QUADS), tmp_path).sheets:
        assert s.straightened
        w, h = s.image.size
        assert abs(w - 520) / 520 < 0.08 and abs(h - 670) / 670 < 0.08, (s.doc_id, s.image.size)


def test_noise_is_dropped_with_a_reason(tmp_path):
    tiny = {"box": [950, 933, 967, 956], "confidence": 0.9}
    q = QUADS[0]
    dup = {"box": [int(q[:, 0].min() / 2.4) + 2, int(q[:, 1].min() / 1.8) + 2,
                   int(q[:, 0].max() / 2.4) - 2, int(q[:, 1].max() / 1.8) - 2], "confidence": 0.5}
    long_strip = {"box": [125, 956, 875, 989], "confidence": 0.9}
    res = find(FakeTable(QUADS, extra=[tiny, dup, long_strip]), tmp_path)
    assert len(res.sheets) == 5
    reasons = " ".join(res.dropped)
    assert "too small" in reasons and "duplicate" in reasons and "not sheet-shaped" in reasons


def test_no_sheets_is_a_normal_answer(tmp_path):
    assert find(FakeTable([]), tmp_path).sheets == []


def test_cut_answer_is_an_error_not_a_short_answer(tmp_path):
    with pytest.raises(ModelError, match="without a final event"):
        find(FakeTable(QUADS, completed=False), tmp_path)


def test_request_asks_for_boxes_without_thinking(tmp_path):
    fake = FakeTable(QUADS)
    find(fake, tmp_path)
    body = fake.sheet_bodies[0]
    assert body["text"]["format"]["name"] == "sheets_on_table" and body["text"]["format"]["strict"] is True
    assert body["reasoning"] == {"effort": "none"} and body["store"] is False
    assert [p["type"] for p in body["input"][1]["content"]] == ["input_text", "input_image"]


def test_journal_logs_the_request_but_never_the_image(tmp_path):
    find(FakeTable(QUADS), tmp_path)
    line = (tmp_path / "requests.jsonl").read_text().strip()
    entry = json.loads(line)
    assert entry["images"] == 1 and entry["live"] is False
    assert "base64" not in line and len(line) < 1000


def test_reading_order_is_rows_then_left_to_right():
    dets = [sheets.Detection(str(i), (x, y, x + 400, y + 520), 0.9)
            for i, (x, y) in enumerate([(1500, 1000), (100, 80), (900, 60), (200, 1010)])]
    assert [d.object_id for d in sheets.reading_order(dets)] == ["1", "2", "3", "0"]


def test_overlay_draws_numbers(tmp_path):
    photo = table_photo(QUADS, SIZE)
    res = find(FakeTable(QUADS), tmp_path, photo)
    assert max(sheets.overlay(photo, res).size) == 1400


def test_straighten_gives_the_sheet_upright():
    q = sheet_quad(600, 500, 400, 520, 8)
    arr = np.zeros((1000, 1200, 3), dtype=np.uint8)
    out = sheets.straighten(arr, sheets._order_corners(q))
    assert abs(out.shape[1] - 400) < 4 and abs(out.shape[0] - 520) < 4
