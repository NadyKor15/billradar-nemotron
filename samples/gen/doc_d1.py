"""Document type D1: a physician-group bill for anesthesia (3 pages, modern consumer design).

Page 1  logo + "do not mail payments here", envelope window with the STMT line, account box,
        "Your total is $…", the totals card with the fold arrow, tear-off "Ways to Pay"
Page 2  visit details (each billed service as a row), subtotal panel, mail-in slip with OCR line
Page 3  "Your bill summary"
"""
from __future__ import annotations

import os
import random
from datetime import timedelta

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .common import Page, m_dollar, new_pdf
from .world import ANESTHESIA as AN

INDIGO = "#3f5b9a"
GREY = "#333333"
LIGHT = "#6b6b6b"
PANEL = "#ececec"
F, FB = "Helvetica", "Helvetica-Bold"


def _serif():
    for path in ("/System/Library/Fonts/Supplemental/Georgia Bold.ttf", "/Library/Fonts/Georgia Bold.ttf"):
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("DisplaySerif", path))
                return "DisplaySerif"
            except Exception:
                pass
    return "Times-Bold"


SERIF = _serif()


def logo(p, x, top, scale=1.0):
    s = 6.0 * scale
    for i, col in enumerate(["#3a4250", "#3a4250", "#7d8796", "#7d8796"]):
        dx, dy = (i % 2) * (s + 2.2 * scale), (i // 2) * (s + 2.2 * scale)
        p.fill(x + dx, top + dy, x + dx + s, top + dy + s, col)
    p.text(x + 2 * s + 6 * scale, top - 1.5 * scale, AN["brand"], F, 13.5 * scale, "#4a5260")
    p.text(x + 2 * s + 6.5 * scale, top + 12.6 * scale, AN["brand_sub"], F, 4.6 * scale, "#4a5260")


def money_m(x):
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def perforation(p, y, shaded=False):
    if shaded:
        p.fill(36, y - 9, 590, y + 2, "#e4e4e4")
    c = p.c
    c.saveState()
    c.setDash(2.2, 1.6)
    c.setStrokeColorRGB(0.35, 0.35, 0.35)
    c.setLineWidth(0.6)
    c.line(18, 792 - y, 594, 792 - y)
    c.restoreState()
    # scissors
    c.saveState()
    c.setStrokeColorRGB(0.25, 0.25, 0.25)
    c.setLineWidth(0.7)
    c.circle(34, 792 - (y - 4.2), 1.6)
    c.circle(34, 792 - (y - 0.4), 1.6)
    c.line(35.5, 792 - (y - 3.8), 42, 792 - (y - 1.0))
    c.line(35.5, 792 - (y - 0.8), 42, 792 - (y - 3.6))
    c.restoreState()
    p.text(56.0, y - 8.0, "FOLD OR CUT AND DETACH AREA BELOW TO SEND WITH PAYMENT", "Helvetica-BoldOblique", 5.6, GREY,
           char_space=0.45)


def icon_monitor(p, x, top):
    p.box(x, top, x + 9, top + 6.5, GREY, 0.7)
    p.box(x + 6.5, top + 2.0, x + 11, top + 8.5, GREY, 0.7)


def icon_phone(p, x, top, r=4.6):
    c = p.c
    c.setStrokeColorRGB(0.2, 0.2, 0.2)
    c.setLineWidth(0.8)
    c.circle(x + r, 792 - (top + r), r)
    c.setLineWidth(1.2)
    c.arc(x + r - 2.4, 792 - (top + r) - 2.4, x + r + 2.4, 792 - (top + r) + 2.4, 120, 150)


def icon_mail(p, x, top):
    p.box(x, top, x + 11, top + 7.5, GREY, 0.8)
    c = p.c
    c.line(x, 792 - top, x + 5.5, 792 - (top + 4.2))
    c.line(x + 11, 792 - top, x + 5.5, 792 - (top + 4.2))


def icon_chat(p, x, top):
    c = p.c
    c.setStrokeColorRGB(0.2, 0.2, 0.2)
    c.setLineWidth(0.8)
    c.circle(x + 5, 792 - (top + 5), 5)


def build(case, cl, seed):
    """Collect what the anesthesia bill shows from the case and its claims."""
    rng = random.Random(seed * 7 + 11)
    claim = cl["anesthesia_claim"]
    june = cl["eob_june"]
    mom = case.family.mother
    billed = round(sum(l.charge for l in claim.lines), 2)
    adjusted = round(sum(l.discount for l in claim.lines), 2)
    paid = round(sum(l.benefit for l in claim.lines), 2)
    printed = june.print_date + timedelta(days=1)
    a = case.family.address
    return {
        "patient": mom.full, "patient_first": mom.first, "patient_last": mom.last,
        "account": f"{rng.randint(10_000_000, 99_999_999)}",
        "bill_id": " - ".join(f"{rng.randint(1000, 9999)}" for _ in range(3)),
        "stmt": f"STMT ▲ {' '.join(str(rng.randint(100000, 999999)))}  {rng.choice('ABCDEFGH')}{rng.randint(1, 9)}",
        "printed": printed, "due": printed + timedelta(days=30),
        "visit": claim.lines[0].dos_from, "clinician": "Oswin Tremayne MD",
        "lines": [("Anesthesia For Vaginal Delivery Only", "01960", l.charge) for l in claim.lines],
        "billed": billed, "adjusted": adjusted, "paid": paid, "due_amt": round(billed - adjusted - paid, 2),
        "addr": [mom.full, f"{a.street} {a.apt}", f"{a.city} {a.state} {a.zip5}-{a.zip4}"],
        "side_no": f"{rng.randint(10**9, 10**10 - 1)}",
    }


def ocr_line(d):
    cents = int(round(d["due_amt"] * 100))
    name = (d["patient_first"] + d["patient_last"]).upper().replace(" ", "")
    body = f"{d['account']}019{'0' * 7}{name}{'0' * 5}{cents:05d}{d['printed']:%m%d%Y}"
    check = sum(int(ch) for ch in body if ch.isdigit()) % 10
    return body + str(check)


def side_marks(p, d, n):
    p.barcode("ECC200DataMatrix", f"{d['account']}{n}", 15, 84 if n == 1 else 112, w=19, h=19)
    c = p.c
    c.saveState()
    c.setFont(F, 4.4)
    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.translate(23, 792 - (108 if n == 1 else 136))
    c.rotate(-90)
    c.drawString(0, 0, d["side_no"])
    c.restoreState()


def render(d, path):
    c = new_pdf(path, f"Anesthesia bill {d['bill_id']}")
    # ---------------- page 1 ----------------
    p = Page(c)
    logo(p, 55.0, 17.0)
    p.text(57.0, 35.0, AN["no_mail"][0], F, 7.4, "#222222")
    p.text(57.0, 43.0, AN["no_mail"][1], F, 7.4, "#222222")
    p.text(57.0, 51.0, AN["no_mail"][2], F, 7.4, "#222222")
    side_marks(p, d, 1)
    p.barcode("ECC200DataMatrix", d["bill_id"].replace(" - ", ""), 49, 121, w=16, h=16)
    p.text(74.0, 125.0, d["stmt"], F, 9.0, "#111111")
    for i, s in enumerate(d["addr"]):
        p.text(74.0, 135.5 + i * 10.0, s, F, 9.0, "#111111")
    p.text(582.0, 14.0, "Page 1 of 3", F, 9.0, GREY, "right")
    p.text(357.0, 37.0, "This bill covers anesthesiology services rendered at", F, 7.6, GREY)
    p.text(357.0, 49.5, "SEASIDE REGIONAL HOSP", SERIF, 10.6, INDIGO)
    for i, (k, v) in enumerate([("Patient:", d["patient"]), ("Account number:", d["account"]),
                                ("Bill ID:", d["bill_id"]), ("Printed on:", f"{d['printed']:%b %d, %Y}")]):
        p.text(357.0, 77.5 + i * 12.0, k, F, 7.6, GREY)
        p.text(462.0, 75.5 + i * 12.0, v, FB, 7.6, "#222222")
    p.fill(357.0, 144.0, 359.2, 204.0, INDIGO)
    p.text(367.5, 151.0, "Difficulty paying your bill?", FB, 8.6, "#222222")
    p.runs(367.5, 164.5, [("Visit ", F, GREY), (AN["site"], FB, "#222222"), (" to set up an affordable", F, GREY)], 7.4)
    p.text(367.5, 176.5, "payment plan and see more ways to resolve your", F, 7.4, GREY)
    p.text(367.5, 188.5, "balance.", F, 7.4, GREY)

    p.text(47.0, 291.0, f"Your total is {money_m(d['due_amt'])}", SERIF, 20.5, "#1a1a1a")
    p.text(52.0, 328.0, "You have one bill that is ready to pay. The total amount is", F, 8.8, GREY)
    p.runs(52.0, 342.5, [("due by ", F, GREY), (f"{d['due']:%b %d, %Y}.", FB, "#1a1a1a")], 8.8)
    p.fill(52.0, 375.0, 305.0, 423.0, "#fbf3f1")
    p.fill(52.0, 375.0, 54.0, 423.0, "#c0303a")
    for i, s in enumerate(["Additional information about your bill or insurance coverage can be found",
                           None, "information."]):
        if s:
            p.text(61.0, 385.0 + i * 10.4, s, F, 6.7, GREY)
    p.runs(61.0, 395.4, [("online or on your statement details. Visit ", F, GREY), (AN["site"], FB, "#222222"),
                         (" for more", F, GREY)], 6.7)
    # totals card
    p.text(357.0, 283.0, "See back for details", FB, 7.0, "#222222")
    p.text(436.0, 281.5, "→", F, 9.0, INDIGO)
    p.round_box(357.0, 296.0, 554.0, 423.0, 6, "#2b2b2b", 0.9)
    tri = p.c.beginPath()
    tri.moveTo(554.0, 792 - 318.0)
    tri.lineTo(568.0, 792 - 358.0)
    tri.lineTo(554.0, 792 - 400.0)
    tri.close()
    p.c.setFillColorRGB(0.29, 0.41, 0.68)
    p.c.drawPath(tri, stroke=0, fill=1)
    rows = [("Total billed", money_m(d["billed"]), 0), ("Insurance adjusted", money_m(-d["adjusted"]), 0),
            ("Insurance paid", money_m(-d["paid"]), 0), ("COMMERCIAL", money_m(-d["paid"]), 15),
            ("SELF PAY AFTER", "-$0.00", 15), ("INSURANCE", "", 15)]
    for i, (k, v, ind) in enumerate(rows):
        p.text(371.0 + ind, 315.0 + i * 11.8, k, F, 7.5, "#222222")
        if v:
            p.text(538.0 if ind == 0 else 505.0, 315.0 + i * 11.8, v, F, 7.5, "#222222", "right")
    p.hline(371.0, 541.0, 389.5, "#9a9a9a", 0.6)
    p.text(371.0, 398.5, "Total due", FB, 7.6, "#111111")
    p.text(538.0, 397.5, money_m(d["due_amt"]), FB, 9.0, "#111111", "right")

    perforation(p, 518.0)
    p.text(47.0, 543.0, "Ways to Pay", SERIF, 13.0, "#1a1a1a")
    icon_monitor(p, 49.0, 568.0)
    p.text(66.0, 567.0, "Online", F, 10.0, GREY)
    icon_phone(p, 175.0, 567.0)
    p.text(192.0, 567.0, "Phone", F, 10.0, GREY)
    icon_mail(p, 299.0, 569.0)
    p.text(316.0, 567.0, "Mail", F, 10.0, GREY)
    p.text(49.0, 591.0, "Pay via desktop or mobile:", F, 7.0, GREY)
    p.text(49.0, 602.5, AN["site"], FB, 7.0, "#222222")
    for i, s in enumerate(["To pay by our automated", "phone system, call toll-free", "24/7:"]):
        p.text(175.0, 591.0 + i * 11.5, s, F, 7.0, GREY)
    p.text(175.0, 625.5, AN["phone"].replace("1-", "(").replace("-555", ") 555", 1), FB, 7.0, "#222222")
    for i, s in enumerate(["Mail check or money order", "with this part of the bill to the", "address on the reverse side.",
                           "Do not send cash."]):
        p.text(299.0, 591.0 + i * 11.5, s, F, 7.0, GREY)
    p.runs(49.0, 640.0, [("Bill ID: ", F, GREY), (d["bill_id"], FB, "#222222")], 7.0)
    p.fill(50.0, 655.0, 405.0, 722.0, "#efefef", 3)
    p.barcode("QR", f"https://{AN['site']}/b/{d['bill_id'].replace(' - ', '')}", 56.0, 659.0, w=50, h=50)
    p.text(119.0, 668.0, "Scan to pay online.", FB, 7.4, "#222222")
    p.text(119.0, 693.0, "Just point your phone's camera at the code to scan. Some phones may", F, 6.8, GREY)
    p.text(119.0, 702.5, "require a QR code app.", F, 6.8, GREY)
    p.vline(420.0, 537.0, 729.0, "#9a9a9a", 0.6)
    p.text(443.0, 543.0, "Need Help?", FB, 10.0, "#1a1a1a")
    icon_phone(p, 443.0, 577.0, 6.0)
    for i, s in enumerate(["Call our team toll-free", "(Mon to Fri 8AM to 6PM EDT)"]):
        p.text(462.0, 575.0 + i * 11.0, s, F, 7.4, GREY)
    p.text(462.0, 597.5, AN["help_phone"].replace("1-", "(").replace("-555", ") 555", 1), FB, 7.4, "#222222")
    icon_chat(p, 443.0, 619.0)
    p.text(462.0, 617.0, "Live Chat", F, 7.4, GREY)
    p.text(462.0, 629.0, AN["site"], FB, 7.4, "#222222")
    p.fiction_note()
    c.showPage()

    # ---------------- page 2 ----------------
    p = Page(c)
    p.c.setStrokeColorRGB(0.25, 0.37, 0.62)
    p.c.setLineWidth(0.9)
    p.c.circle(37.0, 792 - 20.5, 3.4)
    p.text(47.0, 17.0, "Anesthesiology services", F, 7.5, GREY)
    p.text(580.0, 17.0, "Page 2 of 3", F, 9.0, GREY, "right")
    p.text(580.0, 38.0, f"Due date: {d['due']:%b %d, %Y}", FB, 7.6, "#222222", "right")
    p.text(44.0, 37.0, f"{d['patient_first']}'s visit on {d['visit']:%B} {d['visit'].day}, {d['visit']:%Y}", SERIF, 13.5,
           "#1a1a1a")
    p.round_box(275.0, 40.0, 350.0, 51.0, 3, "#bdbdbd", 0.5)
    p.text(291.0, 42.0, "Insurance applied", F, 6.8, GREY)
    shield = p.c.beginPath()
    for i, (sx, sy) in enumerate([(278.5, 42.5), (283.0, 41.5), (287.5, 42.5), (287.0, 47.0), (283.0, 50.0),
                                  (279.0, 47.0)]):
        (shield.moveTo if i == 0 else shield.lineTo)(sx, 792 - sy)
    shield.close()
    p.c.setStrokeColorRGB(0.3, 0.3, 0.3)
    p.c.setLineWidth(0.6)
    p.c.drawPath(shield, stroke=1, fill=0)
    p.text(44.0, 58.5, f"Location: Seaside Regional Hosp  |  Clinician: {d['clinician']}", F, 7.3, GREY)
    p.text(57.0, 87.5, "Bill details", FB, 8.4, "#1a1a1a")
    p.hline(44.0, 580.0, 103.0, "#333333", 1.0)
    p.text(57.0, 108.0, "Service", FB, 7.4, "#1a1a1a")
    p.text(577.0, 108.0, "Total billed", FB, 7.4, "#1a1a1a", "right")
    p.hline(44.0, 580.0, 121.0, "#333333", 1.0)
    y = 121.0
    for name, code, amt in d["lines"]:
        p.text(60.0, y + 12.0, "Anesthesia For Vaginal Delivery Only", F, 7.5, GREY)
        p.text(60.0, y + 35.5, f"CPT/HCPCS code: {code}", F, 6.3, LIGHT)
        p.text(577.0, y + 12.0, f"${amt:,.2f}", "Courier", 7.6, GREY, "right")
        y += 50.0
        p.hline(50.0, 574.0, y, "#555555", 0.6)
    y += 3.0
    p.fill(50.0, y, 352.0, y + 50.0, PANEL)
    p.c.setStrokeColorRGB(0.2, 0.2, 0.2)
    p.c.setLineWidth(0.6)
    p.c.circle(64.0, 792 - (y + 11.0), 4.0)
    p.text(64.0, y + 7.5, "i", FB, 6.0, GREY, "center")
    p.text(73.0, y + 7.0, "Additional information", FB, 7.4, "#1a1a1a")
    for i, s in enumerate(["Your insurance plan determines how much you owe according to your benefit",
                           "plan design. The balance may be due to deductible, co-pay, co insurance. This",
                           "bill is the portion you owe for care provided."]):
        p.text(73.0, y + 21.0 + i * 9.2, s, F, 6.4, GREY)
    p.fill(357.0, y, 574.0, y + 76.0, PANEL)
    for i, (k, v) in enumerate([("Subtotal billed", f"${d['billed']:,.2f}"), ("Insurance adjusted", f"-${d['adjusted']:,.2f}"),
                                ("Insurance paid", f"-${d['paid']:,.2f}")]):
        p.text(370.0, y + 9.0 + i * 16.5, k, F, 7.3, "#222222")
        p.text(566.0, y + 9.0 + i * 16.5, v, "Courier", 7.6, "#222222", "right")
    p.hline(370.0, 568.0, y + 55.5, "#555555", 0.6)
    p.text(370.0, y + 62.0, "Amount you owe (subtotal)", FB, 7.3, "#111111")
    p.text(566.0, y + 62.0, f"${d['due_amt']:,.2f}", "Courier-Bold", 7.6, "#111111", "right")
    p.text(535.0, 471.0, "Continued on next page", SERIF, 12.5, "#1a1a1a", "right")
    perforation(p, 518.0, shaded=True)
    p.text(82.0, 545.0, "Mail this slip with check", SERIF, 12.5, "#1a1a1a")
    for i, (k, v) in enumerate([("Account Holder:", d["patient"]), ("Account Number:", d["account"]),
                                ("Bill Amount:", f"${d['due_amt']:,.2f}")]):
        p.runs(82.0, 577.0 + i * 12.2, [(k + " ", F, GREY), (v, FB, "#1a1a1a")], 7.2)
    p.text(350.0, 646.0, "MAKE CHECK PAYABLE & MAIL TO:", F, 7.0, GREY)
    for i, s in enumerate([AN["legal_caps"]] + AN["remit"]):
        p.text(350.0, 672.0 + i * 13.8, s, FB, 8.6, "#2a2a2a")
    logo(p, 82.0, 700.0, 1.0)
    p.text(93.0, 738.0, ocr_line(d), "Courier", 10.6, "#222222", char_space=0.35)
    p.fiction_note()
    c.showPage()

    # ---------------- page 3 ----------------
    p = Page(c)
    side_marks(p, d, 3)
    p.text(580.0, 20.0, "Page 3 of 3", F, 9.0, GREY, "right")
    p.text(64.0, 50.0, "Your bill summary", SERIF, 13.0, "#1a1a1a")
    logo(p, 464.0, 52.0, 0.9)
    p.hline(64.0, 556.0, 76.0, "#4a5260", 1.4)
    p.text(77.0, 90.5, "Total billed", FB, 8.6, "#222222")
    p.text(549.0, 92.5, f"${d['billed']:,.2f}", F, 8.6, "#222222", "right")
    p.text(77.0, 112.0, "Insurance adjusted", FB, 8.6, "#222222")
    p.text(77.0, 126.5, "The discounted amount applied by your insurance.", F, 8.6, GREY)
    p.text(549.0, 114.0, f"-${d['adjusted']:,.2f}", F, 8.6, "#222222", "right")
    p.text(77.0, 148.0, "Insurance paid", FB, 8.6, "#222222")
    p.text(77.0, 162.5, "The benefit amount your insurance has paid based on your plan.", F, 8.6, GREY)
    p.text(549.0, 150.0, f"-${d['paid']:,.2f}", F, 8.6, "#222222", "right")
    p.text(76.0, 184.5, "Total due", FB, 10.4, INDIGO)
    p.text(549.0, 186.0, f"${d['due_amt']:,.2f}", FB, 10.4, INDIGO, "right")
    p.fiction_note()
    c.showPage()
    c.save()
