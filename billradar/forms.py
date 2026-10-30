"""Fill a hospital's real, public application form on the device.

Memorial's Financial Evaluation Form (2310-10040) is a flat PDF without form fields,
so values are drawn on top of page 1 at the positions of its printed labels
(measured with `pdftotext -bbox`). Pages 2-4 (the list of documents to attach)
are kept as they are. Nothing is sent anywhere; the family prints, signs and
attaches the documents.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas

from billradar.case import Case, Role
from billradar.policies import source

PAGE_H = 792.0  # Letter; pdftotext measures y from the top, reportlab from the bottom
INK = Color(0.05, 0.2, 0.45)
STAMP = Color(0.85, 0.15, 0.15, alpha=0.55)

# (x, top-y) in points where each value starts, from the label boxes on page 1.
MEMORIAL_FEF = {
    "account": (36, 122), "mr": (362, 122),
    "patient": (112, 146.5), "street": (112, 156.5), "phone": (406, 156.5),
    "city": (52, 166.5), "state": (274, 166.5), "zip": (418, 166.5),
    "row0": 280, "row_h": 16.2, "cols": (36, 228, 334, 434),
    "salary": (212, 412), "spouse": (212, 425.5), "other": (212, 439),
    "rent": (464, 412), "electricity": (464, 425.5), "phone_bill": (464, 439),
    "checking": (257, 477), "savings": (257, 489), "residence": (257, 501),
}


@dataclass
class FilledForm:
    pdf: bytes
    filled: list[str]  # which fields got a value
    left_for_you: list[str]  # which fields the family still has to write by hand


def _amount(x: Decimal | None) -> str:
    return "" if x is None else f"{x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def memorial_values(case: Case) -> dict[str, str]:
    p, loc = case.profile, case.profile.local
    mother = next((x for x in p.people if x.role == Role.MOTHER), None)
    from billradar.checks import after_stay_bills
    stay = [b for b in after_stay_bills(case) if b.account_ref] or [b for b in case.bills() if b.account_ref]
    mom = [b for b in stay if mother and case.profile.role_of(b.patient) == Role.MOTHER] or stay
    # the hospital's own account for the stay; an account number the reader covered ([ACCT_n]) over a guarantor code
    account = next((b.account_ref for b in mom if b.account_ref.startswith("[ACCT")), mom[0].account_ref if mom else "")
    account = loc.tokens.get(account.strip("[]"), "" if account.startswith("[") else account)
    values = {
        "account": account, "mr": "",
        "patient": mother.name if mother else "",
        "street": loc.street, "phone": loc.phone, "city": loc.city, "state": loc.state, "zip": loc.zip,
        "salary": _amount(p.annual_income / 12),
        "spouse": _amount(loc.spouse_monthly_gross), "other": _amount(loc.other_monthly_income),
        "rent": _amount(loc.rent), "electricity": _amount(loc.electricity), "phone_bill": _amount(loc.phone_bill),
        "checking": _amount(loc.checking), "savings": _amount(loc.savings), "residence": _amount(loc.residence_value),
    }
    for i, person in enumerate(p.people[:6]):
        values[f"member{i}"] = "|".join([person.name, person.birth_date, person.relationship, person.employer])
    for i in range(len(p.people), min(p.household_size or 0, 6)):  # the household counts more people than the case names:
        values[f"member{i}"] = ""  # a blank row for the family to write in, listed as left empty
    return values


def fill_memorial(case: Case) -> FilledForm:
    blank = source.form_path("memorial-fef")
    if not blank.exists():
        raise FileNotFoundError("run scripts/fetch_policies.py to download the blank form")
    values = memorial_values(case)
    pos = MEMORIAL_FEF
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, PAGE_H))
    c.setFillColor(INK)
    c.setFont("Helvetica", 9.5)
    filled, empty = [], []
    for key, (x, top) in ((k, v) for k, v in pos.items() if isinstance(v, tuple) and len(v) == 2 and k != "cols"):
        text = values.get(key, "")
        (filled if text else empty).append(key)
        if text:
            c.drawString(x, PAGE_H - top, text)
    for i in range(6):
        row = values.get(f"member{i}")
        if not row:
            if f"member{i}" in values:
                empty.append(f"member{i}")
            continue
        top = pos["row0"] + i * pos["row_h"]
        for x, cell in zip(pos["cols"], row.split("|")):
            if cell:
                c.drawString(x, PAGE_H - top, cell)
        filled.append(f"member{i}")
    if case.demo:
        c.saveState()
        c.setFillColor(STAMP)
        c.setFont("Helvetica-Bold", 11)  # in the top margin, off the form's own text
        c.drawCentredString(306, PAGE_H - 16, "SAMPLE - FICTIONAL DATA · Filled by BillRadar for a demo. Not a real application.")
        c.restoreState()
        c.saveState()
        c.setFillColor(STAMP)
        c.setFont("Helvetica", 7)
        from billradar.writer import DEMO_RULES
        c.drawCentredString(306, 14, DEMO_RULES)  # bottom margin: whose form this is, for a made-up case
        c.restoreState()
    c.save()

    overlay = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
    reader = PdfReader(str(blank))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i == 0:
            page.merge_page(overlay)
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    left = [k for k in empty if k != "mr"] + ["signature", "date", "witness"]
    return FilledForm(out.getvalue(), filled, left)


# --- the insurer's member appeal form ------------------------------------------------------

APPEAL_FIELDS = [  # (key, label) in the order they appear on the form and on the copy of the portal
    ("patient_name", "Patient name"), ("member_id", "Member ID"), ("claim_number", "Claim number"),
    ("service_dates", "Date(s) of service"), ("provider", "Provider or hospital"),
    ("denial_date", "Date on the denial notice"), ("denial_codes", "Remark code(s) on the notice"),
    ("amount", "Amount in dispute ($)"), ("street", "Street address"), ("city", "City"), ("state", "State"),
    ("zip", "ZIP"), ("phone", "Phone"), ("reason", "Why you disagree"), ("attachments", "Documents attached"),
]


def fill_insurer_appeal(case: Case, plan, denial) -> FilledForm:
    """The member appeal form of the fictional Sample Health Plan, drawn and filled on the device.
    The form itself is ours (the insurer is not real), so every copy carries the SAMPLE mark."""
    from billradar import appeal as appeals

    values = appeals.form_values(case, plan, denial)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, PAGE_H), invariant=1)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(46, 744, f"{plan.insurer}")
    c.setFont("Helvetica", 11)
    c.drawString(46, 726, "Member Appeal Form")
    c.setFont("Helvetica", 8.5)
    for i, line in enumerate(plan.appeals_to):
        c.drawRightString(566, 748 - i * 11, line)
    y = 690
    filled, empty = [], []
    for key, label in APPEAL_FIELDS:
        text = values.get(key, "")
        tall = key == "reason"
        h = 34 if tall else 22
        c.setStrokeColor(Color(0.55, 0.6, 0.65))
        c.rect(46, y - h, 520, h, stroke=1, fill=0)
        c.setFillColor(Color(0.35, 0.38, 0.42))
        c.setFont("Helvetica", 7.5)
        c.drawString(50, y - 9, label)
        c.setFillColor(INK)
        c.setFont("Helvetica", 9.5)
        if text:
            c.drawString(52, y - 19, text[:110])
            filled.append(key)
        else:
            empty.append(key)
        y -= h + 6
    c.setFillColor(Color(0, 0, 0))
    c.setFont("Helvetica", 8)
    c.drawString(46, y - 4, "By signing, I ask the plan to review its decision on the claim above.")
    for x, label in ((46, "Signature of member or authorized representative"), (380, "Date")):
        c.line(x, y - 40, x + (300 if x == 46 else 186), y - 40)
        c.drawString(x, y - 50, label)
    c.saveState()
    c.setFillColor(STAMP)
    c.setFont("Helvetica-Bold", 30)
    c.translate(306, 430)
    c.rotate(18)
    c.drawCentredString(0, 0, "SAMPLE - FICTIONAL DATA" if case.demo else "SAMPLE - FICTIONAL INSURER")
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(0, -22, "Demo form of a made-up insurer. Not a real appeal.")
    c.restoreState()
    c.save()
    return FilledForm(buf.getvalue(), filled, empty + ["signature", "date"])
