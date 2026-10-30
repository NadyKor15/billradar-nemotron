"""Insurance plan documents as data, every rule tied to a quote on a page of the plan's own PDF.

Same rule as the hospital policies: a quote that is not found on its page is not used.
An appeal letter quotes the family's plan document, never our paraphrase of it.

The demo plan ("Sample Health Plan") is fictional and marked SAMPLE. Its PDF is built
from `insurance_plans/<plan_id>.txt` by `scripts/make_sample_plan.py` and kept in the repo,
so quotes are checked against the same text layer a family's uploaded plan would have.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from pypdf import PdfReader

from billradar.policies.source import compact

PLAN_DIR = Path(__file__).parent / "insurance_plans"
PREFIX = "plan:"


@dataclass(frozen=True)
class Fragment:
    plan_id: str
    fragment_id: str
    page: int
    quote: str

    @property
    def ref(self) -> str:
        return f"{PREFIX}{self.plan_id}#{self.fragment_id}"


@dataclass(frozen=True)
class Ground:
    code: str
    fragment: str
    say: str  # our sentence next to the quote: no number, no fact about the family that is not in the case
    needs: str = ""  # "birth": used only when the case shows a birth


@dataclass
class Plan:
    plan_id: str
    insurer: str
    title: str
    plan_type: str
    sample: bool
    appeals_to: list[str]
    appeal_days: int
    appeal_days_fragment: str
    decide_days_post_service: int | None
    decide_days_fragment: str | None
    remark_codes: dict[str, dict]
    grounds: dict[str, list[Ground]]
    fragments: dict[str, Fragment] = field(default_factory=dict)

    def fragment(self, fragment_id: str) -> Fragment:
        return self.fragments[fragment_id]

    def ref(self, fragment_id: str) -> str:
        return self.fragments[fragment_id].ref

    def is_denial(self, code: str) -> bool:
        return bool(self.remark_codes.get(code, {}).get("denial"))

    def code_fragment(self, code: str) -> Fragment | None:
        entry = self.remark_codes.get(code)
        return self.fragments.get(entry["fragment"]) if entry else None


def pdf_path(plan_id: str) -> Path:
    return PLAN_DIR / f"{plan_id}.pdf"


@cache
def load(plan_id: str) -> Plan:
    raw = json.loads((PLAN_DIR / f"{plan_id}.json").read_text())
    frags = {fid: Fragment(plan_id, fid, f["page"], f["quote"]) for fid, f in raw["fragments"].items()}
    grounds = {code: [Ground(code, g["fragment"], g["say"], g.get("needs", "")) for g in items] for code, items in raw["grounds"].items()}
    return Plan(
        plan_id=raw["plan_id"], insurer=raw["insurer"], title=raw["title"], plan_type=raw.get("plan_type", ""),
        sample=raw.get("sample", False), appeals_to=raw["appeals_to"], appeal_days=raw["appeal_days"],
        appeal_days_fragment=raw["appeal_days_fragment"],
        decide_days_post_service=raw.get("decide_days_post_service"),
        decide_days_fragment=raw.get("decide_days_fragment"),
        remark_codes=raw["remark_codes"], grounds=grounds, fragments=frags)


def available() -> list[str]:
    return sorted(p.stem for p in PLAN_DIR.glob("*.json"))


def for_insurer(name: str) -> str | None:
    """The plan whose insurer is printed on an insurance statement ("Sample Health Plan")."""
    key = compact(name or "")
    return next((p for p in available() if key and compact(load(p).insurer) == key), None)


@cache
def page_texts(plan_id: str) -> tuple[str, ...]:
    path = pdf_path(plan_id)
    if not path.exists():
        return ()
    return tuple(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def verify(frag: Fragment) -> tuple[bool, str]:
    pages = [compact(t) for t in page_texts(frag.plan_id)]
    if not pages:
        return False, "plan document not found"
    needle = compact(frag.quote)
    if 0 < frag.page <= len(pages) and needle in pages[frag.page - 1]:
        return True, "ok"
    elsewhere = next((i + 1 for i, p in enumerate(pages) if needle in p), None)
    if elsewhere:
        return False, f"quote is on page {elsewhere}, not {frag.page}"
    return False, "quote not found in plan document"


def is_ref(ref: str) -> bool:
    return ref.startswith(PREFIX)


@cache
def verified(ref: str) -> bool:
    plan_id, fragment_id = ref[len(PREFIX):].split("#", 1)
    try:
        frag = load(plan_id).fragment(fragment_id)
    except (FileNotFoundError, KeyError):
        return False
    return verify(frag)[0]


def resolve(ref: str) -> Fragment:
    plan_id, fragment_id = ref[len(PREFIX):].split("#", 1)
    return load(plan_id).fragment(fragment_id)


# --- the SAMPLE plan PDF -------------------------------------------------------------------

def source_pages(plan_id: str) -> list[str]:
    text = (PLAN_DIR / f"{plan_id}.txt").read_text()
    return [p.strip("\n") for p in text.split("\n\\f\n")]


def render_pdf(pages: list[str]) -> bytes:
    """A plain Letter-size PDF, one source page per PDF page, long lines wrapped."""
    from reportlab.lib.colors import Color
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792), invariant=1)
    for n, page in enumerate(pages, 1):
        y = 740
        c.setFillColor(Color(0.8, 0.1, 0.1))
        c.setFont("Helvetica-Bold", 9)
        c.drawRightString(576, 770, "SAMPLE - FICTIONAL")
        c.setFillColor(Color(0, 0, 0))
        for line in page.split("\n"):
            bold = line.startswith(("Section", "Sample Health Plan", "SAMPLE")) or line[:3].rstrip(".").replace(".", "").isdigit()
            font = "Helvetica-Bold" if bold else "Helvetica"
            c.setFont(font, 10.5)
            for part in simpleSplit(line, font, 10.5, 520) or [""]:
                c.drawString(46, y, part)
                y -= 15
        c.setFont("Helvetica", 8.5)
        c.drawCentredString(306, 30, f"Page {n} of {len(pages)}")
        c.showPage()
    c.save()
    return buf.getvalue()
