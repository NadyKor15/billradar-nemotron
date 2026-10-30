"""Ideal answers for a pack, for wiring tests without live models (results are REPLAYED, not a measurement).

answers/reading.json  what a perfect reader would return, in the shape of billradar/reader.SCHEMA:
                      one document per photo sheet (S1..Sn) and per PDF (D1..Dn), text copied as printed
answers/sheets.json   for every photo, the corners of each sheet in S order (pixels of the photo,
                      clockwise from top-left)
Everything is taken from the generator's own values, so it matches the sheets exactly.
"""
from __future__ import annotations

import json

from .claims import caps2
from .common import m_dollar, m_dollar_sign_first, m_paren, m_plain
from .world import ANESTHESIA, HOSPITAL_SYSTEM as HS, INSURER, NEONATAL, OBGYN, PEDIATRIC

DIGITAL = [("D1", "01-hospital-statement"), ("D2", "02-hospital-itemized-mother"), ("D3", "03-hospital-itemized-newborn")]


def _iso(d):
    return d.isoformat() if d else ""


def doc(doc_id, kind, **kw):
    base = {"doc_id": doc_id, "kind": kind, "patient": "", "provider": "", "account_ref": "", "claim_ref": "",
            "statement_date": "", "service_from": "", "service_to": "", "first_after_discharge": False,
            "insurance_payment_date": "", "total_charges": "", "insurance_paid": "", "fa_discount": "",
            "patient_balance": "", "amount_due_coupon": "", "member_ref": "", "page_no": 0, "page_count": 0,
            "lines": []}
    base.update(kw)
    return base


def line(n, **kw):
    base = {"line_no": n, "service_date": "", "code": "", "description": "", "charges": "", "insurance_paid": "",
            "adjustment": "", "fa_discount": "", "patient_responsibility": "", "claim_ref": "", "denial_codes": ""}
    base.update(kw)
    return base


def _hospital_digital(case, acct, doc_id, kind, token, acct_token, stmt_date=None):
    if kind == "hospital_statement":
        return doc(doc_id, kind, patient=token, provider=HS["name"], account_ref=acct_token,
                   statement_date=_iso(stmt_date), service_from=_iso(acct.admit), service_to=_iso(acct.discharge),
                   total_charges=m_dollar(acct.total), insurance_paid=m_dollar(acct.ins_pmts_adj),
                   patient_balance=m_dollar(acct.due_from_patient), amount_due_coupon=m_dollar(acct.due_from_patient))
    lines = [line(i, service_date=_iso(l.svc), code=l.rev, description=l.desc, charges=m_plain(l.amt))
             for i, l in enumerate(acct.lines, 1)]
    due = m_dollar_sign_first(acct.due_from_patient) if round(acct.due_from_patient, 2) else ""
    return doc(doc_id, kind, patient=token, provider=HS["hospital"], account_ref=acct_token,
               statement_date=_iso(acct.print_date), service_from=_iso(acct.admit), service_to=_iso(acct.discharge),
               total_charges=m_plain(acct.total), insurance_paid=m_plain(acct.ins_pmts_adj), patient_balance=due,
               lines=lines)  # a whole PDF is one complete document: no page numbers


def _eob_page(doc_id, eob, page, total, tokens):
    rows = [(ci, li) for p, ci, li in eob.trace if p == page]
    lines = []
    for n, (ci, li) in enumerate(rows, 1):
        cl, l = eob.claims[ci], eob.claims[ci].lines[li]
        resp = round(l.copay + l.deductible + l.coins + l.not_covered, 2)
        lines.append(line(n, service_date=_iso(l.dos_from), code=l.code, charges=m_dollar_sign_first(l.charge),
                          insurance_paid=m_dollar_sign_first(l.benefit), adjustment=m_dollar_sign_first(l.discount),
                          patient_responsibility=m_dollar_sign_first(resp), claim_ref=cl.claim_no,
                          denial_codes=l.reason.replace(" ", ", ")))
    first = eob.claims[rows[0][0]] if rows else eob.claims[0]
    patient, member = tokens["patient"].get(first.patient, ""), tokens["member"].get(first.member_id, "")
    # several claims on one page: claim numbers go on the lines, not on the sheet
    claim_ref = first.claim_no if len({ci for ci, _ in rows}) == 1 else ""
    return doc(doc_id, "eob", patient=patient, provider=INSURER["name"], claim_ref=claim_ref,
               statement_date=_iso(eob.print_date), member_ref=member, page_no=page, page_count=total, lines=lines)


def _anesthesia(doc_id, d, page):
    common = dict(patient="PATIENT_1", provider=f"{ANESTHESIA['brand']} {ANESTHESIA['brand_sub'].title()}",
                  account_ref=d["account"], statement_date=_iso(d["printed"]), service_from=_iso(d["visit"]),
                  service_to=_iso(d["visit"]), page_no=page, page_count=3)
    if page == 1:
        return doc(doc_id, "hospital_statement", total_charges=m_dollar(d["billed"]), insurance_paid=m_dollar(-d["paid"]),
                   patient_balance=m_dollar(d["due_amt"]), **common)
    if page == 2:
        lines = [line(i, service_date=_iso(d["visit"]), code=code, description=name, charges=f"${amt:,.2f}")
                 for i, (name, code, amt) in enumerate(d["lines"], 1)]
        return doc(doc_id, "hospital_statement", total_charges=f"${d['billed']:,.2f}",
                   insurance_paid=f"-${d['paid']:,.2f}", patient_balance=f"${d['due_amt']:,.2f}",
                   amount_due_coupon=f"${d['due_amt']:,.2f}", lines=lines, **common)
    return doc(doc_id, "hospital_statement", total_charges=f"${d['billed']:,.2f}", insurance_paid=f"-${d['paid']:,.2f}",
               patient_balance=f"${d['due_amt']:,.2f}", **common)


def _obgyn(doc_id, d, sheet_no):
    """Each claim on the ledger closes with its own balance line; only the last sheet's last line is the
    statement's closing balance, so earlier sheets leave patient_balance empty."""
    lines, cur, claim, last_balance = [], None, "", ""
    for day, text, charge, pmt, amount, centered in d["sheets"][sheet_no - 1]:
        if text.startswith("Claim:"):
            claim = text.split(",")[0].replace("Claim:", "")
        elif charge is not None:
            code, desc = text.split(" ", 1)
            cur = line(len(lines) + 1, service_date=_iso(day), code=code, description=desc, charges=f"${charge:,.2f}",
                       claim_ref=claim)
            lines.append(cur)
        elif pmt is not None and cur is not None and text == "Primary Insurance Payment":
            cur["insurance_paid"] = m_paren(pmt)
        elif pmt is not None and cur is not None and text == "Primary Insurance Adjustment":
            cur["adjustment"] = m_paren(pmt)
        elif centered and amount is not None:
            last_balance = m_paren(amount)
            for ln in lines:
                if ln["claim_ref"] == claim and not ln["patient_responsibility"]:
                    ln["patient_responsibility"] = last_balance
    return doc(doc_id, "hospital_statement", patient="PATIENT_1", provider=OBGYN["name"], account_ref=d["account"],
               statement_date=_iso(d["bill_date"]),
               patient_balance=last_balance if sheet_no == len(d["sheets"]) else "",
               amount_due_coupon=f"${d['pay']:,.2f}", lines=lines)


def _pediatric(doc_id, d):
    ln = line(1, service_date=_iso(d["visit"]), code="", description="OFFICE VISIT-EST PAT LEVEL 2",
              charges=f"{d['charge']:.2f}", insurance_paid=f"{d['ins']:.2f}", adjustment=f"{d['disc']:.2f}",
              patient_responsibility=f"{d['due']:.2f}", claim_ref=d["claim"])
    return doc(doc_id, "hospital_statement", patient="PATIENT_2", provider=PEDIATRIC["payable"], account_ref=d["acct"],
               statement_date=_iso(d["stmt"]), service_from=_iso(d["visit"]), service_to=_iso(d["visit"]),
               total_charges=f"{d['charge']:.2f}", patient_balance=f"{d['due']:.2f}",
               amount_due_coupon=f"{d['due']:.2f}", lines=[ln])


def _letter(doc_id, e):
    return doc(doc_id, "other", patient="PATIENT_2", provider=NEONATAL["name"], account_ref=e["account"],
               statement_date=_iso(e["date"]), patient_balance=f"${e['balance']:,.2f}")


def write(out, case, cl, extra, sheet_ids, corners, eobs):
    fam = case.family
    tokens = {"patient": {caps2(fam.mother): "PATIENT_1", caps2(fam.baby): "PATIENT_2"},
              "member": {fam.member_id: "MEMBER_1", fam.baby_member_id: "MEMBER_2"}}
    m, b = case.mother_acct, case.baby_acct
    docs = []
    for (stem, page), sid in sorted(sheet_ids.items(), key=lambda kv: int(kv[1][1:])):
        if stem in eobs:
            e = eobs[stem]
            pages = max((p for p, _, _ in e.trace), default=1)
            total = max(pages, _page_count(out, stem))
            docs.append(_eob_page(sid, e, page, total, tokens))
        elif stem == "08-anesthesia-bill":
            docs.append(_anesthesia(sid, extra["d1"], page))
        elif stem == "09-obgyn-statement":
            docs.append(_obgyn(sid, extra["d2"], page))
        elif stem == "10-pediatric-statement":
            docs.append(_pediatric(sid, extra["d3"]))
        elif stem == "11-neonatal-letter":
            docs.append(_letter(sid, extra["e"]))
    docs.append(_hospital_digital(case, m, "D1", "hospital_statement", "PATIENT_1", "[ACCT_1]", extra["stmt_date"]))
    docs.append(_hospital_digital(case, m, "D2", "itemized_bill", "PATIENT_1", "[ACCT_1]"))
    docs.append(_hospital_digital(case, b, "D3", "itemized_bill", "PATIENT_2", "[ACCT_2]"))
    ans = out / "answers"
    ans.mkdir(exist_ok=True)
    (ans / "reading.json").write_text(json.dumps({"documents": docs}, indent=1) + "\n")
    (ans / "sheets.json").write_text(json.dumps(corners, indent=1) + "\n")


def _page_count(out, stem):
    import fitz
    return len(fitz.open(out / "print" / f"{stem}.pdf"))
