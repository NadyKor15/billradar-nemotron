"""Document type D2: an OB/GYN practice statement (grey practice-management printout, 2 sheets).

Sheet 1: provider box, patient/bill-date box, address box, note box, then the ledger table
(claim header line, service line, payments and adjustments, "Your Balance Due..." line per claim)
and the remittance coupon at the bottom. Sheet 2 continues the ledger and repeats the coupon.

Error flags read from case.errors:
  E3  the patient paid more at the office than the EOB co-pay; the credit stays in brackets
      on the statement and is simply added into PAY THIS AMOUNT
  E4  the last ledger line reads "Your Balance Due On These Services... $0.00" (balance moved to a
      payment plan) while the coupon on the same sheet still asks for the full amount
"""
from __future__ import annotations

import random
from datetime import timedelta

from .common import Page, m_paren, new_pdf
from .world import OBGYN, HOSPITAL_SYSTEM as HS

GREY = (0.66, 0.66, 0.66)
F, FB = "Helvetica", "Helvetica-Bold"
X0, X1 = 36.0, 576.0
DATE_X, DESC_X, CHG_X, PMT_R, AMT_R = 40.0, 126.0, 357.0, 461.0, 548.0
PITCH = 17.0


def d_mon(d):
    return d.strftime("%b %d, %Y")


def build(case, cl, seed):
    rng = random.Random(seed * 13 + 3)
    err = case.errors
    mom = case.family.mother
    ob = cl["ob_claim"]
    pre = cl["eob_prenatal"]
    us = pre.claims[1].lines[0]
    copay = cl["us_copay"]
    g, adm = ob.lines[0], ob.lines[1]
    bill_date = cl["eob_ob"].print_date + timedelta(days=rng.randint(6, 10))
    dr = "Marit Halversen, MD"
    rows = []          # (date or None, text, charge, pmt, amount, bold_center)
    totals = []
    # a routine prenatal visit, paid; the member's copay is the balance
    pv_day = us.dos_from - timedelta(days=rng.randint(20, 35))
    pv_charge = float(rng.choice([215, 228, 240]))
    pv_copay = float(rng.choice([20, 25]))
    pv_paid = round(pv_charge * rng.uniform(0.3, 0.36) - pv_copay, 2)
    pv_adj = round(pv_charge - pv_paid - pv_copay, 2)
    pv_eob = pv_day + timedelta(days=rng.randint(15, 25))
    rows += [(pv_day, f"Claim:{rng.randint(4_100_000, 4_499_999)}, Provider: {dr}", None, None, None, False),
             (pv_day, "99213 Office Visit, Est Pt., Level 3", pv_charge, None, None, False),
             (pv_eob, "Primary Insurance Payment", None, pv_paid, None, False),
             (pv_eob, "Primary Insurance Adjustment", None, pv_adj, None, False)]
    rows += _balance_block(bill_date, pv_copay)
    totals.append(pv_copay)
    # ultrasound: copay collected at the office
    paid_office = copay + (float(rng.choice([25, 40, 55])) if err.get("E3") else 0.0)
    us_adj = round(us.charge - us.allowed, 2)
    us_bal = round(us.charge - paid_office - us.benefit - us_adj, 2)
    eob_day = pre.print_date - timedelta(days=2)
    rows += [(us.dos_from, f"Claim:{rng.randint(4_500_000, 4_799_999)}, Provider: {dr}", None, None, None, False),
             (us.dos_from, "76815 OB US, LIMITED, 1+ FETUSES", us.charge, None, None, False),
             (us.dos_from, "Patient Copay Collected", None, paid_office, None, False),
             (eob_day, "Primary Insurance Payment", None, us.benefit, None, False),
             (eob_day, "Primary Insurance Adjustment", None, us_adj, None, False)]
    rows += _balance_block(bill_date, us_bal, two_line=False)
    totals.append(us_bal)
    sheet1 = rows
    # sheet 2: the delivery and postpartum package
    adj = round(g.charge - g.allowed, 2)
    pkg_bal = round(g.deductible + g.coins, 2)
    rows2 = []
    # admission visit, paid; member share is co-insurance
    adm_adj = round(adm.charge - adm.allowed, 2)
    adm_bal = round(adm.coins + adm.deductible, 2)
    rows2 += [(adm.dos_from, f"Claim:{rng.randint(4_800_000, 4_999_999)}, Provider: {dr}", None, None, None, False),
             (adm.dos_from, "99222 INIT HOSP CARE-MOD CPLX", adm.charge, None, None, False),
             (bill_date - timedelta(days=4), "Primary Insurance Payment", None, adm.benefit, None, False),
             (bill_date - timedelta(days=4), "Primary Insurance Adjustment", None, adm_adj, None, False)]
    rows2 += _balance_block(bill_date, adm_bal, two_line=False)
    totals.append(adm_bal)
    rows2 += [(g.dos_from, f"Claim:{rng.randint(5_000_000, 5_199_999)}, Provider: {dr}", None, None, None, False),
              (g.dos_from, f"59410 VAG DELIV & PP CARE ({g.dos_from:%m/%d/%Y})", g.charge, None, None, False),
             (bill_date - timedelta(days=4), "Primary Insurance Payment", None, g.benefit, None, False),
             (bill_date - timedelta(days=4), "Primary Insurance Adjustment", None, adj, None, False)]
    rows2 += _balance_block(bill_date, pkg_bal)
    totals.append(pkg_bal)
    if err.get("E4"):
        rows2 += [(bill_date, "Balance Transferred To Payment Plan", None, -pkg_bal, None, False),
                  (None, "Your Balance Due On These Services...", None, None, 0.0, True)]
    pay = round(sum(totals), 2)
    return {"patient": f"{mom.last} , {mom.first}", "bill_date": bill_date,
            "account": f"{rng.randint(20000, 79999)}", "sheets": [sheet1, rows2], "pay": pay,
            "addr": [f"{mom.last} , {mom.first}", case.family.address.street, case.family.address.apt,
                     f"{case.family.address.city} {case.family.address.state} {case.family.address.zip5}"],
            "credit": us_bal if us_bal < 0 else 0.0, "paid_office": paid_office, "eob_copay": copay}


def _balance_block(day, amount, two_line=True):
    """The practice system closes each claim with a balance line; the long form adds the 'now due' note."""
    rows = [(day, "Your Balance Due On These Services", None, None, None, False)]
    if two_line:
        rows += [(day, "\n", None, None, None, False),
                 (None, "Your Payment is now due. Thank you for your|prompt response.", None, None, None, False)]
    return rows + [(None, "Your Balance Due On These Services...", None, None, amount, True)]


def header_boxes(p, d):
    p.text(306.0, 4.0, "STATEMENT", F, 7.0, "#555555", "center")
    p.box(46.0, 19.0, 289.0, 88.0, "#222222", 0.7)
    p.fill(46.0, 19.0, 289.0, 45.0, GREY)
    p.hline(46.0, 289.0, 45.0, "#222222", 0.7)
    p.text(167.5, 21.5, "THIS IS A STATEMENT OF SERVICES RENDERED BY PROVIDER(S)", F, 7.6, "#1a1a1a", "center")
    p.text(167.5, 32.5, "WHO ARE MEMBERS OF:", F, 7.6, "#1a1a1a", "center")
    for i, s in enumerate([OBGYN["member_of"], HS["street_caps"], "CORAL HAVEN, FL 33019-4417"]):
        p.text(49.0, 50.5 + i * 13.0, s, F, 9.2, "#1a1a1a")
    p.box(316.0, 19.0, 573.0, 89.0, "#222222", 0.7)
    p.fill(316.0, 19.0, 573.0, 33.0, GREY)
    p.hline(316.0, 573.0, 33.0, "#222222", 0.7)
    p.text(444.5, 21.5, "PATIENT NAME", F, 8.0, "#1a1a1a", "center")
    p.text(330.0, 40.0, d["patient"], F, 10.0, "#1a1a1a")
    p.fill(316.0, 55.0, 573.0, 68.0, GREY)
    p.hline(316.0, 573.0, 55.0, "#222222", 0.7)
    p.hline(316.0, 573.0, 68.0, "#222222", 0.7)
    for x0, x1, lab, val in [(316.0, 397.0, "BILL DATE", d_mon(d["bill_date"])), (397.0, 484.0, "ACCOUNT NO.", d["account"]),
                             (484.0, 573.0, "AMOUNT PAID", "")]:
        p.vline(x1, 55.0, 89.0, "#222222", 0.7)
        p.text((x0 + x1) / 2, 57.5, lab, F, 7.8, "#1a1a1a", "center")
        p.text((x0 + x1) / 2, 73.5, val, F, 9.4, "#1a1a1a", "center")
    p.box(64.0, 141.0, 288.0, 213.0, "#222222", 0.8)
    for i, s in enumerate(d["addr"]):
        p.text(70.0, 146.0 + i * 17.0, s, F, 9.6, "#1a1a1a")
    p.box(319.0, 143.0, 573.0, 213.0, "#222222", 0.8)
    for i, s in enumerate(["This is a statement for professional services rendered by your",
                           "physician. You may receive a separate bill from the hospital for", "its services."]):
        p.text(323.0, 163.0 + i * 11.4, s, F, 7.6, "#1a1a1a")


def table_head(p, top):
    p.fill(X0, top, X1, top + 28.0, GREY)
    p.text(45.0, top + 9.0, "DATE OF SERVICE", F, 7.6, "#1a1a1a")
    p.text(228.0, top + 9.0, "DESCRIPTION OF SERVICE", F, 7.6, "#1a1a1a", "center")
    p.text(366.0, top + 10.0, "CHARGES", F, 7.6, "#1a1a1a", "center")
    p.text(435.0, top + 3.0, "PMT/ADJ/", F, 7.6, "#1a1a1a", "center")
    p.text(435.0, top + 13.5, "WITHHELD", F, 7.6, "#1a1a1a", "center")
    p.text(522.0, top + 9.0, "AMOUNT", F, 7.6, "#1a1a1a", "center")
    return top + 34.0


def ledger(p, top, rows):
    y = top
    for day, text, charge, pmt, amount, centered in rows:
        if text == "\n":
            p.text(DATE_X + 2, y, d_mon(day), F, 9.2, "#1a1a1a")
            y += PITCH
            continue
        if "|" in text:
            a, b = text.split("|")
            p.text(DESC_X + 3, y - 3.0, a, F, 8.6, "#1a1a1a")
            p.text(DESC_X + 3, y + 7.6, b, F, 8.6, "#1a1a1a")
            y += PITCH + 6.0
            continue
        if day is not None:
            p.text(DATE_X, y, d_mon(day), F, 9.2, "#1a1a1a")
        if centered:
            p.text(DESC_X + 46, y - 1.0, text, FB, 8.6, "#1a1a1a")
        else:
            p.text(DESC_X, y, text, F, 8.6, "#1a1a1a")
        if charge is not None:
            p.text(CHG_X, y + 1.5, f"${charge:,.2f}", F, 9.0, "#1a1a1a")
        if pmt is not None:
            p.text(PMT_R, y + 3.0, m_paren(pmt), F, 9.0, "#1a1a1a", "right")
        if amount is not None:
            p.text(AMT_R, y + 1.0, m_paren(amount), F, 9.0, "#1a1a1a", "right")
        y += PITCH
    return y


def coupon(p, d):
    def cell(x0, x1, label, value):
        p.box(x0, 609.0, x1, 643.0, "#222222", 0.8)
        p.fill(x0, 609.0, x1, 622.0, GREY)
        p.hline(x0, x1, 622.0, "#222222", 0.8)
        p.text((x0 + x1) / 2, 611.0, label, F, 8.0, "#1a1a1a", "center")
        p.text((x0 + x1) / 2, 627.5, value, F, 8.8, "#1a1a1a", "center")
    cell(29.0, 117.0, "DATE", d_mon(d["bill_date"]))
    cell(131.0, 323.0, "PATIENT NAME", d["patient"])
    cell(335.0, 419.0, "ACCOUNT NO.", d["account"])
    p.text(453.0, 612.0, "PAY THIS", F, 8.6, "#1a1a1a", "center")
    p.text(453.0, 627.0, "AMOUNT", F, 8.6, "#1a1a1a", "center")
    p.box(482.0, 624.0, 580.0, 642.0, "#222222", 0.9)
    p.text(531.0, 628.0, f"${d['pay']:,.2f}" if d["pay"] >= 0 else m_paren(d["pay"]), F, 9.4, "#1a1a1a", "center")
    p.text(91.0, 659.0, "MAKE CHECK PAYABLE TO :", FB, 7.6, "#1a1a1a")
    p.text(204.0, 658.0, OBGYN["payable"], F, 9.6, "#1a1a1a")
    p.fill(30.0, 683.0, 584.0, 709.0, GREY)
    p.box(30.0, 683.0, 584.0, 709.0, "#222222", 0.8)
    p.text(307.0, 688.5, "IMPORTANT MESSAGE REGARDING YOUR ACCOUNT", F, 9.2, "#1a1a1a", "center")
    p.vline(584.0, 709.0, 770.0, "#222222", 0.8)
    p.text(34.0, 725.0, "We are pleased to offer you the option of credit card payment. Please indicate your method below.", F,
           9.0, "#1a1a1a")
    p.text(34.0, 742.0, "Payment Method:", F, 8.4, "#1a1a1a")
    for x, s in [(170.0, "VISA"), (245.0, "MASTER CARD"), (345.0, "DISCOVER"), (440.0, "AMEX"), (520.0, "CHECK")]:
        p.box(x - 12.0, 742.0, x - 4.0, 750.0, "#222222", 0.6)
        p.text(x, 742.0, s, F, 7.6, "#1a1a1a")
    p.text(34.0, 758.0, "Card No.: ____________________________   Exp.: ________   Signature: ______________________________", F, 8.0,
           "#1a1a1a")


def render(d, path):
    c = new_pdf(path, f"Statement {d['account']}")
    for i, rows in enumerate(d["sheets"]):
        p = Page(c)
        if i == 0:
            header_boxes(p, d)
            y = table_head(p, 228.0)
        else:
            p.text(306.0, 4.0, "STATEMENT", F, 7.0, "#555555", "center")
            y = table_head(p, 26.0)
        ledger(p, y, rows)
        coupon(p, d)
        p.fiction_note()
        c.showPage()
    c.save()
