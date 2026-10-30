"""Document type E: a physician group's "this is not a bill" letter (Courier body, one sheet).

The newborn doctors' group tells the family the plan did not accept its claim because it cannot
match the patient to the member ID on file, and that the balance becomes theirs in 30 days. In a pack with the
B1 error this pairs with the EOB, where the newborn's care was filed on the mother's member ID.
"""
from __future__ import annotations

import random
from datetime import timedelta

from .common import Page, new_pdf
from .world import NEONATAL as NN

C = "Courier"
T, TB = "Times-Roman", "Times-Bold"
LEAD = 10.0


def logo(p, x, top):
    c = p.c
    c.setLineWidth(1.8)
    for (dx, dy), col in zip([(0, 0), (9, 0), (4.5, 7)], [(0.85, 0.25, 0.45), (0.45, 0.25, 0.6), (0.15, 0.6, 0.6)]):
        c.setStrokeColorRGB(*col)
        c.circle(x + 7 + dx, 792 - (top + 9 + dy), 6.2)
    p.text(x + 31, top + 1.0, NN["brand"], "Helvetica-Bold", 17.5, "#2d4f8c")
    p.text(x + 31.5, top + 20.0, NN["brand_sub"], "Helvetica", 9.6, "#2a8c8c", char_space=0.6)


def build(case, cl, seed):
    rng = random.Random(seed * 19 + 4)
    neo = cl["neonatal_claims"]
    baby, mom = case.family.baby, case.family.mother
    letter_day = cl["eob_june"].print_date + timedelta(days=rng.randint(8, 14))
    bal = float(rng.choice([180, 210, 250, 275]))
    a = case.family.address
    return {"date": letter_day, "account": f"CVX {rng.randint(10**8, 10**9 - 1)}", "balance": bal,
            "patient": f"{baby.last.upper()}, {baby.first.upper()}", "mail_no": f"{rng.randint(10000, 99999)}",
            "addr": [mom.full.upper(), f"{a.street} {a.apt}", f"{a.city} {a.state}  {a.zip5}-{a.zip4}"],
            "foot": (f"{NN['footer_code']}-{rng.randint(10**6, 10**7 - 1)}-0000000-{rng.randint(10**7, 10**8 - 1)}"
                     f"-001-{rng.randint(1, 999999):06d}-#{rng.randint(1, 999999):06d}-{rng.randint(1000, 9999)}"),
            "denied_claims": [x.claim_no for x in neo]}


def render(d, path):
    c = new_pdf(path, "Letter")
    p = Page(c)
    p.text(45.0, 30.0, NN["name"], TB, 11.5, "#111111")
    for i, s in enumerate(NN["addr"]):
        p.text(45.0, 43.5 + i * 11.0, s, T, 9.6, "#111111")
    logo(p, 400.0, 27.0)
    p.barcode("ECC200DataMatrix", d["account"] + d["mail_no"], 570.0, 10.0, w=19, h=19)
    p.text(566.0, 35.0, d["mail_no"], C, 7.6, "#111111")
    p.text(45.0, 113.0, d["date"].strftime("%m/%d/%y"), T, 9.6, "#111111")
    tracking = (d["mail_no"] * 5)[:20]
    p.barcode("USPS_4State", tracking, 36.0, 172.0, w=196, h=12, routing="33019441799")
    p.text(248.0, 172.0, d["mail_no"], C, 7.6, "#111111")
    p.barcode("ECC200DataMatrix", d["mail_no"] + d["account"], 29.0, 191.0, w=18, h=18)
    for i, s in enumerate(d["addr"]):
        p.text(57.0, 189.0 + i * 9.6, s, T, 9.6, "#111111")
    p.barcode("USPS_4State", tracking[::-1], 342.0, 172.0, w=202, h=12, routing="30374099007")
    p.text(342.0, 189.0, NN["caps"], TB, 9.8, "#111111")
    for i, s in enumerate(NN["addr"]):
        p.text(342.0, 199.6 + i * 9.6, s, T, 9.6, "#111111")

    size = 10.6
    y = 272.0
    for label, val, x in [("Account Number:", d["account"], 192.0), ("Account Balance:", f"${d['balance']:,.2f}", 205.0),
                          ("Patient Name:", d["patient"], 217.0)]:
        p.text(56.0, y, label, C, size, "#111111")
        p.text(x, y, val, C, size, "#111111")
        y += 9.7
    paras = [
        ["Please note, this letter is not a bill."],
        ["Dear Patient or Responsible Party:"],
        ["Our physicians cared for the patient named above during a recent hospital",
         "stay. Our group bills separately from the hospital, so you may also receive",
         "statements from the hospital for its own services."],
        ["We sent a claim for our services to the insurance plan on file, and the plan",
         "did not accept it. The plan reports that it cannot match the patient to the",
         "member ID or coverage we have on record."],
        ["WHAT TO DO: Please check the insurance details we have for the patient. If the",
         "coverage or member ID has changed, send the new details through",
         f"{NN['form_url']} or call us at {NN['phone']}."],
        ["Insurance plans accept claims only for a limited time, so a quick reply helps.",
         "If we do not receive updated coverage, or the plan does not pay, within 30 days",
         "of the date of this letter, the balance shown above becomes your",
         "responsibility."],
        ["Sincerely,", "Patient Accounts Team"],
        ["*NOTE: A payment made in the last 14 days may not be shown in the balance above."],
        [f"Para recibir esta informacion en Espanol, llame al {NN['phone']}. Gracias."],
    ]
    y += 10.0
    for para in paras:
        for s in para:
            p.text(56.0, y, s, C, size, "#111111")
            y += LEAD
        y += 9.6
    p.text(300.0, 748.0, d["foot"], "Helvetica", 5.6, "#111111", "center")
    p.fiction_note()
    c.showPage()
    c.save()
