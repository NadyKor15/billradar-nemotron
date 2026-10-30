"""The case as one PDF: the total, every finding with its sheets and quotes, what was checked and held
no money, denials to appeal, the letters, and the deadlines. Built on the device with reportlab,
no browser.

Numbers and quotes come from the report and the sources exactly as the screen shows them; this
module only lays them out. Headings follow the chosen language (i18n); findings stay as the
checks wrote them.
"""

from __future__ import annotations

import io
from decimal import ROUND_DOWN
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from billradar import appeal as appeals
from billradar import i18n, plans, writer
from billradar.case import Case
from billradar.checks import Finding, Report

FONTS = Path(__file__).parent / "fonts"
BRAND = Path(__file__).parent / "web" / "static" / "brand"
TEAL = HexColor("#0f5e5a")
GOLD = HexColor("#b8892c")
INK = HexColor("#1d2b2a")
MUTED = HexColor("#5f6b6a")
LINE = HexColor("#d9d4c7")
RED = Color(0.8, 0.15, 0.15)


def _fonts() -> None:
    if "Noto" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("Noto", str(FONTS / "NotoSans-Regular.ttf")))
        pdfmetrics.registerFont(TTFont("Noto-Bold", str(FONTS / "NotoSans-Bold.ttf")))


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName="Noto", fontSize=9.5, leading=13, textColor=INK)
    return {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=10.5, textColor=MUTED),
        "h1": ParagraphStyle("h1", parent=base, fontName="Noto-Bold", fontSize=18, leading=22, textColor=TEAL),
        "hero": ParagraphStyle("hero", parent=base, fontName="Noto-Bold", fontSize=15, leading=19, textColor=INK),
        "h2": ParagraphStyle("h2", parent=base, fontName="Noto-Bold", fontSize=12, leading=16, textColor=TEAL,
                             spaceBefore=10, spaceAfter=4),
        "title": ParagraphStyle("title", parent=base, fontName="Noto-Bold", fontSize=10.5, leading=14),
        "quote": ParagraphStyle("quote", parent=base, fontSize=8.5, leading=11.5, textColor=MUTED, leftIndent=10),
        "sample": ParagraphStyle("sample", parent=base, fontName="Noto-Bold", fontSize=10, textColor=RED),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


def headline(report: Report, lang: str) -> str:
    """Same rule as the case screen: whole dollars, rounded down, so 'up to' never promises more."""
    total = report.total_may_come_off
    if total <= 0:
        return i18n.t("report.nothing", lang)
    whole = total.to_integral_value(rounding=ROUND_DOWN)
    return i18n.t("report.up_to", lang, amount=i18n.money(whole, lang).replace(".00", "").replace(",00", ""))


def _where(f: Finding, lang: str) -> str:
    bits = []
    for e in f.evidence:
        bit = f"{i18n.t('report.sheet', lang)} {e.doc_id}"
        if e.line_no is not None:
            bit += f", {i18n.t('report.line', lang)} {e.line_no}"
        bits.append(bit)
    return " · ".join(dict.fromkeys(bits))


def _quotes(refs: list[str], lang: str, st) -> list:
    out = []
    for ref in dict.fromkeys(refs):
        if not writer.verified(ref):
            continue
        src = writer.source_of(ref)
        page = f", {i18n.t('report.page', lang)} {src['page']}" if writer.paged(ref) else ""
        out.append(_p(f"“{src['quote']}” - {src['title']}{page}", st["quote"]))
    return out


def _finding(f: Finding, lang: str, st, case: Case, with_amount: bool = True, seen: set | None = None) -> KeepTogether:
    from billradar import finding_text
    title, detail = finding_text.text(f, case, lang)  # the family's language, numbers from the finding
    head = title + (f"  ·  {i18n.money(f.amount, lang)}" if with_amount and f.amount is not None else "")
    parts = [_p(head, st["title"]), _p(detail, st["base"])]
    if f.evidence:
        parts.append(_p(_where(f, lang), st["small"]))
    refs = list(dict.fromkeys(list(f.policy_refs) + list(getattr(f, "laws", []) or [])))
    if refs and seen is not None and set(refs) <= seen:  # the same quotes once, not under every bill
        parts.append(_p(i18n.t("report.same_rules", lang), st["quote"]))
    else:
        parts += _quotes(refs, lang, st)
    if seen is not None:
        seen.update(refs)
    parts.append(Spacer(1, 6))
    return KeepTogether(parts)


def build(case: Case, report: Report, letters: list[writer.Letter] | None = None, lang: str = "en",
          replayed: bool = True) -> bytes:
    """`replayed`: the model answers came from recordings, not a live run; the report says so."""
    i18n.check(lang)
    _fonts()
    st = _styles()
    letters = writer.all_letters(case, report) + writer.appeal_letters(case) if letters is None else letters
    story: list = []

    logo = BRAND / "wordmark-cut.png"  # her logo without a background (wordmark.png carries a beige box)
    if logo.exists():
        img = Image(str(logo), mask="auto")
        img._restrictSize(1.7 * inch, 0.5 * inch)
        img.hAlign = "LEFT"
        story.append(img)
        story.append(Spacer(1, 6))
    if case.demo:
        story.append(_p(i18n.t("report.sample", lang), st["sample"]))
    story.append(_p(i18n.t("report.title", lang), st["h1"]))
    sub = [i18n.t("report.sample_case", lang) if case.demo else f"{i18n.t('report.case', lang)} {case.case_id}", case.letterhead,
           f"{i18n.t('report.made', lang)} {i18n.date_text(case.today, lang)}"]
    story.append(_p(" · ".join(x for x in sub if x), st["small"]))
    story.append(Spacer(1, 10))
    story.append(_p(headline(report, lang), st["hero"]))
    if report.apply_by:
        story.append(_p(i18n.t("report.apply_by", lang, date=i18n.date_text(report.apply_by, lang)), st["base"]))
    if case.demo:
        story.append(Spacer(1, 4))
        story.append(_p(i18n.t("report.policy_note", lang), st["small"]))

    sure = [f for f in report.findings if getattr(f, "severity", None) != "borderline"]
    maybe = [f for f in report.findings if getattr(f, "severity", None) == "borderline"] + list(report.worth_asking)
    seen: set = set()  # rule quotes already printed
    if sure:
        story.append(_p(i18n.t("report.findings", lang), st["h2"]))
        story += [_finding(f, lang, st, case, seen=seen) for f in sure]
    if maybe:
        story.append(_p(i18n.t("report.borderline", lang), st["h2"]))
        story.append(_p(i18n.t("report.borderline_note", lang), st["small"]))
        story += [_finding(f, lang, st, case, with_amount=False, seen=seen) for f in maybe]
    if report.no_money:
        story.append(_p(i18n.t("report.no_money", lang), st["h2"]))
        story += [_finding(f, lang, st, case) for f in report.no_money]
    if report.held_back:
        story.append(_p(i18n.t("report.held", lang, n=len(report.held_back)), st["small"]))

    denials = appeals.denials(case)
    if denials:
        story.append(_p(i18n.t("report.appeals", lang), st["h2"]))
        for d in denials:
            plan = plans.load(d.plan_id)
            dl = appeals.deadline(plan, d)
            line = (f"{plan.insurer} · claim {d.claim_ref or '-'} · {', '.join(d.codes)}"
                    + (f" · {i18n.money(d.patient_responsibility, lang)}" if d.patient_responsibility else ""))
            parts = [_p(line, st["title"])]
            if dl:
                parts.append(_p(i18n.t("report.appeal_by", lang, date=i18n.date_text(dl.day, lang)), st["base"]))
                parts += _quotes(dl.refs, lang, st)
            story.append(KeepTogether(parts + [Spacer(1, 6)]))

    if letters:
        story.append(_p(i18n.t("report.letters", lang), st["h2"]))
        rows = [[_p(i18n.t(f"letter.{x.kind}", lang) if f"letter.{x.kind}" in i18n.STRINGS and lang != "en" else x.subject, st["base"]),
                 _p(f"{i18n.t('report.to', lang)}: {x.to}", st["small"])] for x in letters]
        table = Table(rows, colWidths=[3.6 * inch, 3.3 * inch])
        table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story.append(table)

    story.append(_p(i18n.t("report.papers", lang), st["h2"]))
    from billradar.callprep import provider_name
    rows = [[_p(i18n.t(f"report.col.{c}", lang), st["small"]) for c in ("paper", "kind", "from", "date", "amount")]]
    for d in case.documents:
        when = d.statement_date or d.service_from
        bal = d.patient_balance if d.patient_balance is not None else None
        rows.append([_p(d.doc_id, st["base"]), _p(i18n.t(f"kind.{d.kind.value}", lang), st["small"]),
                     _p(provider_name(d.provider), st["small"]), _p(i18n.date_text(when, lang) if when else "", st["small"]),
                     _p(i18n.money(bal, lang) if bal is not None else "", st["small"])])
    if rows:
        table = Table(rows, colWidths=[0.85 * inch, 1.5 * inch, 2.2 * inch, 1.35 * inch, 1.0 * inch], repeatRows=1)
        table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LINEBELOW", (0, 0), (-1, 0), 0.8, TEAL)]))
        story.append(table)

    story.append(Spacer(1, 12))
    story.append(_p(i18n.t("report.replayed" if replayed else "report.models", lang), st["small"]))
    story.append(_p(i18n.t("report.footer", lang), st["small"]))

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Noto", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(LETTER[0] - 0.7 * inch, 0.45 * inch,
                               f"BillRadar · {i18n.t('report.sample_case', lang) if case.demo else case.case_id} · {doc.page}")
        if case.demo:
            canvas.setFillColor(RED)
            canvas.drawString(0.7 * inch, 0.45 * inch, i18n.t("report.sample", lang))
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.75 * inch, title=i18n.t("report.title", lang),
                            author="BillRadar", invariant=1)
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()


def letter_pdf(case: Case, letter: writer.Letter, translation=None) -> bytes:
    """One letter as the family prints and signs it (English). A translation, when given, follows on its
    own page under its note ("for understanding; the English letter is the one sent")."""
    _fonts()
    st = _styles()
    story: list = []
    if case.demo:
        story.append(_p(i18n.t("report.sample", "en"), st["sample"]))
    story.append(_p(i18n.date_text(case.today, "en"), st["base"]))
    story.append(Spacer(1, 10))
    story.append(_p(letter.to, st["base"]))
    story.append(Spacer(1, 10))
    story.append(_p(f"Subject: {letter.subject}", st["title"]))
    story.append(Spacer(1, 6))
    for para in letter.paragraphs:
        story += [_p(para.text, st["base"]), Spacer(1, 6)]
    if letter.attachments:
        story.append(_p("Attached: " + "; ".join(letter.attachments), st["small"]))
    story += [Spacer(1, 10), _p("Sincerely,", st["base"]), Spacer(1, 26)]
    story += [_p(line, st["base"]) for line in writer.signature(case)]
    if translation is not None:
        from reportlab.platypus import PageBreak
        story += [PageBreak(), _p(translation.note, st["sample"]), Spacer(1, 8),
                  _p(translation.subject, st["title"]), Spacer(1, 6)]
        for text in translation.paragraphs:
            story += [_p(text, st["base"]), Spacer(1, 6)]

    def page(canvas, doc):
        if case.demo:
            canvas.saveState()
            canvas.setFont("Noto", 7.5)
            canvas.setFillColor(RED)
            canvas.drawString(0.8 * inch, 0.45 * inch, i18n.t("report.sample", "en"))
            canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.8 * inch, title=letter.subject, invariant=1)
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()
