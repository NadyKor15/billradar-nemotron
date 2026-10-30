"""Document type D3: a pediatric practice statement (one sheet, teal, tear-off coupon on top).

Top third: logo, return address, pay-online note, teal account table, addressee / payee blocks
in Courier with postal barcodes, change-of-address checkbox, detach line.
Middle: STATEMENT banner and the ledger with the patient row highlighted in yellow.
Bottom: payment options, the PAY THIS AMOUNT arrow and the please-pay table.
Background realism (not a core finding): "Your Account may be subject to Collections" on a small balance.
"""
from __future__ import annotations

import random
from datetime import timedelta

from .common import Page, new_pdf
from .world import PEDIATRIC as PD

TEAL = "#2a6f97"
ORANGE = "#e07a2e"
GREEN = "#6aa84f"
F, FB = "Helvetica", "Helvetica-Bold"
C = "Courier"


def logo(p, x, top):
    c = p.c
    c.setFillColorRGB(0.89, 0.48, 0.18)
    c.circle(x + 15.5, 792 - (top + 20), 15.5, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.circle(x + 15.5, 792 - (top + 20), 12.3, stroke=0, fill=1)
    c.setFillColorRGB(0.16, 0.44, 0.59)
    for dx in (-6.5, 0, 6.5):
        c.circle(x + 15.5 + dx, 792 - (top + 18.5), 3.1, stroke=0, fill=1)
        c.rect(x + 12.6 + dx, 792 - (top + 29.5), 5.8, 8.5, stroke=0, fill=1)
    c.setFillColorRGB(0.42, 0.66, 0.31)
    c.ellipse(x + 9, 792 - (top + 10), x + 15, 792 - (top + 2), stroke=0, fill=1)
    c.ellipse(x + 16, 792 - (top + 9), x + 23, 792 - (top + 1), stroke=0, fill=1)
    p.text(x + 36, top + 6.5, "LITTLE HARBOR", FB, 9.0, TEAL)
    p.text(x + 36, top + 16.5, "PEDIATRICS", FB, 9.0, TEAL)
    p.text(x + 36.5, top + 27.0, PD["sub"], FB, 4.4, ORANGE)


def build(case, cl, seed):
    rng = random.Random(seed * 17 + 1)
    baby = case.family.baby
    mom = case.family.mother
    visit = case.baby_acct.discharge + timedelta(days=rng.randint(14, 40))
    stmt = visit + timedelta(days=rng.randint(100, 120))
    charge = float(rng.choice([142, 151, 164, 177]))
    paid = float(rng.choice([15, 20, 25]))
    due = float(rng.choice([18, 22, 27, 31]))
    ins = round((charge - paid - due) * rng.uniform(0.45, 0.55), 2)
    disc = round(charge - paid - due - ins, 2)
    a = case.family.address
    return {
        "guarantor": mom.full.upper(), "patient": f"{baby.first} {baby.middle} {baby.last}",
        "acct": f"{rng.randint(4_000_000, 6_999_999)}", "claim": f"{rng.randint(3_000_000, 3_999_999)}",
        "visit": visit, "stmt": stmt, "charge": charge, "paid": paid, "ins": ins, "disc": disc, "due": due,
        "doctor": "Greta I . V Vanterpool, MD", "code": "".join(rng.choice("abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ")
                                                           for _ in range(8)),
        "addr": [mom.full.upper(), a.street.upper(), f"{a.city.upper()} {a.state} {a.zip5}-{a.zip4}"],
        "route": f"{rng.randint(100000, 999999)} - {rng.randint(100, 999)}",
        "dm_no": f"{rng.randint(1000, 9999):04d} {rng.randint(100000, 999999)}",
        "foot": f"{rng.randint(100000, 999999)}-{rng.randint(100, 999)}-{rng.randint(10**7, 10**8 - 1)}",
    }


def imb(p, x, top, value, w):
    tracking = (value * 3)[:20].ljust(20, "0")
    p.barcode("USPS_4State", tracking, x, top, w=w, h=11, routing="33019441799")


def render(d, path):
    c = new_pdf(path, f"Statement {d['acct']}")
    p = Page(c)
    logo(p, 40.0, 6.0)
    for i, s in enumerate(PD["addr"] + ["RETURN SERVICE REQUESTED"]):
        p.text(152.0, 15.0 + i * 11.0, s, F, 8.8, "#1a1a1a")
    p.text(457.0, 17.0, f"Pay Online at {PD['pay_site']} using your personal", F, 8.8, "#1a1a1a", "center")
    p.text(457.0, 28.5, "statement code below.", F, 8.8, "#1a1a1a", "center")
    xs = [16.0, 229.0, 334.0, 421.0, 509.0, 594.0]
    p.fill(xs[0], 91.0, xs[-1], 104.0, TEAL)
    p.box(xs[0], 91.0, xs[-1], 119.0, "#1a1a1a", 0.6)
    vals = [d["guarantor"], d["acct"], d["stmt"].strftime("%m/%d/%Y"), "Upon Receipt", f"{d['due']:.2f}"]
    for (x0, x1), lab, v in zip(zip(xs, xs[1:]), ["Guarantor Name", "Patient Account Num", "Statement Date", "Due Date",
                                                    "Amount Due"], vals):
        p.vline(x1, 91.0, 119.0, "#1a1a1a", 0.6)
        p.text((x0 + x1) / 2, 93.5, lab, FB, 7.4, "#ffffff", "center")
        p.text((x0 + x1) / 2, 107.0, v, FB, 9.4, "#1a1a1a", "center")
    p.fill(43.0, 137.0, 270.0, 144.5, TEAL)
    p.text(156.5, 137.6, "ADDRESSEE:", FB, 6.6, "#ffffff", "center")
    p.fill(322.0, 137.0, 550.0, 144.5, TEAL)
    p.text(323.0, 137.6, "MAKE CHECKS PAYABLE TO:", FB, 6.6, "#ffffff")
    imb(p, 47.0, 157.0, d["acct"] + d["claim"], 196)
    p.text(254.0, 160.0, d["route"], F, 5.6, "#1a1a1a")
    p.barcode("ECC200DataMatrix", d["acct"] + d["dm_no"], 40.0, 179.0, w=20, h=20)
    p.text(36.0, 202.0, d["dm_no"], F, 4.8, "#1a1a1a")
    for i, s in enumerate(d["addr"]):
        p.text(73.0, 177.0 + i * 11.2, s, C, 9.4, "#1a1a1a")
    for i, s in enumerate([PD["payable"], PD["addr"][0] + " " + PD["addr"][1], PD["addr"][2]]):
        p.text(323.0, 166.0 + i * 11.2, s, C, 9.4, "#1a1a1a")
    imb(p, 322.0, 199.0, d["claim"] + d["acct"], 205)
    p.box(11.0, 251.0, 17.0, 257.0, "#1a1a1a", 0.6)
    p.text(23.0, 249.5, "Please check box here if above address is incorrect or insurance information has", F, 6.8, "#1a1a1a")
    p.text(23.0, 257.5, "changed, and indicate change(s) on reverse side.", F, 6.8, "#1a1a1a")
    cc = p.c
    cc.saveState()
    cc.setDash(2.0, 1.5)
    cc.setLineWidth(0.6)
    cc.line(11.0, 792 - 267.0, 600.0, 792 - 267.0)
    cc.restoreState()
    p.text(40.0, 268.5, "PLEASE DETACH AND RETURN ABOVE WITH YOUR PAYMENT", F, 6.8, "#1a1a1a")

    # statement body
    p.fill(11.0, 276.0, 599.0, 298.0, TEAL)
    p.text(13.0, 276.5, "STATEMENT", FB, 22.0, "#ffffff")
    p.text(14.0, 302.0, "Guarantor Name", FB, 7.0, "#1a1a1a")
    p.hline(14.0, 69.0, 309.6, "#1a1a1a", 0.5)
    p.text(14.0, 311.5, d["guarantor"], F, 11.5, TEAL)
    p.text(551.0, 302.0, "Statement Date", FB, 7.0, "#1a1a1a", "right")
    p.hline(498.0, 551.0, 309.6, "#1a1a1a", 0.5)
    p.text(551.0, 311.5, d["stmt"].strftime("%m/%d/%Y"), F, 11.5, TEAL, "right")
    cols = [15.0, 89.0, 375.0, 447.0, 520.0, 594.0]
    top = 329.0
    p.fill(cols[0], top, cols[-1], top + 26.0, TEAL)
    heads = [("Date", "c"), ("Description", "l"), ("Charges", "c"), ("Payments|/Adjustments", "c"), ("Amount Due", "c")]
    for (x0, x1), (h, al) in zip(zip(cols, cols[1:]), heads):
        lines = h.split("|")
        for k, s in enumerate(lines):
            tx = (x0 + x1) / 2 if al == "c" else x0 + 3
            ty = top + 3.0 + k * 11.0 if len(lines) > 1 else top + 3.0
            w = p.text(tx, ty, s, FB, 9.6, "#ffffff", "center" if al == "c" else "left")
            ux0 = tx - w / 2 if al == "c" else tx
            p.hline(ux0, ux0 + w, ty + 10.2, "#ffffff", 0.6)
    y = top + 26.0
    v, st = d["visit"], d["stmt"]
    rows = [
        (v, [f"Patient: {d['patient']}, Account Num:", d["acct"]], None, None, None, True),
        (v, [f"Claim:{d['claim']}, Provider: {d['doctor']}"], None, None, None, False),
        (v, ["OFFICE VISIT-EST PAT LEVEL 2"], d["charge"], None, None, False),
        (v, ["Patient Copay"], None, d["paid"], None, False),
        (v + timedelta(days=21), ["Insurance Payment"], None, d["ins"], None, False),
        (v + timedelta(days=21), ["Insurance Adjustment"], None, d["disc"], None, False),
        (v + timedelta(days=21), ["Your Balance Due On These Services ..."], None, None, d["due"], False),
        (st, [f"**** Make a secure online payment at {PD['pay_site']}",
              f"by using your personal statement code - {d['code']} ****"], None, None, None, False),
        (None, ["Your Account may be subject to Collections. You can resolve",
                "this by making an immediate payment."], None, None, None, False),
    ]
    for day, desc, ch, pay, due, yellow in rows:
        h = 13.2 * len(desc) + (1.0 if len(desc) > 1 else 0)
        if yellow:
            p.fill(cols[1] + 1, y + 1, cols[2] - 1, y + h - 1, "#f2c811")
        if day:
            p.text((cols[0] + cols[1]) / 2, y + 2.6, day.strftime("%m/%d/%Y"), F, 9.4, "#1a1a1a", "center")
        for k, s in enumerate(desc):
            p.text(cols[1] + 3.0, y + 2.6 + k * 11.4, s, F, 8.9, "#1a1a1a")
        for val, x in ((ch, cols[3] - 4), (pay, cols[4] - 4), (due, cols[5] - 4)):
            if val is not None:
                p.text(x, y + 4.0 if yellow else y + 2.6, f"{val:.2f}", F, 9.4, "#1a1a1a", "right")
        for x0, x1 in zip(cols, cols[1:]):
            p.box(x0, y, x1, y + h, "#1a1a1a", 0.6)
        y += h

    # payment options
    p.fill(18.0, 616.0, 278.0, 627.0, TEAL)
    p.box(18.0, 616.0, 278.0, 724.0, TEAL, 0.9)
    p.text(148.0, 617.0, "PAYMENT OPTIONS", FB, 8.6, "#ffffff", "center")
    for i, s in enumerate(["Make a secure online payment at", f"{PD['pay_site']} by using your personal",
                           f"statement code - {d['code']}"]):
        p.text(25.0, 631.0 + i * 11.0, s, F, 9.0, "#1a1a1a")
    p.barcode("QR", f"{PD['pay_site']}/{d['code']}", 26.0, 667.0, w=41, h=41)
    p.fill(26.0, 708.0, 67.0, 716.0, "#1a1a1a")
    p.text(46.5, 709.2, "SCAN TO PAY", F, 4.9, "#ffffff", "center")
    p.c.setStrokeColorRGB(0.1, 0.1, 0.1)
    p.c.setLineWidth(0.8)
    p.c.arc(74.0, 792 - 698.0, 86.0, 792 - 686.0, 100, 160)
    p.text(90.0, 682.0, "Billing Phone:", FB, 9.0, "#1a1a1a")
    p.text(90.0, 693.0, PD["phone"].replace("(", "").replace(") ", "-"), F, 9.0, "#1a1a1a")
    p.box(167.0, 685.0, 180.0, 694.0, "#1a1a1a", 0.7)
    p.c.line(167.0, 792 - 685.0, 173.5, 792 - 690.0)
    p.c.line(180.0, 792 - 685.0, 173.5, 792 - 690.0)
    p.text(183.0, 682.0, "By Mail:", FB, 9.0, "#1a1a1a")
    p.text(183.0, 693.0, "Use coupon above", F, 9.0, "#1a1a1a")
    arrow = p.c.beginPath()
    for i, (ax, ay) in enumerate([(290, 622), (390, 622), (390, 616), (411, 633), (390, 650), (390, 644), (290, 644)]):
        (arrow.moveTo if i == 0 else arrow.lineTo)(ax, 792 - ay)
    arrow.close()
    p.c.setFillColorRGB(0.16, 0.44, 0.59)
    p.c.setStrokeColorRGB(0.1, 0.1, 0.1)
    p.c.setLineWidth(0.6)
    p.c.drawPath(arrow, stroke=1, fill=1)
    p.text(341.0, 627.5, "PAY THIS AMOUNT", FB, 8.6, "#ffffff", "center")
    p.fill(418.0, 616.0, 590.0, 627.0, TEAL)
    p.box(418.0, 616.0, 590.0, 650.0, "#1a1a1a", 0.8)
    p.vline(505.0, 616.0, 650.0, "#1a1a1a", 0.8)
    for x, s in [(461.5, "Please Pay"), (547.5, "Please Pay By")]:
        w = p.text(x, 617.5, s, FB, 6.6, "#1f3864", "center")
        p.hline(x - w / 2, x + w / 2, 624.6, "#1f3864", 0.4)
    p.text(493.0, 634.0, f"{d['due']:.2f}", FB, 10.6, "#7b1f1f", "right")
    p.text(547.5, 634.0, "Upon Receipt", FB, 10.6, "#7b1f1f", "center")
    p.text(37.0, 748.0, "1 of 1", F, 4.6, "#1a1a1a")
    p.barcode("Code128", d["foot"], 30.0, 755.0, w=39, h=10)
    p.text(554.0, 759.0, d["foot"], F, 6.2, "#1a1a1a", "right")
    p.fiction_note()
    c.showPage()
    c.save()
