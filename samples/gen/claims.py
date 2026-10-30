"""Insurance claims for one invented birth, grouped into the EOB mailings the family receives.

Codes, quantities, amounts and claim stories are invented from general knowledge of US billing
(public CPT/HCPCS and UB-04 revenue code lists); none is taken from a real bill.

Error flags (case.errors) change the data, not the look:
  E2  the anesthesia claim carries the same 01960 line twice (same date, same amount)
  B1  newborn care (doctors 99460/99462/99238, nursery rev 0170) is filed on the MOTHER's member ID and denied
  P1  set by the pack builder: an EOB page without planted claims is left out of the pack
Without B1 the newborn claims are filed on the baby's own member ID and paid.
"""
from __future__ import annotations

import random
from dataclasses import replace
from datetime import timedelta

from .doc_c import EOB, Claim, EOBLine

PATHOLOGIST = "QUILLFORD, AUGUSTINE P."
ANESTHESIOLOGIST = "TREMAYNE, OSWIN K."
NEONATOLOGISTS = ["VOSKUIJLEN, PETRA", "OYELARAN, BENEDICT T.", "CALLOWAY, ROSAMUND J."]
OB_DOCTOR = "HALVERSEN, MARIT"
LAB = "COASTLINE DIAGNOSTICS REFERENCE LABORATORY SERVICES"
HOSPITALIST = "MBEKI-HOLLOWAY, TAMSIN R."


def r2(x):
    return round(x + 1e-9, 2)


def caps2(person):
    """Patient as the insurer prints it: caps, two spaces between first and last name."""
    return f"{person.first.upper()}  {person.last.upper()}"


class Numbers:
    def __init__(self, rng):
        self.rng = rng
        self.base = 731200000000 + rng.randint(1_000_000, 9_000_000) * 100

    def claim(self):
        self.base += self.rng.randint(800, 90000) * 100
        return str(self.base)


def paid_line(d0, d1, code, charge, rate, coins_pct=0.2, deductible=0.0, reason="CPA"):
    allowed = r2(charge * rate)
    coins = r2((allowed - deductible) * coins_pct)
    return EOBLine(d0, d1, code, charge, r2(charge - allowed), allowed, reason, deductible=deductible, coins=coins,
                   benefit=r2(allowed - deductible - coins))


def build(case):
    rng = random.Random(case.seed * 31 + 5)
    fam, err = case.family, case.errors
    m_acct, b_acct = case.mother_acct, case.baby_acct
    mom, baby = fam.mother, fam.baby
    nums = Numbers(rng)
    admit, born = m_acct.admit, b_acct.admit
    disc = m_acct.discharge
    mother_id = fam.member_id
    baby_id = fam.baby_member_id
    out = {}

    def eob(sheet, print_date, claims_, tag):
        return EOB(sheet, _mail(rng), "", print_date, f"{mom.first} {mom.last}".upper(), _street(fam), _city(fam),
                   fam.group_number, claims_, tag)

    # ---- prenatal: panel at the reference lab + limited ultrasound at the OB office ----
    lab_day = admit - timedelta(days=rng.randint(55, 80))
    us_day = lab_day + timedelta(days=rng.randint(10, 20))
    lab_lines = [paid_line(lab_day, lab_day, code, float(rng.randrange(41, 260)), rng.uniform(0.08, 0.22), 0.1,
                           reason="FS1")
                 for code in ["80055", "81003", "87086", "86762", "86780", "87491", "87591"]]
    us_charge = float(rng.randrange(260, 340))
    us_allowed = r2(us_charge * rng.uniform(0.38, 0.46))
    us_copay = float(rng.choice([25, 30, 50]))
    us_line = EOBLine(us_day, us_day, "76815", us_charge, r2(us_charge - us_allowed), us_allowed, "CDA",
                      copay=us_copay, benefit=r2(us_allowed - us_copay))
    out["eob_prenatal"] = eob(str(rng.randint(10, 99)), us_day + timedelta(days=17),
                              [Claim(nums.claim(), caps2(mom), LAB, mother_id, lab_lines),
                               Claim(nums.claim(), caps2(mom), OB_DOCTOR, mother_id, [us_line])], "prenatal")
    out["us_copay"] = us_copay

    # ---- the OB's professional claim: global delivery + one admission visit ----
    g_line = paid_line(born, born, "59410", float(rng.randrange(5200, 6400)), rng.uniform(0.44, 0.5), 0.2,
                       float(rng.choice([150, 250, 300])), "CDA")
    a_line = paid_line(admit, admit, "99222", float(rng.randrange(310, 420)), rng.uniform(0.4, 0.46), 0.2, 0.0, "CDA")
    ob_claim = Claim(nums.claim(), caps2(mom), OB_DOCTOR, mother_id, [g_line, a_line])
    out["eob_ob"] = eob(str(rng.randint(100, 240)), disc + timedelta(days=rng.randint(24, 30)), [ob_claim], "ob")
    out["ob_claim"] = ob_claim

    # ---- doctors' claims around the birth, several providers ----
    june = [Claim(nums.claim(), caps2(mom), PATHOLOGIST, mother_id,
                  [paid_line(admit, admit, code, float(rng.randrange(36, 120)), rng.uniform(0.1, 0.2), 0.1, reason="FS1")
                   for code in ["85610", "85730", "85384", "86880"]])]
    an = paid_line(admit, admit, "01960", float(rng.randrange(3800, 5200)), rng.uniform(0.17, 0.23), 0.2, reason="CDA")
    an_claim = Claim(nums.claim(), caps2(mom), ANESTHESIOLOGIST, mother_id,
                     [replace(an), replace(an)] if err.get("E2") else [replace(an)])
    june.append(an_claim)
    out["anesthesia_claim"] = an_claim

    nb_patient, nb_id = (caps2(mom), mother_id) if err.get("B1") else (caps2(baby), baby_id)
    neo_claims = []
    for k, (code, d) in enumerate([("99460", born), ("99462", born + timedelta(days=1)), ("99238", b_acct.discharge)]):
        ch = float(rng.randrange(260, 520))
        line = (EOBLine(d, d, code, ch, ch, 0.0, "A27") if err.get("B1")
                else paid_line(d, d, code, ch, rng.uniform(0.38, 0.45), 0.0, reason="CDA"))
        neo_claims.append(Claim(nums.claim(), nb_patient, NEONATOLOGISTS[k], nb_id, [line]))
    out["neonatal_claims"] = neo_claims

    # the newborn's nursery stay at the hospital, from the baby's itemized bill
    sums = {}
    for l in b_acct.lines:
        sums[l.rev] = sums.get(l.rev, 0.0) + l.amt
    nur_lines = []
    for rev in ["0170", "0251", "0302", "0471", "0306"]:
        ch = r2(sums.get(rev, 0.0))
        nur_lines.append(EOBLine(born, b_acct.discharge, rev, ch, ch, 0.0, "A27") if err.get("B1")
                         else paid_line(born, b_acct.discharge, rev, ch, rng.uniform(0.4, 0.5), 0.0, reason="CPA"))
    nursery_claim = Claim(nums.claim(), nb_patient, "SEASIDE REGIONAL HOSPITAL", nb_id, nur_lines)
    out["nursery_claim"] = nursery_claim

    # the mother's other inpatient professional claims (nothing planted) come last and fill a page of their own
    lab_ip = [paid_line(admit + timedelta(days=k // 4), admit + timedelta(days=k // 4), code,
                        float(rng.randrange(28, 96)), rng.uniform(0.1, 0.2), 0.1, reason="FS1")
              for k, code in enumerate(["80048", "83735", "84132", "82947", "85610", "85730", "80048", "83735", "84132",
                                        "82947"])]
    lab_claim = Claim(nums.claim(), caps2(mom), "SHORELINE CLINICAL PATHOLOGY GROUP INC", mother_id, lab_ip)
    hosp = [paid_line(admit + timedelta(days=k + 1), admit + timedelta(days=k + 1), code, float(rng.randrange(170, 290)),
                      rng.uniform(0.36, 0.44), 0.2, reason="CDA") for k, code in enumerate(["99231", "99238"])]
    hosp_claim = Claim(nums.claim(), caps2(mom), HOSPITALIST, mother_id, hosp)
    june += [neo_claims[0], nursery_claim, neo_claims[1], neo_claims[2], lab_claim, hosp_claim]
    out["eob_june"] = eob(str(rng.randint(100, 260)), disc + timedelta(days=rng.randint(33, 40)), june, "june")

    # ---- the mother's hospital claim, paid at the contracted rate per revenue line ----
    msums = {}
    for l in m_acct.lines:
        msums[l.rev] = msums.get(l.rev, 0.0) + l.amt
    revs = [r for r in ["0112", "0722", "0379", "0251", "0637", "0272", "0302", "0309"] if r in msums]
    ded = float(rng.choice([500, 750, 1000]))
    lines = [paid_line(admit, disc, r, r2(msums[r]), rng.uniform(0.42, 0.52), 0.2, ded if i == 0 else 0.0, "CPA")
             for i, r in enumerate(revs)]
    paid = Claim(nums.claim(), caps2(mom), "SEASIDE REGIONAL HOSPITAL", mother_id, lines)
    out["eob_hospital"] = eob(str(rng.randint(10, 99)), disc + timedelta(days=rng.randint(44, 50)), [paid], "hospital")
    out["hospital_claim"] = paid

    # hospital side agrees with the paid claim: patient owes deductible + co-insurance (control "no money here");
    # the hospital prints insurance payment and contractual adjustment as one line
    m_acct.ins_pmts_adj = -r2(sum(l.discount + l.benefit for l in lines))
    m_acct.due_from_insurance = 0.0

    for key in ["eob_prenatal", "eob_ob", "eob_june", "eob_hospital"]:
        e = out[key]
        e.envelope = f"HLNX - {e.mail_code[:8]} - {e.mail_code[8:]} - 001 OF 001"
    return out


def _mail(rng):
    return f"H2PX{rng.randint(300, 399)}M{rng.randint(1000, 9999):06d}"


def _street(fam):
    return f"{fam.address.street.upper()} # {fam.address.apt.replace('Apt ', '')}"


def _city(fam):
    a = fam.address
    return f"{a.city.upper()}, {a.state} {a.zip5}"
