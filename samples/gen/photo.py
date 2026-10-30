"""Turn a rendered page into a phone-photo look: a letter folded in three, lying on a light
surface, shot slightly off-axis. Deterministic for a given seed.
"""
from __future__ import annotations

import random

import fitz  # pymupdf
from PIL import Image, ImageDraw, ImageFilter

OUT_W, OUT_H = 1440, 2560


def corners(mask, dx=0, dy=0, scale=1.0):
    """Four corners of the sheet in a mask, clockwise from top-left, in output-photo pixels."""
    pts = [(x, y) for y in range(0, mask.height, 2) for x in range(0, mask.width, 2) if mask.getpixel((x, y)) > 128]
    tl = min(pts, key=lambda q: q[0] + q[1])
    tr = max(pts, key=lambda q: q[0] - q[1])
    br = max(pts, key=lambda q: q[0] + q[1])
    bl = max(pts, key=lambda q: q[1] - q[0])
    return [[round((x + dx) * scale), round((y + dy) * scale)] for x, y in (tl, tr, br, bl)]


def page_image(pdf_path, page_no, dpi=170):
    doc = fitz.open(pdf_path)
    pm = doc[page_no].get_pixmap(dpi=dpi)
    return Image.frombytes("RGB", (pm.width, pm.height), pm.samples)


def _paper(img, rng):
    """Warm grey paper tone, two horizontal folds, faint shading toward one corner."""
    w, h = img.size
    tone = Image.new("RGB", (w, h), (246, 245, 241))
    paper = Image.blend(img, tone, 0.04)
    shade = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(shade)
    for i in range(h):
        d.line([(0, i), (w, i)], fill=int(12 * i / h + rng.uniform(0, 2)))
    for k, f in enumerate((1 / 3, 2 / 3)):
        y = int(h * f + rng.uniform(-6, 6))
        d.rectangle([0, y - 2, w, y + 1], fill=60)
        band = 34 if k == 0 else 26
        for j in range(band):
            d.line([(0, y + 2 + j), (w, y + 2 + j)], fill=int(26 * (1 - j / band)))
    shade = shade.filter(ImageFilter.GaussianBlur(2.2))
    dark = Image.new("RGB", (w, h), (120, 118, 112))
    return Image.composite(dark, paper, shade.point(lambda v: min(v, 90)))


def _quad(w, h, rng):
    j = lambda a: rng.uniform(-a, a)
    return (j(w * 0.012), j(h * 0.006), j(w * 0.012), h + j(h * 0.006),
            w + j(w * 0.012), h + j(h * 0.006), w + j(w * 0.012), j(h * 0.006))


def photo(page, seed):
    rng = random.Random(seed)
    sheet = _paper(page, rng)
    sw = int(OUT_W * rng.uniform(0.90, 0.95))
    sh = int(sw * page.height / page.width)
    sheet = sheet.resize((sw, sh), Image.LANCZOS)
    quad = _quad(sw, sh, rng)
    sheet = sheet.transform((sw, sh), Image.QUAD, quad, Image.BICUBIC)
    mask = Image.new("L", (sw, sh), 255).transform((sw, sh), Image.QUAD, quad, Image.BICUBIC)
    bg = Image.new("RGB", (OUT_W, OUT_H), (232, 229, 214))
    nw, nh = OUT_W // 16, OUT_H // 16
    noise = Image.new("L", (nw, nh))
    noise.putdata([int(rng.gauss(128, 18)) for _ in range(nw * nh)])
    noise = noise.resize((OUT_W, OUT_H), Image.BICUBIC)
    bg = Image.composite(Image.new("RGB", bg.size, (214, 210, 192)), bg, noise.point(lambda v: max(0, v - 120)))
    x = (OUT_W - sw) // 2 + rng.randint(-20, 20)
    y = (OUT_H - sh) // 2 + rng.randint(-60, 60)
    shadow = Image.new("L", (OUT_W, OUT_H), 0)
    ImageDraw.Draw(shadow).rectangle([x + 10, y + 14, x + sw + 10, y + sh + 14], fill=110)
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    bg = Image.composite(Image.new("RGB", bg.size, (150, 146, 132)), bg, shadow)
    bg.paste(sheet, (x, y), mask)
    quad_pts = corners(mask.resize((mask.width // 4, mask.height // 4)), scale=4.0)
    quad_pts = [[px + x, py + y] for px, py in quad_pts]
    out = bg.filter(ImageFilter.GaussianBlur(0.6))
    light = Image.new("L", (OUT_W, OUT_H), 0)
    ImageDraw.Draw(light).ellipse([-OUT_W // 2, -OUT_H // 3, OUT_W, OUT_H // 2], fill=22)
    light = light.filter(ImageFilter.GaussianBlur(120))
    out = Image.composite(Image.new("RGB", out.size, (255, 255, 250)), out, light)
    out.sheet_corners = [quad_pts]
    return out


def save_photos(pdf_path, out_prefix, seed, pages=None):
    doc = fitz.open(pdf_path)
    written = []
    for i in range(len(doc)):
        if pages is not None and i + 1 not in pages:
            continue
        img = photo(page_image(pdf_path, i), seed * 100 + i)
        path = f"{out_prefix}-p{i + 1}.jpg"
        img.save(path, "JPEG", quality=86, optimize=True)
        written.append((path, img.sheet_corners))
    return written


def table(pages, seed, cols=2):
    """Several sheets lying on one table, not overlapping, 2-3 cm apart, read row by row left to right."""
    rng = random.Random(seed)
    sw = 1300
    sheets = []
    for i, page in enumerate(pages):
        sheet = _paper(page, rng)
        sh = int(sw * page.height / page.width)
        sheet = sheet.resize((sw, sh), Image.LANCZOS)
        angle = rng.uniform(-1.6, 1.6)
        mask = Image.new("L", sheet.size, 255).rotate(angle, expand=True, resample=Image.BICUBIC)
        sheets.append((sheet.rotate(angle, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0)), mask))
    gap = rng.randint(140, 190)
    rows = (len(sheets) + cols - 1) // cols
    cw = max(s.width for s, _ in sheets)
    ch = max(s.height for s, _ in sheets)
    W, H = cols * cw + (cols + 1) * gap, rows * ch + (rows + 1) * gap
    bg = Image.new("RGB", (W, H), (232, 229, 214))
    nw, nh = max(1, W // 16), max(1, H // 16)
    noise = Image.new("L", (nw, nh))
    noise.putdata([int(rng.gauss(128, 18)) for _ in range(nw * nh)])
    noise = noise.resize((W, H), Image.BICUBIC)
    bg = Image.composite(Image.new("RGB", bg.size, (214, 210, 192)), bg, noise.point(lambda v: max(0, v - 120)))
    placed = []
    for i, (s, m) in enumerate(sheets):
        r, c = divmod(i, cols)
        x = gap + c * (cw + gap) + rng.randint(-15, 15)
        y = gap + r * (ch + gap) + rng.randint(-15, 15)
        shadow = Image.new("L", (W, H), 0)
        shadow.paste(m, (x + 10, y + 14))
        shadow = shadow.filter(ImageFilter.GaussianBlur(14)).point(lambda v: v * 110 // 255)
        bg = Image.composite(Image.new("RGB", bg.size, (150, 146, 132)), bg, shadow)
        bg.paste(s, (x, y), m)
        placed.append((m, x, y))
    out = bg.filter(ImageFilter.GaussianBlur(0.6))
    scale = 3000 / out.width if out.width > 3000 else 1.0
    if scale != 1.0:
        out = out.resize((int(out.width * scale), int(out.height * scale)), Image.LANCZOS)
    out.sheet_corners = [corners(m.resize((m.width // 4, m.height // 4)), x / 4, y / 4, 4.0 * scale) for m, x, y in placed]
    return out
