"""SYNTHETIC test material: a drawn table photo, a client that never calls a model, and fakes that answer it.

Nothing here came from an API. FakeClient takes the same Responses-style bodies as NemotronClient and hands
each one to a fake that returns Responses events (text deltas, then response.completed); FakeTable answers the
sheet finder from sheets drawn on the photo and the reader with a fixed reading.
"""

from __future__ import annotations

import io
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from billradar.model_api import ModelError, Result, request_key

WOOD = (120, 84, 52)
SIZE = (2400, 1800)
CASE_FIXTURE = Path(__file__).parent / "fixtures" / "cases" / "anna-birth.json"


def sheet_quad(cx: float, cy: float, w: float, h: float, angle_deg: float) -> np.ndarray:
    a = np.deg2rad(angle_deg)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    pts = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]]) @ rot.T
    return (pts + [cx, cy]).astype(np.float32)


# Two rows of sheets, slightly turned, like papers laid out on a kitchen table (8.5x11 ratio).
QUADS = [
    sheet_quad(500, 470, 520, 670, -6),
    sheet_quad(1180, 450, 520, 670, 4),
    sheet_quad(1880, 480, 520, 670, -3),
    sheet_quad(700, 1330, 520, 670, 5),
    sheet_quad(1500, 1310, 520, 670, -4),
]


def table_photo(quads: list[np.ndarray], size=SIZE) -> bytes:
    W, H = size
    img = np.full((H, W, 3), WOOD, dtype=np.uint8)
    rng = np.random.default_rng(7)
    img = np.clip(img.astype(int) + rng.integers(-12, 12, img.shape), 0, 255).astype(np.uint8)
    for q in quads:
        cv2.fillPoly(img, [q.astype(np.int32)], (246, 244, 238))
        tl = q[0]
        for k in range(8):  # a few printed lines, so the sheet is not blank
            y = tl + (q[3] - q[0]) * (0.12 + 0.09 * k)
            x2 = y + (q[1] - q[0]) * 0.75
            cv2.line(img, tuple((y + (q[1] - q[0]) * 0.1).astype(int)), tuple(x2.astype(int)), (70, 70, 70), 4)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def boxes(quads: list[np.ndarray], size=SIZE) -> list[dict]:
    """The sheet finder's answer for quads drawn on a photo of `size`: boxes in 0..1000 of each axis."""
    W, H = size
    return [{"box": [int(q[:, 0].min() / W * 1000), int(q[:, 1].min() / H * 1000),
                     int(q[:, 0].max() / W * 1000 + 1), int(q[:, 1].max() / H * 1000 + 1)],
             "confidence": 0.9} for q in reversed(quads)]  # any order: code sorts them


def events(text: str, completed: bool = True, usage: dict | None = None) -> list[dict]:
    half = len(text) // 2  # deltas may split anywhere
    evs = [{"type": "response.output_text.delta", "delta": text[:half]},
           {"type": "response.output_text.delta", "delta": text[half:]}] if text else []
    if completed:
        evs.append({"type": "response.completed", "response": {"id": "resp_SYNTHETIC", "usage": usage or {}}})
    return evs


def collect(evs: list[dict]) -> tuple[str, dict, str]:
    """Join output text; insist on response.completed (a cut answer is an error, not a short answer)."""
    chunks, final = [], None
    for ev in evs:
        t = ev.get("type")
        if t == "response.output_text.delta":
            chunks.append(ev.get("delta") or "")
        elif t == "error":
            raise ModelError(f"stream error {ev.get('code')}: {ev.get('message')}")
        elif t in ("response.completed", "response.failed", "response.incomplete"):
            final = ev
    if not final:
        raise ModelError("stream ended without a final event (partial output)")
    if final["type"] != "response.completed":
        raise ModelError(f"response ended with {final['type']}: {json.dumps(final)[:500]}")
    resp = final.get("response") or {}
    return "".join(chunks), resp.get("usage", {}), resp.get("id", "")


class FakeClient:
    """A client that never leaves the machine: each body goes to `fake.events(body)`. It journals each request
    but keeps no `calls` list, so a case run with it has no receipt."""

    provider = "fake"
    max_images = None

    def __init__(self, fake, journal: Path | None = None, live: bool = False):
        self.fake, self.journal, self._live = fake, journal, live

    @property
    def live(self) -> bool:
        return self._live

    def respond(self, body: dict, label: str = "") -> Result:
        started = time.monotonic()
        evs = self.fake.events(body)
        seconds = round(time.monotonic() - started, 2)
        text, usage, rid = collect(evs)
        images = sum(1 for item in body.get("input", []) if isinstance(item.get("content"), list)
                     for p in item["content"] if p.get("type") == "input_image")
        entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "label": label,
                 "model": body["model"], "key": request_key(body), "live": self.live, "images": images,
                 "seconds": seconds, "usage": usage}
        if self.journal:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        return Result(text, evs, usage, rid, replayed=not self.live, seconds=seconds)

    def transcribe(self, request: dict, audio: bytes, label: str = ""):
        return self.fake.transcribe(request, audio), not self.live


def reading_from_fixture(rename: dict[str, str]) -> dict:
    """SYNTHETIC reader answer: the anna-birth case written back as printed text, as the schema asks."""
    case = json.loads(CASE_FIXTURE.read_text())

    def txt(v):
        if v is None:
            return ""
        if isinstance(v, str) and v.replace(".", "").replace("-", "").isdigit() and "-" not in v[1:]:
            d = Decimal(v)
            return f"({abs(d):,.2f})" if d < 0 else f"${d:,.2f}"
        return str(v)

    docs = []
    for d in case["documents"]:
        docs.append({
            "doc_id": rename.get(d["doc_id"], d["doc_id"]), "kind": d["kind"], "patient": f"[{d.get('patient', '')}]",
            "provider": d.get("provider", ""), "account_ref": d.get("account_ref", ""), "claim_ref": d.get("claim_ref", ""),
            "statement_date": d.get("statement_date", ""), "service_from": d.get("service_from", ""),
            "service_to": d.get("service_to", ""), "first_after_discharge": d.get("first_after_discharge", False),
            "insurance_payment_date": d.get("insurance_payment_date", ""),
            "member_ref": d.get("member_ref", ""), "page_no": d.get("page_no") or 0, "page_count": d.get("page_count") or 0,
            **{k: txt(d.get(k)) for k in ("total_charges", "insurance_paid", "fa_discount", "patient_balance",
                                          "amount_due_coupon")},
            "lines": [{
                "line_no": ln["line_no"], "service_date": ln.get("service_date", ""), "code": ln.get("code", ""),
                "description": ln.get("description", ""), "claim_ref": ln.get("claim_ref", ""),
                "denial_codes": ln.get("denial_codes", ""),
                **{k: txt(ln.get(k)) for k in ("charges", "insurance_paid", "adjustment", "fa_discount",
                                                "patient_responsibility")},
            } for ln in d.get("lines", [])],
        })
    return {"documents": docs}


class FakeTable:
    """Answers the sheet finder from quads drawn on the ORIGINAL photo and the reader with a fixed reading."""

    def __init__(self, quads: list[np.ndarray], size=SIZE, reading: dict | str = "", extra: list[dict] | None = None,
                 completed: bool = True):
        self.quads, self.size, self.reading, self.extra, self.completed = quads, size, reading, extra or [], completed
        self.sheet_bodies: list[dict] = []
        self.read_bodies: list[dict] = []

    def events(self, body: dict) -> list[dict]:
        name = ((body.get("text") or {}).get("format") or {}).get("name")
        if name == "sheets_on_table":
            self.sheet_bodies.append(body)
            return events(json.dumps({"sheets": boxes(self.quads, self.size) + self.extra}), self.completed)
        self.read_bodies.append(body)
        text = self.reading if isinstance(self.reading, str) else json.dumps(self.reading)
        return events(text, usage={"input_tokens": 9000, "output_tokens": 1200})


class PackAnswers:
    """Answers a sample pack from its generator's files: the sheet finder from answers/sheets.json (the corners
    of every sheet on every photo), the reader from answers/reading.json (what a perfect reader returns)."""

    def __init__(self, pack: Path):
        self.reading = json.loads((pack / "answers" / "reading.json").read_text())
        self.corners = json.loads((pack / "answers" / "sheets.json").read_text())
        self.photos = sorted(p for p in pack.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
        self.sizes = {p.name: Image.open(p).size for p in self.photos}
        self.sheet_calls = 0

    def events(self, body: dict) -> list[dict]:
        if ((body.get("text") or {}).get("format") or {}).get("name") == "sheets_on_table":
            name = self.photos[self.sheet_calls].name  # the pipeline sends photos in sorted order
            self.sheet_calls += 1
            quads = [np.array(q, dtype=np.float32) for q in self.corners[name]]
            return events(json.dumps({"sheets": boxes(quads, self.sizes[name])}))
        return events(json.dumps(self.paged(body)))

    def paged(self, body: dict) -> dict:
        """Private mode sends a PDF as page images D1.1, D1.2, ...: answer each page, the file's whole reading on
        its first page and empty continuation pages, so the pipeline's merge_pages gives back the file's reading."""
        asked = re.findall(r"\b[SD]\d+(?:\.\d+)?\b", body["input"][1]["content"][0]["text"])  # "Documents in this case: ..."
        docs = []
        for d in self.reading["documents"]:
            pages = sorted((a for a in asked if a.startswith(d["doc_id"] + ".")), key=lambda a: int(a.split(".")[1]))
            if not pages:
                docs.append(d)
                continue
            docs.append(d | {"doc_id": pages[0]})
            blank = {k: ("" if isinstance(v, str) else v) for k, v in d.items()}
            docs += [blank | {"doc_id": pid, "kind": "other", "lines": [], "first_after_discharge": False,
                              "page_no": 0, "page_count": 0} for pid in pages[1:]]
        return self.reading | {"documents": docs}


def run_pack(pack: Path, case_id: str | None = None, private: bool = False, answers: PackAnswers | None = None):
    """The pack through the whole pipeline with its generator's answers (SYNTHETIC, not a measurement)."""
    from billradar import example
    answers = answers or PackAnswers(pack)
    info = json.loads((pack / "profile.json").read_text())
    return example._run(FakeClient(answers), pack, info, answers.photos, case_id, private)
