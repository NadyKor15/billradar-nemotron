"""Nemotron 3.5 Super VL finds the sheets on a table photo; local OpenCV finds their corners.

Token Factory has no segmentation model, so finding the sheets is split in two:
  smaller copy of the photo -> Super VL "box around every sheet of paper" (JSON, 0-1000 coordinates)
  -> boxes in original pixels -> noise rules and reading order (billradar.sheets)
  -> inside each box, no model: paper is the large bright region, its contour gives the corners
  -> straighten and cut from the full-size original.

Returns sheets.SheetsResult for the pipeline, the overlay and the screens.
"""

from __future__ import annotations

import io
import json

import cv2
import numpy as np
from PIL import Image

from billradar import sheets as sg
from billradar.model_api import data_url
from billradar.nemotron_client import READER_MODEL

PHRASE = "sheet of paper"
SCALE = 1000  # the model answers in 0..1000 on both axes of the image it got
MARGIN = 0.04  # widen each box a little before looking for the paper's edge inside it

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sheets"],
    "properties": {"sheets": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["box", "confidence"],
        "properties": {
            "box": {"type": "array", "items": {"type": "integer"}},
            "confidence": {"type": "number"},
        }}}},
}

INSTRUCTIONS = f"""You find paper on a photo of a table for BillRadar. You do not read the papers.

Return one entry per separate sheet of paper that is visible (letters, bills, statements, forms, envelopes count only if they are flat paper sheets).
- box: [left, top, right, bottom] around the whole sheet, in coordinates from 0 to {SCALE} on each axis of the image (0,0 is the top-left corner, {SCALE},{SCALE} the bottom-right).
- confidence: 0 to 1, how sure you are this is one whole sheet.
Overlapping sheets are separate entries. A sheet cut by the photo edge still counts. Do not return the table, a phone, a pen or a hand."""


def request_body(jpeg: bytes, effort: str = "none") -> dict:
    return {
        "model": READER_MODEL, "store": False, "reasoning": {"effort": effort}, "max_output_tokens": 4000,
        "text": {"format": {"type": "json_schema", "name": "sheets_on_table", "schema": SCHEMA, "strict": True}},
        "input": [
            {"role": "developer", "content": [{"type": "input_text", "text": INSTRUCTIONS}]},
            {"role": "user", "content": [{"type": "input_text", "text": f"Find every {PHRASE}."},
                                         {"type": "input_image", "image_url": data_url(jpeg, "image/jpeg")}]},
        ],
    }


def parse(text: str, size: tuple[int, int]) -> tuple[list[sg.Detection], list[str]]:
    """Model JSON -> detections in pixels of an image of `size`; a box that makes no sense is dropped with a note."""
    W, H = size
    dets, notes = [], []
    for i, s in enumerate(json.loads(text).get("sheets", []), start=1):
        box = s.get("box") or []
        if len(box) != 4:
            notes.append(f"box {i}: {box!r} is not 4 numbers")
            continue
        l, t, r, b = (max(0, min(SCALE, int(v))) for v in box)
        if r <= l or b <= t:
            notes.append(f"box {i}: {box!r} is empty")
            continue
        dets.append(sg.Detection(str(i), (l * W / SCALE, t * H / SCALE, r * W / SCALE, b * H / SCALE),
                                  float(s["confidence"]) if s.get("confidence") is not None else None))
    return dets, notes


def paper_mask(arr: np.ndarray, det: sg.Detection) -> tuple[sg.Detection, np.ndarray | None]:
    """No model here. Widen the box, take the bright paper region by Otsu, keep the biggest blob.
    Returns the detection with its box widened and a mask the size of that box (None if no paper was found)."""
    H, W = arr.shape[:2]
    l, t, r, b = det.box
    mx, my = (r - l) * MARGIN, (b - t) * MARGIN
    box = (max(0.0, l - mx), max(0.0, t - my), min(float(W), r + mx), min(float(H), b + my))
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    crop = arr[y0:y1, x0:x1]
    if crop.size == 0:
        return det, None
    gray = cv2.GaussianBlur(cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    _, bright = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))  # printed text is dark: close it
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bright, connectivity=8)
    if n < 2:
        return det, None
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = (labels == biggest).astype(np.uint8)
    widened = sg.Detection(det.object_id, (float(x0), float(y0), float(x1), float(y1)), det.confidence)
    return widened, mask


def find_sheets(client, photo: bytes, phrase: str = PHRASE, max_side: int = sg.SEND_MAX_SIDE,
                send: Image.Image | None = None, crop_from: Image.Image | None = None) -> sg.SheetsResult:
    """send: the image the model gets instead of a plain smaller copy (private mode: covered and blurred).
    crop_from: the full-size image sheets are cut from (private mode: the covered original)."""
    img = crop_from if crop_from is not None else sg.load_photo(photo)
    sent = sg.to_jpeg(send) if send is not None else sg.to_jpeg(img, max_side)
    sent_w, sent_h = Image.open(io.BytesIO(sent)).size
    res = client.respond(request_body(sent), label="vl:sheets")
    dets, notes = parse(res.text, img.size)
    kept, dropped = sg.clean(dets, img.size)
    arr = np.asarray(img)
    sheets = []
    for i, d in enumerate(sg.reading_order(kept), start=1):
        widened, mask = paper_mask(arr, d)
        corners = None
        if mask is not None:
            widened.mask = mask
            corners = sg.corners_from_mask(widened)
        if corners is not None:
            crop = Image.fromarray(sg.straighten(arr, corners))
        else:
            crop = img.crop(tuple(int(v) for v in d.box))
        l, t, r, b = (int(v) for v in d.box)
        sheets.append(sg.Sheet(
            doc_id=f"S{i}", box=(l, t, r, b), confidence=d.confidence,
            corners=[(int(x), int(y)) for x, y in corners] if corners is not None else None,
            image=crop, straightened=corners is not None))
    return sg.SheetsResult(sheets, dropped + notes, phrase, (sent_w, sent_h), img.size, res.seconds, res.replayed)
