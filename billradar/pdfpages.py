"""Digital files in private mode: every PDF page is drawn as an image on this device, covered like a paper
sheet, checked by OCR, and only then sent. The PDF itself never leaves.

  1. The PDF's text layer (pypdf) is run through the same labels and known values as the photos, so an
     account number or a name found in the file is covered on every page and every sheet, label or not.
  2. Pages are drawn by poppler's `pdftoppm` (a local program: `brew install poppler`, `apt install
     poppler-utils`), covered by redact.redact, and checked by the same leak gate as the sheets.
  3. Pages go to the reader as images "D1.1", "D1.2", ...; after reading, merge_pages joins them back into
     one document "D1" (lines in page order, header fields from the first page that prints them).
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from billradar import redact
from billradar.case import DocKind, Document, Source

DPI = 150
PAGE_ID = re.compile(r"^(D\d+)\.(\d+)$")


class RenderError(RuntimeError):
    pass


def _pdftoppm() -> str:
    exe = os.environ.get("BILLRADAR_PDFTOPPM") or shutil.which("pdftoppm")
    if not exe:
        raise RenderError("pdftoppm not found: install poppler (brew install poppler / apt install poppler-utils)")
    return exe


def render(pdf: bytes, dpi: int = DPI, timeout: float = 120.0) -> list[Image.Image]:
    with tempfile.TemporaryDirectory(prefix="billradar-pdf-") as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(pdf)
        try:
            subprocess.run([_pdftoppm(), "-r", str(dpi), "-png", str(src), str(Path(tmp) / "page")],
                           check=True, capture_output=True, timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RenderError(f"could not draw the PDF's pages: {exc}") from exc
        pages = sorted(Path(tmp).glob("page-*.png"), key=lambda p: int(re.sub(r"\D", "", p.stem)))
        return [Image.open(p).convert("RGB") for p in pages]


def text_lines(pdf: bytes) -> list[str]:
    try:
        reader = PdfReader(io.BytesIO(pdf))
        return [ln for page in reader.pages for ln in (page.extract_text() or "").splitlines() if ln.strip()]
    except Exception as exc:  # a broken or encrypted file is not sent, and the person is told
        raise RenderError(f"could not read the file: {exc}") from exc


def learn(vault: redact.Vault, pdf: bytes) -> int:
    """Register every labelled value of the file's text layer in the vault. Returns how many lines had one."""
    hits = 0
    for line in text_lines(pdf):
        hits += bool(redact._spans(line, vault))
    return hits


def cover(pdf: bytes, file_id: str, vault: redact.Vault, check) -> list[tuple[str, Image.Image]]:
    """Pages of one file, covered and checked: [("D1.1", image), ...]. `check(image, page_id)` is the leak gate."""
    out = []
    for k, page in enumerate(render(pdf), start=1):
        page_id = f"{file_id}.{k}"
        out.append((page_id, check(redact.redact(page, vault).image, page_id)))
    return out


def merge_pages(docs: list[Document]) -> list[Document]:
    """'D1.1', 'D1.2' ... read as separate images -> one document 'D1' (source PDF). Others pass through."""
    groups: dict[str, list[tuple[int, Document]]] = {}
    out: list[Document | str] = []
    for d in docs:
        m = PAGE_ID.match(d.doc_id)
        if not m:
            out.append(d)
            continue
        base = m.group(1)
        if base not in groups:
            groups[base] = []
            out.append(base)
        groups[base].append((int(m.group(2)), d))
    return [x if isinstance(x, Document) else _merge(x, groups[x]) for x in out]


def _merge(base: str, numbered: list[tuple[int, Document]]) -> Document:
    pages = [d for _, d in sorted(numbered, key=lambda p: p[0])]
    fields: dict = {}
    for name in Document.model_fields:
        if name in ("doc_id", "source", "lines", "page_no", "page_count", "kind"):
            continue
        values = [getattr(d, name) for d in pages]
        fields[name] = next((v for v in values if v not in (None, "", False)), values[0])
    # a continuation page often reads as "other"; the file is what its first typed page says
    fields["kind"] = next((d.kind for d in pages if d.kind != DocKind.OTHER), pages[0].kind)
    lines = [ln for d in pages for ln in d.lines]
    if len({ln.line_no for ln in lines}) < len(lines):  # each page counted from 1: number them through
        lines = [ln.model_copy(update={"line_no": n}) for n, ln in enumerate(lines, start=1)]
    return Document(doc_id=base, source=Source.PDF, lines=lines, page_no=1, page_count=len(pages), **fields)


CREAM = (250, 246, 239)  # the page background, so the picture has no frame of its own


def to_paper(img: Image.Image) -> Image.Image:
    """Trim the table around a sheet photo: keep the rows and columns that are mostly light paper."""
    import numpy as np
    a = np.asarray(img.convert("L"))
    light = a > max(175, np.percentile(a, 90) - 15)  # the paper itself, not a light table around it
    rows = np.where(light.mean(axis=1) > 0.55)[0]
    cols = np.where(light.mean(axis=0) > 0.55)[0]
    if len(rows) < a.shape[0] * 0.4 or len(cols) < a.shape[1] * 0.4:  # not a sheet photo: leave it
        return img
    return img.crop((int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1))


def paste_paper(canvas: Image.Image, img: Image.Image, x: int, y: int) -> None:
    """A page laid on the background with a soft shadow, the way the documents column shows sheets."""
    from PIL import ImageFilter
    shadow = Image.new("L", (img.width + 40, img.height + 40), 0)
    shadow.paste(70, (20, 26, 20 + img.width, 26 + img.height))
    shadow = shadow.filter(ImageFilter.GaussianBlur(9))
    canvas.paste(Image.new("RGB", shadow.size, (120, 95, 50)), (x - 20, y - 20), shadow)
    canvas.paste(img, (x, y))


def sent_view(pages: list[tuple[str, Image.Image]], model: str = "Nemotron-3.5-Super-VL", per_row: int = 4) -> Image.Image:
    """The covered pages exactly as they left the device, for "What the models saw"; `model` is the reader's name."""
    from PIL import ImageDraw
    cell_w, cell_h, gap, label_h = 480, 620, 28, 40
    rows = (len(pages) + per_row - 1) // per_row
    canvas = Image.new("RGB", (per_row * cell_w + (per_row + 1) * gap, rows * (cell_h + label_h + gap) + gap), CREAM)
    draw = ImageDraw.Draw(canvas)
    font = redact._font(24)
    for i, (page_id, img) in enumerate(pages):
        r, c = divmod(i, per_row)
        x, y = gap + c * (cell_w + gap), gap + r * (cell_h + label_h + gap)
        t = img.copy()
        t.thumbnail((cell_w, cell_h))
        file_id, page = page_id.split(".")
        draw.text((x, y), f"to {model}: file {file_id[1:]} page {page}", fill=(30, 42, 46), font=font)
        paste_paper(canvas, t, x, y + label_h)
    return canvas
