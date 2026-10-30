"""Build look-alike sample packs.

    python -m gen.build [--out packs-real] [--only S1]      (run from samples/)

Each pack directory holds what a family would hand to BillRadar:
  *.pdf             statements that come as files (hospital statement and itemized bills) = D1..D3
  *.jpg             phone photos of the paper mail: one sheet per photo for the long EOBs, and
                    "table" photos of 3-4 sheets lying apart; S1..Sn = file-name order, then reading order
  profile.json      household data in the format `billradar run` reads
  truth.json        what was planted on purpose, with amounts and where to find it (for measuring)
  print/            every document as a PDF, for printing on paper
All people, organizations and numbers are invented.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil

import fitz
from datetime import timedelta

from . import answers, claims, doc_a, doc_b, doc_c, doc_d1, doc_d2, doc_d3, doc_e, photo
from .scenario import build_case

SCENARIOS = {
    "brs1-birth-all-errors": {
        "seed": 101, "errors": {"E2": 1, "B1": 1, "E3": 1, "E4": 1, "P1": 1},
        "income": 58_000, "household": 3,
        "note": "Hospital birth, insured family of 3 with income low enough for financial assistance; "
                "all six core error classes planted.",
    },
    "brs1-birth-clean-control": {
        "seed": 102, "errors": {},
        "income": 168_000, "household": 3,
        "note": "Control: same kind of papers, nothing planted, income above every assistance threshold. "
                "Expected: no money findings.",
    },
    "brs1-birth-newborn-duplicate": {
        "seed": 204, "errors": {"E2": 1, "B1": 1, "P1": 1},
        "income": 74_000, "household": 4,
        "note": "Second family: duplicate anesthesia line, newborn care on the mother's ID, a missing EOB page; "
                "OB statement is clean.",
    },
}


def _money(x):
    return round(float(x), 2)


FPL_2026 = [15960, 21640, 27320, 33000, 38680, 44360, 50040, 55720]   # HHS 2026, 48 states
POLICY_TIERS = [(200, 100), (250, 90), (300, 85), (350, 83), (400, 83)]  # tiers of public policy memorial-2026-05


DIGITAL = {"01-hospital-statement.pdf": "D1", "02-hospital-itemized-mother.pdf": "D2", "03-hospital-itemized-newborn.pdf": "D3"}
TABLES = [
    [("04-eob-prenatal", 1), ("04-eob-prenatal", 2), ("05-eob-ob", 1), ("10-pediatric-statement", 1)],
    [("08-anesthesia-bill", 1), ("08-anesthesia-bill", 2), ("08-anesthesia-bill", 3)],
    [("09-obgyn-statement", 1), ("09-obgyn-statement", 2), ("11-neonatal-letter", 1)],
]


def _add_months(d, months):
    y, mth = divmod(d.month - 1 + months, 12)
    return d.replace(year=d.year + y, month=mth + 1)


def _f1(spec, balance):
    """Discount the public policy gives this household on the hospital balance (None if it gives none)."""
    pct = round(spec["income"] * 100 / FPL_2026[spec["household"] - 1], 1)
    tier = next((d for top, d in POLICY_TIERS if pct <= top), None)
    if tier is None or balance <= 500:
        return pct, None
    saving = round(balance * tier / 100 + 1e-9, 2)
    cap = round(spec["income"] * 0.10, 2)
    extra = max(0.0, round(balance - saving - cap, 2))
    return pct, round(saving + extra, 2)


def _evidence_claims(cl, err):
    ev = set()
    if err.get("E2"):
        ev.add(cl["anesthesia_claim"].claim_no)
    if err.get("B1"):
        ev |= {c.claim_no for c in cl["neonatal_claims"]} | {cl["nursery_claim"].claim_no}
    return ev


def _page_without_evidence(pdf, evidence):
    """P1 drops one middle page of the EOB; it must not be a page that carries a planted claim."""
    doc = fitz.open(pdf)
    for i in range(1, len(doc) - 1):
        text = doc[i].get_text()
        if not any(cn in text for cn in evidence):
            return i + 1
    raise RuntimeError(f"{pdf}: every middle page carries planted evidence; cannot drop one")


def build_pack(name, spec, out_root):
    out = out_root / name
    if out.exists():
        shutil.rmtree(out)
    (out / "print").mkdir(parents=True)
    pr = out / "print"
    case = build_case(spec["seed"], spec["errors"])
    cl = claims.build(case)
    m, b = case.mother_acct, case.baby_acct
    seed = spec["seed"]
    files = {}

    # files that arrive as PDFs
    stmt_date = m.print_date - timedelta(days=2)
    doc_a.render([m], m.guarantor, pr / "01-hospital-statement.pdf", stmt_date)
    doc_b.render(m, pr / "02-hospital-itemized-mother.pdf")
    doc_b.render(b, pr / "03-hospital-itemized-newborn.pdf")
    for f in ("01-hospital-statement.pdf", "02-hospital-itemized-mother.pdf", "03-hospital-itemized-newborn.pdf"):
        shutil.copy(pr / f, out / f)
        files[f] = "pdf"

    # paper mail, photographed
    eobs = [("04-eob-prenatal", cl["eob_prenatal"]), ("05-eob-ob", cl["eob_ob"]), ("06-eob-june", cl["eob_june"]),
            ("07-eob-hospital", cl["eob_hospital"])]
    pages_total = {}
    for stem, e in eobs:
        pages_total[stem] = doc_c.render(e, pr / f"{stem}.pdf")
    d1 = doc_d1.build(case, cl, seed)
    doc_d1.render(d1, pr / "08-anesthesia-bill.pdf")
    d2 = doc_d2.build(case, cl, seed)
    doc_d2.render(d2, pr / "09-obgyn-statement.pdf")
    d3 = doc_d3.build(case, cl, seed)
    doc_d3.render(d3, pr / "10-pediatric-statement.pdf")
    e = doc_e.build(case, cl, seed)
    doc_e.render(e, pr / "11-neonatal-letter.pdf")

    missing = {}
    if spec["errors"].get("P1"):
        missing["06-eob-june"] = [_page_without_evidence(pr / "06-eob-june.pdf", _evidence_claims(cl, spec["errors"]))]
    # single-sheet photos for the EOBs that run several pages, "table" photos of 3-4 sheets for the rest
    photos = []   # (file name, [(stem, page), ...] in reading order)
    corners = {}      # file name -> sheet corners in reading order
    for stem in ["06-eob-june", "07-eob-hospital"]:
        n = len(fitz.open(pr / f"{stem}.pdf"))
        keep = [i for i in range(1, n + 1) if i not in missing.get(stem, [])]
        for path, quads in photo.save_photos(pr / f"{stem}.pdf", str(out / stem), seed + len(stem), keep):
            photos.append((pathlib.Path(path).name, [(stem, int(pathlib.Path(path).stem.rsplit("-p", 1)[1]))]))
            corners[pathlib.Path(path).name] = quads
    for k, group in enumerate(TABLES, 1):
        pages = [photo.page_image(pr / f"{stem}.pdf", pg - 1) for stem, pg in group]
        img = photo.table(pages, seed * 10 + k)
        fname = f"table-{k}.jpg"
        img.save(out / fname, "JPEG", quality=86, optimize=True)
        photos.append((fname, group))
        corners[fname] = img.sheet_corners
    sheet_ids = {}
    for fname, group in sorted(photos):
        for stem, pg in group:
            sheet_ids[(stem, pg)] = f"S{len(sheet_ids) + 1}"
        files[fname] = "photo: " + ", ".join(f"{stem} p{pg}" for stem, pg in group)

    fam = case.family
    profile = {
        "policy_id": "memorial-2026-05",
        "letterhead": "Seaside Regional Hospital",
        "today": (cl["eob_hospital"].print_date + timedelta(days=20)).isoformat(),
        "fictional": True,
        "profile": {
            "household_size": spec["household"],
            "annual_income": str(spec["income"]),
            "liquid_assets": "3200",
            "insured": True,
            "people": [{"token": "PATIENT_1", "role": "mother", "name": fam.mother.full,
                        "birth_date": "04/17/1994", "relationship": "Self", "employer": "Harbor Point Dental Studio"},
                       {"token": "PATIENT_2", "role": "newborn", "name": fam.baby.full,
                        "birth_date": b.admit.strftime("%m/%d/%Y"), "relationship": "Son"}],
            "local": {"street": f"{fam.address.street} {fam.address.apt}", "city": fam.address.city,
                      "state": fam.address.state, "zip": fam.address.zip5, "phone": "(954) 555-0161",
                      "tokens": {"ACCT_1": m.account_no, "ACCT_2": b.account_no, "MEMBER_1": fam.member_id,
                                 "MEMBER_2": fam.baby_member_id}},
        },
    }
    (out / "profile.json").write_text(json.dumps(profile, indent=1) + "\n")

    an = cl["anesthesia_claim"]
    planted = []
    err = spec["errors"]
    f1_pct, f1_saving = _f1(spec, m.due_from_patient)
    planted.append({"class": "F1", "planted": f1_saving is not None,
                    "fpl_pct": f1_pct, "amount": f1_saving,
                    "where": ["01-hospital-statement.pdf", "02-hospital-itemized-mother.pdf"],
                    "what": "Hospital papers only invite an assistance application; no assistance line or discount.",
                    "patient_balance": _money(m.due_from_patient),
                    "income_vs_household": [spec["income"], spec["household"]]})
    if f1_saving is not None:
        window = max(m.discharge + timedelta(days=365), _add_months(cl["eob_hospital"].print_date, 12),
                     stmt_date + timedelta(days=240))
        planted.append({"class": "F2", "planted": True, "where": ["01-hospital-statement.pdf", "02-hospital-itemized-mother.pdf"],
                        "what": "Last day to apply for assistance under the policy window.", "deadline": window.isoformat(),
                        "from": {"service_plus_365": (m.discharge + timedelta(days=365)).isoformat(),
                                 "last_insurance_payment_plus_12m": _add_months(cl["eob_hospital"].print_date, 12).isoformat(),
                                 "first_statement_plus_240": (stmt_date + timedelta(days=240)).isoformat()}})
    if err.get("E2"):
        planted.append({"class": "E2", "planted": True, "where": ["08-anesthesia-bill", "06-eob-june"],
                        "what": "Anesthesia 01960 billed twice for the same date and amount; both lines paid with co-insurance.",
                        "date": an.lines[0].dos_from.isoformat(), "code": "01960", "charge_each": _money(an.lines[0].charge),
                        "extra_patient_cost": _money(an.lines[1].coins), "claim_no": an.claim_no})
    if err.get("B1"):
        planted.append({"class": "B1", "planted": True, "where": ["06-eob-june", "11-neonatal-letter"],
                        "what": "Newborn care (99460/99462/99238 and nursery rev 0170) filed on the mother's member ID and "
                                "denied A27; the doctors' letter says the plan cannot match the patient to the member ID.",
                        "claims": [c.claim_no for c in cl["neonatal_claims"]] + [cl["nursery_claim"].claim_no],
                        "letter_balance": _money(e["balance"]), "letter_date": e["date"].isoformat()})
    if err.get("E3"):
        planted.append({"class": "E3", "planted": True, "where": ["09-obgyn-statement-p1"],
                        "what": "Office collected more than the EOB co-pay for 76815; the credit stays in brackets and is "
                                "added into PAY THIS AMOUNT instead of being refunded.",
                        "paid_at_office": _money(d2["paid_office"]), "eob_copay": _money(d2["eob_copay"]),
                        "credit": _money(-d2["credit"])})
    if err.get("E4"):
        planted.append({"class": "E4", "planted": True, "where": ["09-obgyn-statement-p2"],
                        "what": "Same sheet shows 'Your Balance Due On These Services... $0.00' (moved to a payment plan) "
                                "and PAY THIS AMOUNT with a sum.",
                        "balance_line": 0.0, "pay_this_amount": _money(d2["pay"])})
    if err.get("P1"):
        planted.append({"class": "P1", "planted": True, "where": ["06-eob-june"],
                        "what": "EOB pages are numbered 'Page N of M' and a page is not in the pack.",
                        "pages_total": pages_total["06-eob-june"], "missing_pages": missing["06-eob-june"]})
    for cls in ["E2", "B1", "E3", "E4", "P1"]:
        if not err.get(cls):
            planted.append({"class": cls, "planted": False})
    background = [
        "Hospital merges insurance payments and contractual adjustments into one line (statement and itemized).",
        "Newborn itemized bill: 'Summary by Rev Code' labels drift away from their amounts (wrapped labels).",
        "Pediatric statement: small balance with a collections warning.",
        "Control point: the patient balance on the hospital statement equals deductible + co-insurance on the "
        f"hospital EOB ({_money(cl['hospital_claim'].patient_resp)}).",
    ]
    truth = {"pack": name, "note": spec["note"], "fictional": True, "planted": planted, "background": background,
             "files": files, "eob_pages": pages_total, "sheets": {v: f"{k[0]} p{k[1]}" for k, v in sheet_ids.items()},
             "digital": DIGITAL}
    (out / "truth.json").write_text(json.dumps(truth, indent=1) + "\n")
    key = _key(name, out, planted, sheet_ids)
    (out / "key.json").write_text(json.dumps(key, indent=1) + "\n")
    answers.write(out, case, cl, {"d1": d1, "d2": d2, "d3": d3, "e": e, "stmt_date": stmt_date},
                  sheet_ids, dict(sorted(corners.items())), dict(eobs))
    return out, truth


def _key(name, out, planted, sheet_ids):
    """key.json in the core's format: S1..Sn = sheets on the photos (file-name order, then reading order),
    D1..Dn = PDFs in file-name order."""

    def pages_with(stem, needles):
        doc = fitz.open(out / "print" / f"{stem}.pdf")
        return [sheet_ids[(stem, i)] for i, pg in enumerate(doc, 1)
                if (stem, i) in sheet_ids and any(n in pg.get_text() for n in needles)]

    digital = lambda names: [DIGITAL[n] for n in names]
    items = []
    for p in planted:
        if not p["planted"]:
            continue
        c = p["class"]
        if c == "F1":
            items.append({"cls": "F1", "sheets": digital(p["where"]), "amount": f"{p['amount']:.2f}", "deadline": None,
                          "note": f"{p['fpl_pct']}% of the 2026 guideline; no assistance line on the hospital papers"})
        elif c == "F2":
            items.append({"cls": "F2", "sheets": digital(p["where"]), "amount": None, "deadline": p["deadline"],
                          "note": "latest of service + 365 days, last insurance payment + 12 months, first statement + 240 days"})
        elif c == "E2":
            items.append({"cls": "E2", "sheets": pages_with("08-anesthesia-bill", ["CPT/HCPCS code: 01960"])
                          + pages_with("06-eob-june", [p["claim_no"]]),
                          "amount": f"{p['extra_patient_cost']:.2f}", "deadline": None,
                          "note": "01960 twice, same date and amount; the second co-insurance is the overcharge"})
        elif c == "B1":
            items.append({"cls": "B1", "sheets": pages_with("06-eob-june", p["claims"]) + [sheet_ids[("11-neonatal-letter", 1)]],
                          "amount": None, "deadline": None,
                          "note": f"newborn care filed on the mother's member ID (A27 denials); the doctors' letter asks "
                                  f"${p['letter_balance']:,.2f} within 30 days of {p['letter_date']}"})
        elif c == "E3":
            items.append({"cls": "E3", "sheets": [sheet_ids[("09-obgyn-statement", 1)]], "amount": f"{p['credit']:.2f}",
                          "deadline": None, "note": "co-pay overpaid at the office; credit in brackets added into the total"})
        elif c == "E4":
            items.append({"cls": "E4", "sheets": [sheet_ids[("09-obgyn-statement", 2)]], "amount": None, "deadline": None,
                          "note": f"'Your Balance Due ... $0.00' and PAY THIS AMOUNT ${p['pay_this_amount']:,.2f} on one sheet"})
        elif c == "P1":
            items.append({"cls": "P1", "sheets": [v for k, v in sheet_ids.items() if k[0] == "06-eob-june"], "amount": None,
                          "deadline": None, "note": f"EOB page(s) {p['missing_pages']} of {p['pages_total']} not in the pack"})
    return {"pack_id": name, "split": "work", "items": items}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../eval/packs/work")
    ap.add_argument("--only", default=None)
    a = ap.parse_args(argv)
    root = pathlib.Path(a.out)
    for name, spec in SCENARIOS.items():
        if a.only and not name.startswith(a.only):
            continue
        out, truth = build_pack(name, spec, root)
        print(out, sum(1 for p in truth["planted"] if p["planted"]), "planted,", len(truth["files"]), "files")


if __name__ == "__main__":
    main()
