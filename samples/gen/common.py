"""Drawing helpers shared by every document template.

Coordinates are in PDF points measured from the TOP-LEFT corner of a US Letter page
(612 x 792), the way a layout is usually measured off a printed sheet. `Page.text`
takes the TOP of the text box (not the baseline), so positions can be copied from a
ruler without thinking about font ascent.
"""
from __future__ import annotations

import re
import textwrap

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

W, H = letter
ASCENT = 0.905  # Arial/Helvetica ascender as a share of the font size

FICTION_NOTE = "SAMPLE - FICTIONAL TEST DOCUMENT - not a real bill. All names, numbers and organizations are invented."

BOLD = {"Helvetica": "Helvetica-Bold", "Times-Roman": "Times-Bold", "Courier": "Courier-Bold"}
ITALIC = {"Helvetica": "Helvetica-Oblique", "Times-Roman": "Times-Italic", "Courier": "Courier-Oblique",
          "Helvetica-Bold": "Helvetica-BoldOblique", "Times-Bold": "Times-BoldItalic"}


def rgb(v):
    if isinstance(v, Color):
        return v
    if isinstance(v, str):
        return HexColor(v)
    return Color(*v)


def width(s, font="Helvetica", size: float = 11.0):
    return stringWidth(s, font, size)


def wrap_words(s, max_w, font="Helvetica", size: float = 11.0):
    """Greedy word wrap the way a report writer does it: break after spaces or hyphens."""
    out, cur = [], ""
    for word in re.split(r"(?<=[ -])", s):
        if cur and width((cur + word).rstrip(), font, size) > max_w:
            out.append(cur.rstrip())
            cur = word
        else:
            cur += word
    if cur.strip() or not out:
        out.append(cur.rstrip())
    return out


def wrap_chars(s, max_w, font="Helvetica", size: float = 11.0):
    """Break a token that has no spaces at the last character that still fits (long codes do this on real bills)."""
    out, cur = [], ""
    for ch in s:
        if cur and width(cur + ch, font, size) > max_w:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return out


class Page:
    """One sheet. Wraps a reportlab canvas with top-down coordinates."""

    def __init__(self, c: canvas.Canvas):
        self.c = c

    def text(self, x, top, s, font="Helvetica", size: float = 11.0, color: object = black, align="left", char_space=0):
        c = self.c
        c.setFillColor(rgb(color))
        c.setFont(font, size)
        y = H - top - ASCENT * size
        if char_space:
            c.saveState()
            t = c.beginText()
            t.setFont(font, size)
            t.setCharSpace(char_space)
            w = width(s, font, size) + char_space * max(len(s) - 1, 0)
            x0 = x - w if align == "right" else x - w / 2 if align == "center" else x
            t.setTextOrigin(x0, y)
            t.textOut(s)
            c.drawText(t)
            c.restoreState()
            return w
        if align == "right":
            c.drawRightString(x, y, s)
        elif align == "center":
            c.drawCentredString(x, y, s)
        else:
            c.drawString(x, y, s)
        return width(s, font, size)

    def runs(self, x, top, parts, size: float = 11.0):
        """Draw consecutive runs on one line: parts = [(text, font, color, underline)]."""
        for part in parts:
            s, font = part[0], part[1]
            color = part[2] if len(part) > 2 else black
            underline = len(part) > 3 and part[3]
            w = self.text(x, top, s, font, size, color)
            if underline:
                self.hline(x, x + w, top + size * 1.02, color, 0.6)
            x += w
        return x

    def para(self, x, top, runs, max_w, leading=12.65, size: float = 11.0):
        """Flow styled runs [(text, font, color, underline)] into lines no wider than max_w. Returns next top."""
        words = []
        for r in runs:
            font = r[1]
            color = r[2] if len(r) > 2 else black
            ul = len(r) > 3 and r[3]
            for w in re.split(r"(?<= )", r[0]):
                if w:
                    words.append((w, font, color, ul))
        lines, cur, cur_w = [], [], 0.0
        for w in words:
            ww = width(w[0], w[1], size)
            if cur and cur_w + width(w[0].rstrip(), w[1], size) > max_w:
                lines.append(cur)
                cur, cur_w = [], 0.0
            cur.append(w)
            cur_w += ww
        if cur:
            lines.append(cur)
        y = top
        for ln in lines:
            self.runs(x, y, ln, size)
            y += leading
        return y

    def hline(self, x0, x1, y, color: object = black, lw=0.5):
        c = self.c
        c.setStrokeColor(rgb(color))
        c.setLineWidth(lw)
        c.line(x0, H - y, x1, H - y)

    def vline(self, x, y0, y1, color: object = black, lw=0.5):
        c = self.c
        c.setStrokeColor(rgb(color))
        c.setLineWidth(lw)
        c.line(x, H - y0, x, H - y1)

    def box(self, x0, y0, x1, y1, color: object = black, lw=0.5):
        c = self.c
        c.setStrokeColor(rgb(color))
        c.setLineWidth(lw)
        c.rect(x0, H - y1, x1 - x0, y1 - y0, stroke=1, fill=0)

    def fill(self, x0, y0, x1, y1, color, radius=0):
        c = self.c
        c.setFillColor(rgb(color))
        if radius:
            c.roundRect(x0, H - y1, x1 - x0, y1 - y0, radius, stroke=0, fill=1)
        else:
            c.rect(x0, H - y1, x1 - x0, y1 - y0, stroke=0, fill=1)

    def round_box(self, x0, y0, x1, y1, radius, color: object = black, lw=0.5, fill=None):
        c = self.c
        c.setStrokeColor(rgb(color))
        c.setLineWidth(lw)
        if fill is not None:
            c.setFillColor(rgb(fill))
        c.roundRect(x0, H - y1, x1 - x0, y1 - y0, radius, stroke=1, fill=1 if fill is not None else 0)

    def barcode(self, kind, value, x, top, w=None, h=None, **kw):
        d = createBarcodeDrawing(kind, value=value, **kw)
        sx = (w / d.width) if w else 1
        sy = (h / d.height) if h else sx
        d.scale(sx, sy)
        d.width, d.height = d.width * sx, d.height * sy
        renderPDF.draw(d, self.c, x, H - top - d.height)
        return d.width, d.height

    def fiction_note(self):
        """Small grey line on every sheet: the document is invented. Kept on purpose (public repo, printed copies)."""
        self.text(W / 2, 776, FICTION_NOTE, "Helvetica", 5.5, "#9a9a9a", "center")


def new_pdf(path, title):
    c = canvas.Canvas(str(path), pagesize=letter, pageCompression=1)
    c.setTitle(title)
    c.setAuthor("BillRadar sample generator")
    c.setSubject("Fictional test document")
    return c


# ----- money and dates, in the exact flavours different billing systems print -----

def m_dollar(x):
    """$1,234.00 / -$1,234.00"""
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def m_dollar_sign_first(x):
    """$1,234.00 / $-1,234.00 (sign after the dollar)"""
    return f"$-{abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def m_plain(x):
    """1,234.00 / -1,234.00"""
    return f"-{abs(x):,.2f}" if x < 0 else f"{x:,.2f}"


def m_paren(x):
    """$1,234.00 / ($1.23)"""
    return f"(${abs(x):,.2f})" if x < 0 else f"${x:,.2f}"


def m_bare(x):
    """123.45 without $ and without thousands separator"""
    return f"{x:.2f}"


def d_mdy_slash(d):
    return d.strftime("%m/%d/%Y")


def d_mdy_short(d):
    return d.strftime("%m/%d/%y")


def d_mdy_nopad(d):
    return f"{d.month}/{d.day}/{d.year}"


def d_mon_dd(d):
    return d.strftime("%b %d, %Y")


def d_month_d(d):
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def dedent(s):
    return textwrap.dedent(s).strip()


NAVY = "#000080"
WHITE = white
BLACK = black
