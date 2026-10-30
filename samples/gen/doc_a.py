"""Document type A: the hospital system's guarantor statement (Patient Financial Services).

Two template years, as the hospital really changed its layout:
  "2026"  one account, light table headers (blue text on pale blue), Insurance Balance column,
          green insurance line and blue patient balance line
  "2024"  several accounts of one guarantor (mother + newborn), dark table headers with white
          text, "Balance Forward" rows, no Insurance Balance column
Page 1 carries the pay-by-card coupon, guarantor and remit blocks, how-to-pay bullets, the
detail and summary tables and Questions; the separately billing groups spill onto page 2.
"""
from __future__ import annotations

from datetime import timedelta

from .common import Page, d_mdy_short, m_dollar, new_pdf, width
from .world import HOSPITAL_SYSTEM as HS, SPECIALTY_GROUPS_A

PLAQUE = (0.0, 0.251, 0.502)
PALE = (0.941, 0.961, 0.988)
HEAD_TEXT = "#0f64b9"
GREEN = "#20733f"
DARK_HEAD = "#4a6894"
NAVY_LINE = "#000080"
F, FB = "Helvetica", "Helvetica-Bold"
S = 10.0


def logo(p, x, top):
    """Invented mark: a navy square with a white 'S' wave, then the serif wordmark."""
    p.fill(x, top + 1.5, x + 24, top + 25.5, "#1b3f8b")
    p.text(x + 12, top + 1.0, "S", "Times-Bold", 25, "#ffffff", "center")
    p.hline(x + 1, x + 23, top + 21.5, "#7fa3d9", 1.2)
    p.text(x + 28, top + 1.0, "Seaside", "Times-Bold", 17.5, "#1b3f8b")
    p.text(x + 28.5, top + 17.0, "Healthcare System", "Times-Bold", 8.6, "#1b3f8b")


def coupon(p, stmt_date, guarantor_id, amount_due):
    xs = [269.7, 336.8, 390.7, 449.2, 512.1, 569.1]
    rows = [61.5, 85.2, 97.2, 109.2, 132.7, 156.2, 168.5]
    for (y0, y1) in [(61.5, 85.2), (132.7, 156.2)]:
        p.fill(xs[0], y0, xs[-1], y1, PLAQUE)
    for y in rows:
        p.hline(xs[0], xs[-1], y, "#000000", 0.5)
    for x in (xs[0], xs[-1]):
        p.vline(x, rows[0], rows[-1], "#000000", 0.5)
    for x in xs[1:-1]:
        p.vline(x, 85.2, 97.2, "#000000", 0.5)
        p.vline(x, 156.2, 168.5, "#000000", 0.5)
    for x in xs[1:3]:
        p.vline(x, 97.2, 109.2, "#000000", 0.5)
    w = "#ffffff"
    p.text(270.5, 62.2, "For Credit ", F, S, w)
    p.text(270.5, 73.7, "Card", F, S, w)
    p.text(337.6, 62.2, "by mail, fill  in   below", F, S, w)
    p.text(270.5, 85.7, "Card Number:", F, S)
    p.text(449.9, 85.7, "CVV code", F, S)
    p.text(270.5, 97.7, "Name on card:", F, S)
    p.text(270.5, 109.7, "Signature:", F, S)
    p.text(449.9, 109.7, "Exp. date:", F, S)
    for x, a, b in [(270.5, "Statement ", "Date"), (337.6, "Due Date", ""), (391.5, "Guarantor #", ""),
                    (449.9, "Amount Due", ""), (512.9, "Amount ", "Paid")]:
        p.text(x, 133.2, a, F, S, w)
        if b:
            p.text(x, 144.7, b, F, S, w)
    vals = [d_mdy_short(stmt_date), d_mdy_short(stmt_date + timedelta(days=30)), guarantor_id, m_dollar(amount_due)]
    for (x0, x1), v in zip(zip(xs, xs[1:]), vals):
        p.text((x0 + x1) / 2, 156.7, v, F, S, align="center")


def bullets(p, top, items):
    """items: list of lines; a line is a list of runs; a new bullet starts on a tuple ('*', runs)."""
    y = top
    for item in items:
        first = True
        for line in item:
            if first:
                p.c.setFillColorRGB(0, 0, 0)
                p.c.circle(56.4, 792 - (y + 4.6), 1.5, stroke=0, fill=1)
                first = False
            p.runs(71.9, y, line, S)
            y += 11.5
    return y


def render(accounts, guarantor, path, stmt_date, variant="2026"):
    c = new_pdf(path, f"Statement {d_mdy_short(stmt_date)}")
    p = Page(c)
    logo(p, 54.7, 61.4)
    for i, s in enumerate(["Patient Financial Services", HS["pfs_street"], HS["pfs_city"]]):
        p.text(54.7, 88.7 + i * 11.5, s, F, S)
    amount_due = round(sum(a.due_from_patient for a in accounts), 2)
    coupon(p, stmt_date, guarantor.guarantor_id, amount_due)
    p.fill(53.9, 180.5, 403.8, 197.7, PLAQUE)
    p.fill(403.8, 180.5, 574.7, 197.7, PLAQUE)
    p.text(54.7, 181.2, "Guarantor", F, S, "#ffffff")
    p.text(404.6, 181.2, "Send Payment To:", F, S, "#ffffff")
    for i, s in enumerate([guarantor.name, guarantor.street, guarantor.apt, guarantor.city_line]):
        p.text(54.7, 198.2 + i * 11.5, s, F, S)
    for i, s in enumerate(HS["remit"]):
        p.text(404.6, 198.5 if i == 0 else 198.2 + i * 11.5, s, FB if i == 0 else F, S)
    p.text(53.9, 267.7, "_" * 85, F, S, "#404040")
    p.text(53.9, 291.0, "How can I pay my balance?", FB, S)
    blue = "#0000ff"
    site = "https://" + HS["site"]
    bullets(p, 302.2, [
        [[(f"Sign in to Your {HS['portal']} account", F)]],
        [[("Online  ", F), (site + " ", F, blue, True), ("and click on Pay My Bill", F)]],
        [[("Use the coupon above to pay by mail. If you have a balance due with both areas the payment will be split 50/50", F)],
         [("between your Hospital and Physician Balances", F)]],
        [[(f"Call us at {HS['phone_paren'].replace(')', ') ')} ", F)]],
        [[(f"Payments Plans can be set up in {HS['portal']}, online or by phone", F)]],
        [[("If you would like to apply for financial assistance please call us at the number above. To view our Financial", F)],
         [("Assistance Policy, visit our website: ", F), (HS["fap_url"], F, "#000000", True), (".", F)]],
    ])
    p.text(53.9, 406.0, "Hospital Services Detail:", FB, S)
    y = detail_table(p, 432.1, accounts, variant)
    y = summary_table(p, y + 11.5, accounts, variant)
    p.text(53.9, y + 35.1, "Questions?", FB, S)
    q = bullets(p, y + 46.3, [
        [[(f"Sign in to Your {HS['portal']} account to review your detail charges and current balance", F)]],
        [[(f"Email us at {HS['email'].replace('MyBill', 'Mybill')}, we will respond within one business day", F)]],
        [[(f"Call us {HS['phone_paren'].replace(')', ') ')} ", F)]],
        [[("If you do not agree with the patient balance due, please contact your HMO or Insurance Carrier directly to discuss", F)]],
    ])
    # specialty groups: heading on page 1, the rest wherever it falls (page 2 in the samples)
    y = q + 11.5
    lines = ["If you had services provided by the following specialty providers, they will bill you separately. Below is their billing",
             "contact information"]
    for i, s in enumerate(lines):
        p.text(53.9, y + i * 11.5, s, F, S)
    y += 23.5
    xs = [54.0, 246.5, 430.8, 569.9]

    def head(p, y):
        p.text(54.7, y + 0.5, "Company Name ", F, S)
        p.text(338.6, y + 0.5, "Provider", F, S, align="center")
        p.text(500.3, y + 0.5, "Telephone Number", F, S, align="center")
        for x0, x1 in zip(xs, xs[1:]):
            p.box(x0, y, x1, y + 14.0, "#000000", 0.5)
        return y + 14.0

    if y + 14 > 750:
        p.fiction_note(); c.showPage(); p = Page(c); y = 49.0
    y = head(p, y)
    if y + 12.6 * len(SPECIALTY_GROUPS_A) > 760:
        p.fiction_note(); c.showPage(); p = Page(c)
        y = head(p, 49.0)
    for name, role, phone in SPECIALTY_GROUPS_A:
        p.text(54.7, y + 0.3, name, F, S)
        p.text(338.6, y + 0.3, role, F, S, align="center")
        p.text(500.3, y + 0.3, phone, F, S, "#1f3864" if "Radiolog" in role else "#000000", "center")
        for x0, x1 in zip(xs, xs[1:]):
            p.box(x0, y, x1, y + 12.6, "#000000", 0.5)
        y += 12.6
    p.fiction_note()
    c.showPage()
    c.save()


def _head(p, top, cols, labels, variant, first_left=56.2):
    h = 23.5
    if variant == "2026":
        for x0, x1 in zip(cols, cols[1:]):
            p.fill(x0, top, x1, top + h, PALE)
        p.box(cols[0], top, cols[-1], top + h, NAVY_LINE, 0.25)
        color = HEAD_TEXT
    else:
        p.fill(cols[0], top, cols[-1], top + h, DARK_HEAD)
        p.box(cols[0], top, cols[-1], top + h, "#3b5376", 0.5)
        color = "#ffffff"
    for i, lab in enumerate(labels):
        parts = lab.split("\n")
        for k, part in enumerate(parts):
            if i == 0:
                p.text(first_left, top + 0.8, part, FB, S, color)
            else:
                p.text(cols[i + 1] - 1.6, top + 0.8 + k * 11.5, part, FB, S, color, "right")
    return top + h


def detail_table(p, top, accounts, variant):
    if variant == "2026":
        cols = [53.9, 262.5, 325.1, 387.6, 450.1, 512.7, 575.2]
        labels = ["Description", "Charges", "Insurance\nPmts/Adjs", "Patient\nPmts/Adjs", "Insurance\nBalance", "Patient\nBalance"]
    else:
        cols = [53.9, 325.1, 387.6, 450.1, 512.7, 575.2]
        labels = ["Description", "Charges", "Insurance\nPmts/Adjs", "Patient\nPmts/Adjs", "Patient\nBalance"]
    y = _head(p, top, cols, labels, variant)
    R = 11.7
    right = {k: cols[i + 2] - 1.2 for i, k in enumerate(labels[1:])}
    line = (lambda y: p.hline(cols[0], cols[-1], y, NAVY_LINE, 0.25)) if variant == "2026" else \
        (lambda y: p.hline(cols[0], cols[-1], y, "#000000", 0.5))
    tot_ch = tot_ins = tot_pat = tot_insbal = 0.0
    for a in accounts:
        kind = "Newborn" if a.kind == "Newborn" else "Inpatient"
        text = (f"Acct #: {a.account_no}  Patient: {a.patient.last.upper()},{a.patient.first.upper()}"
                + (f" {a.patient.middle.upper()}" if a.patient.middle else "")
                + f"  Service: {kind}  Hospital: {HS['hospital_caps'].replace(' HOSPITAL', '')} ")
        rest = f"HOSPITAL {d_mdy_short(a.admit)}  to {d_mdy_short(a.discharge)} "
        p.text(54.2, y + 0.2, text, F, S)
        p.text(54.2, y + 11.7, rest, F, S)
        y += 23.1
        line(y)
        tot_ch += a.total
        tot_ins += a.ins_pmts_adj
        tot_insbal += a.due_from_insurance
        tot_pat += a.due_from_patient
        if variant == "2026":
            p.text(55.2, y + 0.3, "Charges", F, S)
            p.text(right["Charges"], y + 0.3, m_dollar(a.total), F, S, align="right")
            y += R; line(y)
            p.text(55.2, y + 0.3, "Insurance Payments and Adjustments", F, S, GREEN)
            p.text(right["Insurance\nPmts/Adjs"], y + 0.3, m_dollar(a.ins_pmts_adj), F, S, GREEN, "right")
            y += R; line(y)
            p.text(55.2, y + 0.6, f"{HS['short']} Hospital Services Totals", FB, S)
            p.text(right["Charges"], y + 0.6, m_dollar(a.total), FB, S, align="right")
            p.text(right["Insurance\nPmts/Adjs"], y + 0.6, m_dollar(a.ins_pmts_adj), FB, S, align="right")
            p.text(right["Insurance\nBalance"], y + 0.6, m_dollar(a.due_from_insurance), FB, S, align="right")
            p.text(right["Patient\nBalance"], y + 0.6, m_dollar(a.due_from_patient), FB, S, align="right")
            y += R; line(y)
            p.text(55.2, y + 0.3, "Patient Balance", F, S, HEAD_TEXT)
            p.text(right["Patient\nBalance"], y + 0.3, m_dollar(a.due_from_patient), F, S, HEAD_TEXT, "right")
            y += R; line(y)
            p.text(54.9, y + 0.7, "Hospital Services Balance Due", FB, S)
            p.text(right["Patient\nBalance"], y + 0.7, m_dollar(tot_pat), FB, S, align="right")
            y += R
            p.vline(cols[0], top, y, NAVY_LINE, 0.25)
            p.vline(cols[-1], top, y, NAVY_LINE, 0.25)
            line(y)
        else:
            if a.balance_forward:
                p.text(55.2, y + 0.3, "Balance Forward", FB, S)
                p.text(right["Charges"], y + 0.3, m_dollar(a.total), FB, S, align="right")
                p.text(right["Insurance\nPmts/Adjs"], y + 0.3, m_dollar(a.ins_pmts_adj), FB, S, align="right")
                p.text(right["Patient\nPmts/Adjs"], y + 0.3, m_dollar(a.patient_pmts), FB, S, align="right")
                p.text(right["Patient\nBalance"], y + 0.3, m_dollar(a.due_from_patient), FB, S, align="right")
                y += R
            else:
                p.text(55.2, y + 0.3, "New Charges", F, S)
                p.text(right["Charges"], y + 0.3, m_dollar(a.total), F, S, align="right")
                p.text(55.2, y + 11.8, "Insurance Payments and Adjustments", F, S)
                p.text(right["Insurance\nPmts/Adjs"], y + 17.5, m_dollar(a.ins_pmts_adj), F, S, align="right")
                p.text(55.2, y + 23.3, "Patient Balance", FB, S)
                p.hline(55.2, 55.2 + width("Patient Balance", FB, S), y + 33.3, "#000000", 0.6)
                p.text(right["Patient\nBalance"], y + 23.3, m_dollar(a.due_from_patient), FB, S, align="right")
                for x in cols[1:-1]:
                    p.vline(x, y, y + 35.0, "#000000", 0.5)
                y += 35.0
            p.box(cols[0], y - (R if a.balance_forward else 35.0), cols[-1], y, "#000000", 0.5)
    if variant != "2026":
        p.text(55.2, y + 0.3, "Totals for Hospital Services", FB, S)
        p.text(right["Charges"], y + 0.3, m_dollar(tot_ch), FB, S, align="right")
        p.text(right["Insurance\nPmts/Adjs"], y + 0.3, m_dollar(tot_ins), FB, S, align="right")
        p.text(right["Patient\nBalance"], y + 0.3, m_dollar(tot_pat), FB, S, align="right")
        y += R
        p.text(55.2, y + 0.3, "Hospital Services Balance Due", FB, S)
        p.hline(55.2, 55.2 + width("Hospital Services Balance Due", FB, S), y + 10.6, "#000000", 0.6)
        p.text(right["Patient\nBalance"], y + 0.3, m_dollar(tot_pat), FB, S, align="right")
        p.box(cols[-2], y, cols[-1], y + R, "#000000", 0.5)
        y += R
    return y


def summary_table(p, top, accounts, variant):
    tot = lambda k: round(sum(getattr(a, k) for a in accounts), 2)
    ch, ins, pat_p = tot("total"), tot("ins_pmts_adj"), tot("patient_pmts")
    outstanding = round(ch + ins + pat_p, 2)
    insbal, patbal = tot("due_from_insurance"), tot("due_from_patient")
    if variant == "2026":
        cols = [53.9, 168.6, 236.4, 304.2, 372.0, 439.7, 507.5, 575.3]
        labels = ["Summary", "Charges", "Insurance\nPmts/Adjs", "Patient\nPmts/Adjs", "Outstanding\nBalance",
                  "Insurance\nBalance", "Patient\nBalance"]
        vals = [ch, ins, pat_p, outstanding, insbal, patbal]
    else:
        cols = [53.9, 236.4, 304.2, 372.0, 439.7, 507.5, 575.3]
        labels = ["Summary", "Charges", "Insurance\nPmts/Adjs", "Patient\nPmts/Adjs", "Outstanding\nBalance",
                  "Patient\nBalance"]
        vals = [ch, ins, pat_p, round(outstanding - insbal, 2) if insbal else outstanding, patbal]
    y = _head(p, top, cols, labels, variant, first_left=59.2)
    for row, font in [("Hospital Services", F), ("Totals", FB)]:
        p.text(59.4, y + 0.5, row, font, S)
        for i, v in enumerate(vals):
            last = i == len(vals) - 1
            color = HEAD_TEXT if (variant == "2026" and last and row == "Totals") else "#000000"
            f = F if (variant == "2026" and last and row == "Totals") else font
            p.text(cols[i + 2] - 1.2, y + 0.5, m_dollar(v), f, S, color, "right")
        y += 11.8
        if variant == "2026":
            p.hline(cols[0], cols[-1], y, NAVY_LINE, 0.25)
        else:
            for x0, x1 in zip(cols, cols[1:]):
                p.box(x0, y - 11.8, x1, y, "#000000", 0.4)
    if variant == "2026":
        p.vline(cols[0], top, y, NAVY_LINE, 0.25)
        p.vline(cols[-1], top, y, NAVY_LINE, 0.25)
    return y
