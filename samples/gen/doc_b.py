"""Document type B: the hospital's itemized bill (4 pages), as a hospital report writer prints it.

Page 1  hospital address, patient block, guarantor block, start of "Hospital Charges"
Page 2  rest of the charges, payments, current balance, "Summary by Rev Code"
Page 3  "SUMMARY OF ACCOUNT AS OF ..." and the standard paragraphs
Page 4  "Providers Associated with your Care" and the separately billing groups
"""
from __future__ import annotations

from .common import (NAVY, Page, d_mdy_nopad, d_mdy_slash, m_dollar_sign_first, m_plain, new_pdf, width,
                     wrap_chars, wrap_words)
from .scenario import REV_CATEGORY, REV_ORDER_BABY, REV_ORDER_MOTHER
from .world import HOSPITAL_SYSTEM as HS, SPECIALTY_GROUPS_B

HEAD_FILL = (0.941, 0.961, 0.988)
HEAD_TEXT = "#0f64b9"
LINK = "#0563c1"
NAVY_LW = 0.25
LEAD = 12.65           # line pitch of 11 pt Arial in this report
ROW_PAD = 0.2
COLS = [36.0, 100.75, 133.11, 202.24, 447.09, 497.49, 575.26]
P1_BOTTOM, PN_BOTTOM = 690.0, 735.0


class Sheet:
    def __init__(self, c, acct, total_pages=4):
        self.c, self.acct, self.total = c, acct, total_pages
        self.n = 0
        self.p = None

    def next_page(self):
        if self.p is not None:
            self.footer()
            self.c.showPage()
        self.n += 1
        self.p = Page(self.c)
        return self.p

    def footer(self):
        p, a = self.p, self.acct
        if self.n == 1:
            p.text(36.0, 695.6, f"Account Number : {a.account_no}   ", size=10)
            p.text(287.7, 695.6, f"Print Date {d_mdy_nopad(a.print_date)}  ", size=10)
            p.text(503.5, 695.6, f"Page 1 of {self.total}", size=10)
            p.text(36.0, 707.1, "Please refer to the account number for all inquiries and correspondence. ", size=10)
            p.text(305.7, 718.9, HS["license_line"], "Helvetica-Bold", 10, align="center")
        else:
            p.text(36.0, 742.7, f"Account Number: {a.account_no}", size=10)
            p.text(287.7, 742.7, f"Print Date {d_mdy_nopad(a.print_date)}  ", size=10)
            p.text(467.5, 742.7, f"Page {self.n} of {self.total}", size=10)
        p.fiction_note()

    @property
    def bottom(self):
        return P1_BOTTOM if self.n == 1 else PN_BOTTOM


def charges_header(p, top):
    h = 2 * LEAD + 0.5
    for x0, x1 in zip(COLS, COLS[1:]):
        p.fill(x0, top, x1, top + h, HEAD_FILL)
    p.hline(COLS[0], COLS[-1], top, NAVY, NAVY_LW)
    p.hline(COLS[0], COLS[-1], top + h, NAVY, NAVY_LW)
    p.vline(COLS[0] + 0.1, top, top + h, NAVY, NAVY_LW)
    p.vline(COLS[-1] - 0.05, top, top + h, NAVY, NAVY_LW)
    t = top + 0.8
    f = "Helvetica-Bold"
    p.text(68.3, t, "Svc Dt", f, 11, HEAD_TEXT, "center")
    p.text(116.9, t, "Rev ", f, 11, HEAD_TEXT, "center")
    p.text(116.9, t + LEAD, "Code", f, 11, HEAD_TEXT, "center")
    p.text(167.7, t, "Px Code", f, 11, HEAD_TEXT, "center")
    p.text(204.2, t, "Description", f, 11, HEAD_TEXT)
    p.text(472.3, t, "Qty", f, 11, HEAD_TEXT, "center")
    p.text(573.0, t, "Amt", f, 11, HEAD_TEXT, "right")
    return top + h


def charge_cells(line):
    px = wrap_chars(line.px, COLS[3] - COLS[2] - 1.6)
    desc = wrap_words(line.desc, COLS[4] - COLS[3] - 3.0)
    return px, desc


def draw_charge_row(p, top, line):
    px, desc = charge_cells(line)
    n = max(len(px), len(desc))
    t = top + ROW_PAD
    p.text(68.4, t, d_mdy_slash(line.svc), align="center")
    p.text(117.25, t, line.rev, align="center")
    for i, s in enumerate(px):
        p.text(167.95 if i == 0 else 167.95, t + i * LEAD, s, align="center")
    for i, s in enumerate(desc):
        p.text(203.4, t + i * LEAD, s)
    p.text(472.15, t, str(line.qty), align="center")
    p.text(574.1, t, m_plain(line.amt), align="right")
    bottom = top + n * LEAD + 0.2
    p.hline(COLS[0], COLS[-1], bottom, NAVY, NAVY_LW)
    p.vline(COLS[0] + 0.1, top, bottom, NAVY, NAVY_LW)
    p.vline(COLS[-1] - 0.05, top, bottom, NAVY, NAVY_LW)
    return bottom


def row_height(line):
    px, desc = charge_cells(line)
    return max(len(px), len(desc)) * LEAD + 0.2


def render(acct, path):
    c = new_pdf(path, f"Itemized statement {acct.account_no}")
    s = Sheet(c, acct)
    p = s.next_page()
    a = acct
    for i, line in enumerate([HS["hospital_caps"], HS["hospital"], HS["street_caps"], HS["city_line"], HS["phone_paren"]]):
        p.text(36.0, 49.7 + i * 12.65, line)
    g = a.guarantor
    p.text(295.3, 126.0, "PATIENT NAME", "Helvetica-Bold")
    p.text(406.0, 125.6, a.patient.last_first_tight())
    p.text(295.3, 144.0, "ADMIT DATE", "Helvetica-Bold")
    p.text(406.0, 143.6, d_mdy_nopad(a.admit))
    p.text(295.3, 156.6, "DISCHARGE DATE:", "Helvetica-Bold")
    p.text(406.0, 156.3, d_mdy_nopad(a.discharge))
    p.text(36.7, 181.9, g.name, "Helvetica-Bold")
    p.text(36.7, 198.4, g.street, "Helvetica-Bold")
    p.text(36.7, 211.1, g.apt + " ", "Helvetica-Bold")
    p.text(36.7, 223.7, g.city_line, "Helvetica-Bold")
    p.text(359.6, 181.6, "Guarantor ID:")
    p.text(481.0, 181.6, " " + g.guarantor_id)
    p.text(359.6, 198.1, "Account Number:")
    p.text(481.0, 198.1, a.account_no)
    p.text(36.0, 261.8, "Hospital Charges", "Helvetica-Bold")

    y = charges_header(p, 286.5)
    for line in a.lines:
        if y + row_height(line) > s.bottom:
            p = s.next_page()
            y = charges_header(p, 49.5)
        y = draw_charge_row(p, y, line)
    # Total row
    if y + LEAD > s.bottom:
        p = s.next_page()
        y = charges_header(p, 49.5)
    p.text(203.4, y + ROW_PAD, "Total Charges", "Helvetica-Bold")
    p.text(574.1, y + ROW_PAD, m_plain(a.total), "Helvetica-Bold", align="right")
    yb = y + LEAD + 0.2
    p.hline(COLS[0], COLS[-1], yb, NAVY, NAVY_LW)
    p.vline(COLS[0] + 0.1, y, yb, NAVY, NAVY_LW)
    p.vline(COLS[-1] - 0.05, y, yb, NAVY, NAVY_LW)
    y = yb

    # Payments, balance, summary by rev code
    need = 13.4 + 25 + 26.2 + 51 + 13.4 + 13.4
    if y + need > s.bottom:
        p = s.next_page()
        y = 36.0
    y += 13.0
    p.text(36.0, y, "Hospital Payments and Adjustments", "Helvetica-Bold")
    y += 24.7
    p.fill(36.0, y, 510.68, y + 13.1, HEAD_FILL)
    p.fill(510.68, y, 575.41, y + 13.1, HEAD_FILL)
    p.text(38.2, y + 0.8, "Description", "Helvetica-Bold", 11, HEAD_TEXT)
    p.text(573.2, y + 0.8, "Amount", "Helvetica-Bold", 11, HEAD_TEXT, "right")
    p.text(37.3, y + 13.4, "Insurance Payments and Adjustments")
    p.text(574.2, y + 13.4, m_plain(a.ins_pmts_adj), align="right")
    for yy in (y, y + 13.1, y + 26.0):
        p.hline(36.0, 575.4, yy, NAVY, NAVY_LW)
    p.vline(36.1, y, y + 26.0, NAVY, NAVY_LW)
    p.vline(575.3, y, y + 26.0, NAVY, NAVY_LW)
    y += 26.0 + 38.0
    # the dark empty bar, then the balance
    p.fill(36.0, y, 445.0, y + 13.5, NAVY)
    p.fill(445.0, y, 574.7, y + 13.5, NAVY)
    p.box(36.2, y + 0.2, 445.0, y + 13.5, "#010101", 0.5)
    p.box(445.0, y + 0.2, 574.4, y + 13.5, "#010101", 0.5)
    p.text(36.7, y + 14.0, "Current Account Balance")
    p.text(573.9, y + 14.0, m_plain(a.balance), align="right")
    p.box(36.2, y + 13.5, 445.0, y + 26.6, "#010101", 0.5)
    p.box(445.0, y + 13.5, 574.4, y + 26.6, "#010101", 0.5)
    y += 26.6 + 12.9

    order = REV_ORDER_BABY if a.kind == "Newborn" else REV_ORDER_MOTHER
    sums = {r: 0.0 for r in order}
    for line in a.lines:
        sums[line.rev] = sums.get(line.rev, 0.0) + line.amt
    labels = []
    for r in order:
        labels += wrap_words(REV_CATEGORY[r], 444.3 - 36.7 - 4)
    values = [("$" + m_plain(sums[r])) if i == 0 else m_plain(sums[r]) for i, r in enumerate(order)]
    # one table row with two multi-line cells: labels wrap, values do not, so they drift apart
    def summary_head(p, y):
        p.fill(36.0, y, 444.3, y + 13.4, NAVY)
        p.fill(444.3, y, 574.7, y + 13.4, NAVY)
        p.text(36.7, y + 0.8, "Summary by Rev Code", color="#ffffff")
        p.text(445.0, y + 0.8, "Dollars by Rev Code", color="#ffffff")
        return y + 13.4

    y = summary_head(p, y)
    li = vi = 0
    while li < len(labels) or vi < len(values):
        room = int((s.bottom - y - 2) // LEAD)
        if room <= 0:
            p = s.next_page()
            y = 49.5
            continue
        take_l = labels[li:li + room]
        take_v = values[vi:vi + room]
        top = y
        for k, t in enumerate(take_l):
            p.text(36.7, top + 0.5 + k * LEAD, t)
        for k, t in enumerate(take_v):
            p.text(574.0, top + 0.5 + k * LEAD, t, align="right")
        li += len(take_l)
        vi += len(take_v)
        done = li >= len(labels) and vi >= len(values)
        n_lines = max(len(take_l), len(take_v)) + (1 if done else 0)
        yb = top + n_lines * LEAD + (1.6 if done else 0.6)
        if not done and yb > s.bottom:
            yb = s.bottom
        p.box(36.2, top, 444.3, yb, "#010101", 0.5)
        p.box(444.3, top, 574.4, yb, "#010101", 0.5)
        y = yb
        if not done:
            p = s.next_page()
            y = 49.5

    # Page 3: summary of account
    while s.n < 2:
        p = s.next_page()
    p = s.next_page()
    rows = [
        (f"SUMMARY OF ACCOUNT AS OF {d_mdy_nopad(a.print_date)}", "AMOUNT"),
        ("TOTAL CHARGES", m_dollar_sign_first(a.total)),
        ("   INS PAYMENTS AND ADJUSTMENTS", m_dollar_sign_first(a.ins_pmts_adj)),
        ("   PATIENT PAYMENTS AND ADJUSTMENTS ", "$ " + m_plain(a.patient_pmts) if a.patient_pmts == 0 else m_dollar_sign_first(a.patient_pmts)),
        ("CURRENT ACCOUNT BALANCE ", m_dollar_sign_first(a.balance)),
        ("", ""),
        ("AMOUNT DUE FROM INSURANCE", m_dollar_sign_first(a.due_from_insurance) if a.due_from_insurance else "$"),
        ("AMOUNT DUE FROM PATIENT", m_dollar_sign_first(a.due_from_patient) if round(a.due_from_patient, 2) else "$"),
    ]
    y = 62.4
    for label, val in rows:
        p.text(36.7, y + 0.5, label)
        p.text(574.7, y + 0.5, val, align="right")
        p.box(36.2, y, 304.9, y + 13.15, "#010101", 0.5)
        p.box(304.9, y, 575.2, y + 13.15, "#010101", 0.5)
        y += 13.15
    p.box(36.2, y, 304.9, y + 25.8, "#010101", 0.5)
    p.box(304.9, y, 575.2, y + 25.8, "#010101", 0.5)
    p.text(36.0, 219.2, f"Thank you for choosing {HS['name']} for your healthcare needs.")
    if round(a.due_from_patient, 2) > 0:
        p.text(36.0, 244.5, "Please Pay the Amount Due from Patient immediately.")
    y = p.para(36.0, 269.8, [
        ("For questions about your account or to apply for financial assistance, please contact Patient Financial Services ", "Helvetica"),
        ("by telephone at ", "Helvetica"), (HS["phone_dash"], "Helvetica-Bold"), (" or by email at ", "Helvetica"),
        (HS["email"], "Helvetica-Bold", LINK, True), (".", "Helvetica-Bold"),
        ("  To view our Financial Assistance Policy, please visit our website at ", "Helvetica"),
        (HS["fap_url"], "Helvetica", LINK, True)], 541.5)
    p.para(36.0, y + 12.65, [("If you do not agree with the patient balance due, please contact your HMO or Insurance Carrier directly to discuss.",
                               "Helvetica")], 541.5)

    # Page 4: providers
    p = s.next_page()
    p.text(305.7, 75.4, f" Providers Associated with your Care for this account {a.account_no}", "Helvetica-Bold", align="center")
    pv = a.providers
    xs = [36.2, 228.5, 450.9, 574.4]

    def prow(top, h, left_lines, mid_lines, right_lines, left_fonts=None):
        for k, t in enumerate(left_lines):
            f = (left_fonts or {}).get(k, "Helvetica")
            p.text(36.7, top + 0.5 + k * LEAD, t, f)
        for k, t in enumerate(mid_lines):
            p.text(338.5, top + 0.5 + k * LEAD, t, align="center")
        for k, t in enumerate(right_lines):
            p.text(513.3, top + 0.5 + k * LEAD, t, align="center")
        for x0, x1 in zip(xs, xs[1:]):
            p.box(x0, top, x1, top + h, "#010101", 0.5)
        return top + h

    def spec(sp):
        return wrap_words(sp, 110) if sp else []

    y = 125.6
    y = prow(y, 25.8, ["Admitting Physician:"], [pv["admitting"][0]] if pv["admitting"][0] else [], spec(pv["admitting"][1]))
    y = prow(y, 25.8, ["Attending Physician:"], [pv["attending"][0]] if pv["attending"][0] else [], spec(pv["attending"][1]))
    y = prow(y, 25.8, ["Anesthesiologist: "], [pv["anesthesiologist"][0]] if pv["anesthesiologist"][0] else [], [])
    y = prow(y, 76.4, ["Procedure Performing Provider(s): ", "(The physician name will appear ",
                       "multiple times if he/she performed ", "multiple services)"], pv["procedure"], [],
             {1: "Helvetica-Oblique", 2: "Helvetica-Oblique", 3: "Helvetica-Oblique"})
    cons = pv["consultants"]
    y = prow(y, 25.8, ["Consultants and other Providers"], [cons[0]] if cons[0] else [], [cons[1]] if cons[1] else [])
    p.text(36.0, 318.7, "For more Provider contact information, please go to our website to Find a Physician at", "Helvetica-Bold")
    p.text(36.0, 331.4, HS["physicians_url"], "Helvetica-Bold")
    p.text(36.0, 356.7, "If you had services provided by the following specialty providers, they will bill you separately. Below is",
           "Helvetica-Bold")
    p.text(36.0, 369.3, "their billing contact information", "Helvetica-Bold")
    xs2 = [36.0, 228.5, 453.3, 574.7]
    top = 394.0
    for x0, x1 in zip(xs2, xs2[1:]):
        p.fill(x0, top, x1, top + 19.5, (0.753, 0.753, 0.753))
        p.box(x0, top, x1, top + 19.5, "#0000ff", 0.5)
    p.text(36.7, 395.1, "Company Name ", "Helvetica-Bold")
    p.text(340.9, 395.1, "Professional Service", "Helvetica-Bold", align="center")
    p.text(514.0, 395.1, "Telephone Number", "Helvetica-Bold", align="center")
    y = top + 19.5
    for name, service, phone in SPECIALTY_GROUPS_B:
        p.text(36.7, y + 0.5, name)
        p.text(340.9, y + 0.5, service, align="center")
        p.text(514.0, y + 0.5, phone, align="center")
        for x0, x1 in zip(xs2, xs2[1:]):
            p.box(x0, y, x1, y + 13.15, "#010101", 0.5)
        y += 13.15
    s.footer()
    c.showPage()
    c.save()
    return s.n
