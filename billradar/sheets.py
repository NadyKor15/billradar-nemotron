"""Sheets on a table photo: the geometry around the model that finds them (billradar.sheets_vl).

  boxes in original pixels -> drop noise (tiny, odd shapes, duplicates) -> reading order S1..Sn
  -> straighten each sheet from its corners, cut from the full-size original -> the numbered overlay.

The model finds the sheets; code does the geometry.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageOps

PHRASE = "sheet of paper"
SEND_MAX_SIDE = 1600  # the copy sent to the model; sheets are cut from the original
MIN_AREA_FRACTION = 0.012
ASPECT_RANGE = (0.33, 3.0)
NMS_IOU = 0.5
SHEET_COLORS = [(232, 106, 90), (214, 164, 72), (52, 160, 150), (139, 104, 196), (74, 136, 214), (232, 110, 150)]


@dataclass
class Detection:
    object_id: str
    box: tuple[float, float, float, float]  # left, top, right, bottom in ORIGINAL pixels, half-open
    confidence: float | None
    mask: np.ndarray | None = None  # uint8 0/1, size of the box in original pixels

    @property
    def area(self) -> float:
        l, t, r, b = self.box
        return max(0.0, r - l) * max(0.0, b - t)


@dataclass
class Sheet:
    doc_id: str  # S1, S2 ... in reading order; the number painted on screen
    box: tuple[int, int, int, int]
    confidence: float | None
    corners: list[tuple[int, int]] | None  # 4 corners in original pixels when the paper's edge gave them
    image: Image.Image  # straightened (or box-cropped) sheet from the full-size original
    straightened: bool


@dataclass
class SheetsResult:
    sheets: list[Sheet]
    dropped: list[str] = field(default_factory=list)  # why each detection was not kept
    phrase: str = PHRASE
    sent_size: tuple[int, int] = (0, 0)
    original_size: tuple[int, int] = (0, 0)
    seconds: float = 0.0
    replayed: bool = True


def load_photo(raw: bytes) -> Image.Image:
    """Phone photos carry rotation in EXIF; apply it before anything measures pixels."""
    img = Image.open(io.BytesIO(raw))
    return ImageOps.exif_transpose(img).convert("RGB")


def to_jpeg(img: Image.Image, max_side: int | None = None, quality: int = 88) -> bytes:
    if max_side and max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _iou(a, b) -> float:
    l, t = max(a[0], b[0]), max(a[1], b[1])
    r, btm = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, r - l) * max(0.0, btm - t)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def clean(dets: list[Detection], size: tuple[int, int]) -> tuple[list[Detection], list[str]]:
    """No confidence threshold from the model is assumed; noise is removed by geometry."""
    W, H = size
    kept, dropped = [], []
    for d in sorted(dets, key=lambda d: (d.confidence or 0, d.area), reverse=True):
        l, t, r, b = d.box
        w, h = r - l, b - t
        if d.area < MIN_AREA_FRACTION * W * H:
            dropped.append(f"object {d.object_id}: too small ({d.area / (W * H):.1%} of photo)")
            continue
        aspect = h / w if w else 0
        if not ASPECT_RANGE[0] <= aspect <= ASPECT_RANGE[1]:
            dropped.append(f"object {d.object_id}: not sheet-shaped (h/w {aspect:.2f})")
            continue
        if any(_iou(d.box, k.box) > NMS_IOU for k in kept):
            dropped.append(f"object {d.object_id}: duplicate of a kept sheet")
            continue
        kept.append(d)
    return kept, dropped


def reading_order(dets: list[Detection]) -> list[Detection]:
    """Top row first, left to right, like a person counts papers on a table."""
    if not dets:
        return []
    heights = sorted(d.box[3] - d.box[1] for d in dets)
    tol = heights[len(heights) // 2] / 2
    rows: list[list[Detection]] = []
    for d in sorted(dets, key=lambda d: (d.box[1] + d.box[3]) / 2):
        cy = (d.box[1] + d.box[3]) / 2
        if rows and abs(cy - np.mean([(x.box[1] + x.box[3]) / 2 for x in rows[-1]])) <= tol:
            rows[-1].append(d)
        else:
            rows.append([d])
    return [d for row in rows for d in sorted(row, key=lambda d: d.box[0])]


def _order_corners(pts: np.ndarray) -> np.ndarray:
    s, diff = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(diff)], pts[np.argmax(s)], pts[np.argmax(diff)]], dtype=np.float32)


def corners_from_mask(det: Detection) -> np.ndarray | None:
    if det.mask is None or det.mask.sum() == 0:
        return None
    contours, _ = cv2.findContours((det.mask * 255).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.5 * det.mask.size * 0.5:
        return None
    approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
    pts = approx.reshape(-1, 2) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(c))
    pts = np.asarray(pts, dtype=np.float32) + np.array([det.box[0], det.box[1]], dtype=np.float32)
    return _order_corners(pts)


def straighten(original: np.ndarray, corners: np.ndarray) -> np.ndarray:
    tl, tr, br, bl = corners
    w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    return cv2.warpPerspective(original, cv2.getPerspectiveTransform(corners, dst), (w, h))


def overlay(photo: bytes, result: SheetsResult, max_side: int = 1400) -> Image.Image:
    """The table photo with each sheet outlined and numbered, for the case screen."""
    img = load_photo(photo)
    k = min(1.0, max_side / max(img.size))
    canvas = cv2.resize(np.asarray(img), (int(img.width * k), int(img.height * k)))
    for n, s in enumerate(result.sheets):
        color = SHEET_COLORS[n % len(SHEET_COLORS)]
        if s.corners:
            pts = (np.array(s.corners) * k).astype(np.int32)
        else:
            l, t, r, b = (np.array(s.box) * k).astype(int)
            pts = np.array([[l, t], [r, t], [r, b], [l, b]], dtype=np.int32)
        cv2.polylines(canvas, [pts], True, color, max(2, int(6 * k)), cv2.LINE_AA)
        cx, cy = pts[0]
        radius = max(14, int(34 * k))
        cv2.circle(canvas, (int(cx), int(cy)), radius, color, -1, cv2.LINE_AA)
        label = s.doc_id[1:]
        scale = radius / 22
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, scale, 2)
        cv2.putText(canvas, label, (int(cx - tw / 2), int(cy + th / 2)), cv2.FONT_HERSHEY_DUPLEX, scale,
                    (255, 255, 255), 2, cv2.LINE_AA)
    return Image.fromarray(canvas)
