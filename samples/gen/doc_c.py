"""Document type C: the insurer's Explanation of Benefits (EOB).

First page: return address, logo, "Explanation of Benefits", envelope window, Customer Service
and Participant Information cards. Then claim blocks: claim number and patient on the left,
a dark Provider / Member ID band on the right, a 13-column table, Claim Totals, Patient
Responsibility, and the dark "Your Responsibility" pill. Code legends close the last page.
Claims flow across pages the way the insurer's print run does it: rows may continue on the
next page under a repeated claim header, and a claim's totals or pill can land alone at the
top of the next page.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from reportlab.lib.colors import HexColor

from .common import Page, m_dollar, m_dollar_sign_first, new_pdf, width, wrap_words
from .world import INSURER

BLUE = "#1d4f7c"
RED = "#c00000"
F, FB = "Helvetica", "Helvetica-Bold"

# column centres (pt) and the table's outer edges
X0, X1 = 54.0, 594.0
COLS = [("Dates of\nService", 78), ("Service\nCode", 113), ("Total\nCharge", 145), ("Discount\nAmount", 190),
        ("Allowed\nAmount", 233), ("Other\nAdjustments", 274), ("Other Plan\nPayments", 315),
        ("Not\nCovered", 350), ("Co-Pay\nAmount", 383), ("Deductible\nAmount", 420),
        ("Co-Insurance\nAmount", 471), ("Benefit\nAmount", 522), ("Reason\nCode", 571)]
MONEY_KEYS = ["charge", "discount", "allowed", "other_adj", "other_plan", "not_covered", "copay", "deductible",
              "coins", "benefit"]

# Plain-language descriptions written for these samples (codes from the public CPT and UB-04 lists).
SERVICE_CODES = {
    "0112": "Private Room, Maternity Hosp IP", "0170": "Newborn Nursery Hosp IP",
    "0251": "Drugs, Generic Hosp IP", "0272": "Sterile Supplies Hosp IP", "0302": "Lab, Immunology Hosp IP",
    "0306": "Lab, Microbiology Hosp IP", "0309": "Lab, Other Hosp IP", "0379": "Anesthesia, Other Hosp IP",
    "0471": "Audiology, Diagnostic Hosp IP", "0637": "Self-Administered Drugs Hosp IP",
    "0722": "Delivery Room Hosp IP",
    "01960": "Anesthesia, Vaginal Delivery Med IP",
    "59410": "Delivery and Postpartum Care Med IP", "76815": "Ultrasound, Pregnant Uterus, Limited Med Office",
    "99222": "Hospital Admission Visit Med IP", "99231": "Hospital Follow-up Visit Med IP",
    "99238": "Hospital Discharge Day Med IP", "99460": "Newborn Initial Care Med IP",
    "99462": "Newborn Follow-up Care Med IP",
    "80048": "Lab Panel, Basic Metabolic Med IP", "80055": "Lab Panel, Obstetric Med OP",
    "81003": "Urinalysis, Automated Med OP", "82947": "Glucose, Quantitative Med IP",
    "83735": "Magnesium Med IP", "84132": "Potassium, Serum Med IP", "85384": "Fibrinogen Activity Med IP",
    "85610": "Prothrombin Time Med IP", "85730": "Thromboplastin Time, Partial Med IP",
    "86762": "Rubella Antibody Med OP", "86780": "Treponema Pallidum Antibody Med OP",
    "86880": "Antihuman Globulin Test, Direct Med IP", "87086": "Urine Culture, Colony Count Med OP",
    "87491": "Chlamydia, Amplified Probe Med OP", "87591": "Gonorrhea, Amplified Probe Med OP",
}
# Reason codes and wording invented for these samples.
REASON_CODES = {
    "A27": "Service is not consistent with the age of the member on file. Please check the member's information.",
    "CDA": "Allowed amount reflects the provider's contract with the plan",
    "CPA": "Paid at the contracted rate; member share applied per plan design",
    "FS1": "Allowed according to the plan's laboratory fee schedule",
}


@dataclass
class EOBLine:
    dos_from: object
    dos_to: object
    code: str
    charge: float
    discount: float
    allowed: float
    reason: str
    copay: float = 0.0
    deductible: float = 0.0
    coins: float = 0.0
    benefit: float = 0.0
    not_covered: float = 0.0
    other_adj: float = 0.0
    other_plan: float = 0.0


@dataclass
class Claim:
    claim_no: str
    patient: str        # printed caps, two spaces between given name and surname
    provider: str
    member_id: str
    lines: list
    totals_override: dict = field(default_factory=dict)   # e.g. the one-cent rounding in a column total
    reversal: bool = False

    def total(self, k):
        if k in self.totals_override:
            return self.totals_override[k]
        return round(sum(getattr(l, k) for l in self.lines), 2)

    @property
    def patient_resp(self):
        return round(sum(l.copay + l.deductible + l.coins + l.not_covered for l in self.lines), 2)

    @property
    def payment(self):
        return self.total("benefit")


@dataclass
class EOB:
    sheet_id: str
    mail_code: str      # H2PX123M000000-style code printed over the window
    envelope: str       # HLNX - H2PX123M - 000000 - 001 OF 001
    print_date: object
    member_caps: str
    street_caps: str
    city_caps: str
    group_number: str
    claims: list
    tag: str = ""
    trace: list = field(default_factory=list)  # filled by render(): (page, claim index, line index)


def _money_line(v):
    return m_dollar_sign_first(v)


def _dates(l):
    return [l.dos_from.strftime("%m/%d/%Y") + "-", l.dos_to.strftime("%m/%d/%Y")]


class Flow:
    TOP_FIRST, TOP_NEXT, BOTTOM = 312.0, 52.0, 722.0

    def __init__(self, c, eob, total_pages):
        self.c, self.eob, self.total = c, eob, total_pages
        self.n = 0
        self.p = None
        self.y = 0

    def new_page(self):
        if self.p is not None:
            self.footer()
            self.c.showPage()
        self.n += 1
        self.p = Page(self.c)
        if self.n == 1:
            first_page_header(self.p, self.eob, self.total)
            self.y = self.TOP_FIRST
        else:
            self.p.text(574.0, 30.0, f"Page {self.n} of {self.total}", "Times-Bold", 7.5, align="right")
            self.y = self.TOP_NEXT

    def footer(self):
        p = self.p
        p.text(306.0, 742.0, f"Page {self.n} of {self.total}", FB, 6.5, align="center")
        p.text(306.0, 751.0, self.eob.print_date.strftime("%B %d, %Y").replace(" 0", " "), FB, 6.5, align="center")
        p.text(549.0, 745.0, self.eob.sheet_id, F, 6.5)
        side_codes(p, self.eob, self.n)
        p.fiction_note()

    def fits(self, h):
        return self.y + h <= self.BOTTOM


def side_codes(p, eob, n):
    """2D codes and the vertical envelope string on the left margin."""
    c = p.c
    p.barcode("ECC200DataMatrix", f"{eob.mail_code}{n:03d}", 18, 96, w=20, h=20)
    if n == 1:
        p.barcode("ECC200DataMatrix", f"{eob.sheet_id}{eob.mail_code}", 9, 47, w=9, h=9)
    p.barcode("ECC200DataMatrix", f"{eob.sheet_id}{n}", 9, 422, w=8, h=8)
    c.saveState()
    c.setFont(F, 5.2)
    c.setFillColorRGB(0.15, 0.15, 0.15)
    c.translate(18, 792 - 378)
    c.rotate(90)
    c.drawString(0, 0, eob.envelope)
    c.restoreState()


def logo(p, x_right, top):
    """Invented insurer mark: three waving stripes (red / blue) over the wordmark."""
    c = p.c
    cx = x_right - 75
    for i, col in enumerate(["#c8202f", "#1d4f7c", "#c8202f"]):
        y = 792 - top - i * 2.6
        c.setStrokeColor(HexColor(col))
        c.setLineWidth(1.6)
        path = c.beginPath()
        path.moveTo(cx - 20, y)
        path.curveTo(cx - 8, y + 3, cx + 4, y - 3, cx + 22, y + 1)
        c.drawPath(path, stroke=1, fill=0)
    p.fill(cx - 21, top - 3.5, cx - 13, top + 2.0, BLUE)
    p.runs(x_right - 150, top + 10, [("Harborlight ", "Times-Bold", BLUE), ("Health", "Times-BoldItalic", BLUE)], 14.5)
    p.text(x_right - 75, top + 25, "Next", "Helvetica-BoldOblique", 12.5, "#111111", "center")
    p.text(x_right - 75, top + 40, INSURER["legal_small"], F, 3.9, "#222222", "center")


def first_page_header(p, eob, total_pages):
    p.text(300.0, 30.0, eob.sheet_id, F, 5.5, align="center")
    lines = [INSURER["name"], INSURER["legal"], INSURER["street"], INSURER["city"]]
    for i, s in enumerate(lines):
        p.text(81.0, 47.0 + i * 10.6, s, "Times-Bold" if i == 0 else "Times-Roman", 8.6)
    logo(p, 530.0, 32.0)
    p.text(443.0, 88.0, "Explanation of Benefits", FB, 13.5, BLUE, "center")
    p.text(443.0, 104.5, "RETAIN FOR TAX PURPOSES", FB, 6.3, RED, "center")
    p.text(443.0, 113.0, "THIS IS NOT A BILL", FB, 8.0, RED, "center")
    p.text(66.0, 136.0, f"{eob.mail_code} HLNX", F, 6.6)
    for i, s in enumerate([eob.member_caps, eob.street_caps, eob.city_caps]):
        p.text(72.0, 152.0 + i * 11.7, s, F, 6.6)
    # cards
    x0, x1 = 309.0, 584.0
    p.round_box(x0, 125.0, x1, 205.0, 3, BLUE, 0.9)
    p.fill(x0, 125.0, x1, 140.0, BLUE, 3)
    p.fill(x0, 133.0, x1, 140.0, BLUE)
    p.text((x0 + x1) / 2, 128.6, "Customer Service", FB, 8.0, "#ffffff", "center")
    p.text((x0 + x1) / 2, 166.0, "For Member Service, please call the number on the back of", FB, 7.3, "#111111", "center")
    p.text((x0 + x1) / 2, 175.6, "your ID card", FB, 7.3, "#111111", "center")
    p.round_box(x0, 197.0, x1, 272.0, 3, BLUE, 0.9)
    p.fill(x0, 197.0, x1, 212.0, BLUE, 3)
    p.fill(x0, 205.0, x1, 212.0, BLUE)
    p.text((x0 + x1) / 2, 200.6, "Participant Information", FB, 8.0, "#ffffff", "center")
    for i, (k, v) in enumerate([("Group Number:", eob.group_number), ("Group Name:", INSURER["group_name"]),
                                ("Member:", eob.member_caps), ("Date:", eob.print_date.strftime("%m/%d/%Y"))]):
        p.text(377.0, 220.0 + i * 11.8, k, FB, 8.0, "#111111", "right")
        p.text(388.0, 220.0 + i * 11.8, v, F, 8.0, "#111111")
    p.text(574.0, 294.0, f"Page 1 of {total_pages}", "Times-Bold", 7.5, align="right")


def claim_head(p, y, cl):
    """Claim number / patient on the left, the dark provider band on the right, then the column header."""
    p.text(66.0, y + 4.0, f"Claim #: {cl.claim_no}", FB, 7.3)
    p.text(66.0, y + 17.0, f"Patient: {cl.patient}", FB, 7.3)
    p.fill(310.0, y, 594.0, y + 29.0, BLUE, 2.5)
    prov = cl.provider
    while width(f"Provider: {prov}", FB, 7.3) > 270:
        prov = prov[:-1]
    p.text(315.0, y + 4.5, f"Provider: {prov}", FB, 7.3, "#ffffff")
    p.text(315.0, y + 18.0, f"Member ID:  {cl.member_id}", FB, 7.3, "#ffffff")
    top = y + 29.0
    for label, cx in COLS:
        a, b = label.split("\n")
        p.text(cx, top + 4.0, a, FB, 5.6, "#111111", "center")
        p.text(cx, top + 11.0, b, FB, 5.6, "#111111", "center")
    return top, top + 20.0


ROW_H = 17.4


def claim_row(p, y, l):
    d = _dates(l)
    p.text(57.0, y + 3.0, d[0], F, 5.8, "#111111")
    p.text(57.0, y + 9.6, d[1], F, 5.8, "#111111")
    vals = [l.code] + [_money_line(getattr(l, k)) for k in MONEY_KEYS] + [l.reason]
    for (label, cx), v in zip(COLS[1:], vals):
        if label.startswith("Benefit"):
            p.text(cx + 21, y + 3.6, v, F, 5.9, "#111111", "right")
        else:
            p.text(cx, y + 3.6, v, F, 5.9, "#111111", "center")
    p.hline(X0, X1, y + ROW_H, BLUE, 0.9)
    return y + ROW_H


def claim_totals(p, y, cl, standalone=False):
    """Claim Totals row, Patient Responsibility row. Returns new y."""
    h = 9.0 + 21.0
    if standalone:
        p.round_box(X0, y, X1, y + h, 3, BLUE, 0.9)
    p.hline(X0, X1, y, BLUE, 1.6)
    p.text(118.0, y + 1.5, "Claim Totals", FB, 5.9, "#111111", "right")
    p.vline(122.0, y, y + h, BLUE, 0.9)
    neg = cl.reversal
    for (label, cx), k in zip(COLS[2:12], MONEY_KEYS):
        v = cl.total(k)
        s = (m_dollar(v) if k == "discount" else m_dollar_sign_first(v)) if neg else m_dollar_sign_first(v)
        if label.startswith("Benefit"):
            p.text(cx + 21, y + 1.5, s, FB, 5.9, "#111111", "right")
        else:
            p.text(cx, y + 1.5, s, FB, 5.9, "#111111", "center")
    p.hline(X0, X1, y + 9.0, BLUE, 1.6)
    p.text(118.0, y + 11.5, "Patient", FB, 5.9, "#111111", "right")
    p.text(118.0, y + 19.0, "Responsibility:", FB, 5.9, "#111111", "right")
    p.text(127.0, y + 15.0, m_dollar_sign_first(cl.patient_resp), F, 6.2, "#111111")
    p.text(560.0, y + 12.0, "Other Carrier Adjustments: $0.00", FB, 6.2, "#111111", "right")
    p.text(560.0, y + 20.0, f"Total Payment Amount: {m_dollar_sign_first(cl.payment)}", FB, 6.2, "#111111", "right")
    return y + h


def pill(p, y, cl):
    p.fill(X0, y, X0 + 145.0, y + 21.0, BLUE, 2.5)
    p.text(X0 + 5.0, y + 7.0, f"Your Responsibility:  {m_dollar_sign_first(cl.patient_resp)}", FB, 6.4, "#ffffff")
    return y + 21.0


def legends(p, y, codes, reasons):
    def box(x0, x1, title, items, code_x, desc_x, desc_w):
        rows = []
        for code, desc in items:
            parts = wrap_words(desc, desc_w, F, 6.4)
            rows.append((code, parts))
        h = 12.0 + sum(len(r[1]) for r in rows) * 9.2 + 3
        p.round_box(x0, y, x1, y + h, 3, BLUE, 0.9)
        p.fill(x0, y, x0 + 140.0, y + 11.5, BLUE, 2)
        p.text(x0 + 4.0, y + 2.5, title, FB, 7.0, "#ffffff")
        yy = y + 13.0
        for code, parts in rows:
            p.text(code_x, yy, code, F, 6.4, "#111111")
            for k, part in enumerate(parts):
                p.text(desc_x, yy + k * 9.2, part, F, 6.4, "#111111")
            yy += len(parts) * 9.2
        return h
    h1 = box(X0 + 4, 311.0, "Service Code Description", [(c, SERVICE_CODES[c]) for c in codes], X0 + 7, 105.0, 200)
    h2 = box(316.0, X1, "Reason Code Description", [(c, REASON_CODES[c]) for c in reasons], 319.0, 359.0, 228)
    return y + max(h1, h2)


def _layout(c, eob, total_pages):
    """Run the page flow. Returns pages used. With c=None nothing is drawn (dry run for page count)."""
    fl = Flow(c, eob, total_pages)
    draw = c is not None
    if not draw:
        fl.new_page = lambda: _dry_new_page(fl)
        fl.footer = lambda: None
    fl.new_page()
    p = fl.p if draw else _NullPage()
    trace = []    # (page, claim index, line index) for every row drawn: which lines sit on which page
    for ci, cl in enumerate(eob.claims):
        if not fl.fits(29 + 20 + ROW_H):
            fl.new_page(); p = fl.p if draw else _NullPage()
        fl.y += 6.0
        _, fl.y = claim_head(p, fl.y, cl)
        table_top = fl.y - 20.0
        for li, l in enumerate(cl.lines):
            if not fl.fits(ROW_H):
                p.round_box(X0, table_top, X1, fl.y, 3, BLUE, 0.9)
                fl.new_page(); p = fl.p if draw else _NullPage()
                fl.y += 6.0
                _, fl.y = claim_head(p, fl.y, cl)
                table_top = fl.y - 20.0
            fl.y = claim_row(p, fl.y, l)
            trace.append((fl.n, ci, li))
        if fl.fits(30.0):
            fl.y = claim_totals(p, fl.y, cl)
            p.round_box(X0, table_top, X1, fl.y, 3, BLUE, 0.9)
        else:
            p.round_box(X0, table_top, X1, fl.y, 3, BLUE, 0.9)
            fl.new_page(); p = fl.p if draw else _NullPage()
            fl.y = claim_totals(p, fl.y, cl, standalone=True)
        if not fl.fits(36.0):
            fl.new_page(); p = fl.p if draw else _NullPage()
        fl.y += 13.0
        fl.y = pill(p, fl.y, cl) + 22.0
    codes, reasons = [], []
    for cl in eob.claims:
        for l in cl.lines:
            if l.code not in codes:
                codes.append(l.code)
            for r in l.reason.split():
                if r not in reasons:
                    reasons.append(r)
    codes.sort(key=lambda s: (len(s) == 5, s))
    reasons.sort(key=lambda s: (not s[0].isdigit(), s.upper()))
    need = 16 + max(len(codes), sum(2 if len(REASON_CODES[r]) > 60 else 1 for r in reasons)) * 9.2
    if not fl.fits(need):
        fl.new_page(); p = fl.p if draw else _NullPage()
    legends(p, fl.y + 4.0, codes, reasons)
    if draw:
        fl.footer()
        c.showPage()
    if draw:
        eob.trace = trace
    return fl.n


class _NullPage:
    def __getattr__(self, name):
        return lambda *a, **k: 0
    c = None


def _dry_new_page(fl):
    fl.n += 1
    fl.p = None
    fl.y = Flow.TOP_FIRST if fl.n == 1 else Flow.TOP_NEXT


def page_count(eob):
    return _layout(None, eob, 0)


def render(eob, path):
    n = page_count(eob)
    c = new_pdf(path, f"Explanation of Benefits {eob.print_date:%m/%d/%Y}")
    _layout(c, eob, n)
    c.save()
    return n
