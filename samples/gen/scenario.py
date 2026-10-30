"""One invented family's hospital birth, as data. Every document template reads from here,
so the numbers agree across the hospital bill, the insurer's EOB and the doctors' bills
(or disagree on purpose, where a pack plants an error).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class Person:
    first: str
    last: str
    middle: str = ""

    @property
    def full(self):
        return " ".join(p for p in (self.first, self.middle, self.last) if p)

    def last_first_tight(self):  # Rivera,Anna
        return f"{self.last},{self.first}" + (f" {self.middle}" if self.middle else "")

    def last_first_caps(self):  # RIVERA, ANNA
        return f"{self.last}, {self.first}".upper() + (f" {self.middle.upper()}" if self.middle else "")


@dataclass
class Address:
    street: str
    apt: str
    city: str
    state: str
    zip5: str
    zip4: str

    def city_line_caps(self):
        return f"{self.city.upper()}, {self.state} {self.zip5}"


@dataclass
class ChargeLine:
    svc: date
    rev: str
    px: str
    desc: str
    qty: int
    amt: float


@dataclass
class Guarantor:
    name: str
    street: str
    apt: str
    city_line: str
    guarantor_id: str


@dataclass
class HospitalAccount:
    account_no: str
    patient: Person
    kind: str  # "Inpatient" | "Newborn"
    admit: date
    discharge: date
    lines: list
    ins_pmts_adj: float  # negative
    patient_pmts: float  # zero or negative
    due_from_insurance: float
    print_date: date
    guarantor: Guarantor = None
    providers: dict = field(default_factory=dict)
    rev_summary_shift: bool = False  # the newborn bill prints category labels one row off
    balance_forward: bool = False    # 2024 statement template prints the account as one "Balance Forward" row

    @property
    def total(self):
        return round(sum(l.amt for l in self.lines), 2)

    @property
    def balance(self):
        return round(self.total + self.ins_pmts_adj + self.patient_pmts, 2)

    @property
    def due_from_patient(self):
        return round(self.balance - self.due_from_insurance, 2)


REV_CATEGORY = {
    "0112": "ROOM & BOARD - PRIVATE (ONE BED) - OB",
    "0170": "NURSERY - GENERAL CLASSIFICATION",
    "0251": "PHARMACY - GENERIC DRUGS",
    "0637": "PHARMACY - EXTENSION OF 063X - SELF-ADMINISTRABLE DRUGS",
    "0272": "MEDICAL/SURGICAL SUPPLIES AND DEVICES - STERILE SUPPLY",
    "0302": "LABORATORY - IMMUNOLOGY",
    "0306": "LABORATORY - BACTERIOLOGY & MICROBIOLOGY",
    "0309": "LABORATORY - OTHER LABORATORY",
    "0379": "ANESTHESIA - OTHER ANESTHESIA",
    "0471": "AUDIOLOGY - DIAGNOSTIC",
    "0722": "LABOR ROOM/DELIVERY - DELIVERY ROOM",
}

# Order in which the hospital lists rev categories on the summary table.
REV_ORDER_MOTHER = ["0251", "0637", "0302", "0309", "0272", "0112", "0722", "0379"]
REV_ORDER_BABY = ["0251", "0302", "0170", "0471", "0306"]


def _px(rng, rev, kind="drug"):
    """Internal charge-master code: 3 digits of the rev code + 6 digits; some lab codes run to 11."""
    if kind == "lab11":
        return f"{rev[:3]}{rng.randint(1000000, 9999999)}{rng.randint(1, 9)}"
    return f"{rev[:3]}{rng.randint(100000, 999999)}"


# An invented charge list for an uncomplicated vaginal birth with a 2-night stay. Items, codes, quantities
# and prices are made up from general knowledge of hospital bills; none come from a real bill.
MOTHER_ITEMS = [
    # day offset, rev, description, qty, price range, px kind
    (0, "0112", "HC PRIVATE ROOM POSTPARTUM", 1, (2100, 2600), "drug"),
    (0, "0722", "HC DELIVERY ROOM VAGINAL LEVEL 2", 1, (3300, 4100), "drug"),
    (0, "0379", "HC ANESTHESIA LABOR SUPPORT PER HR", 6, (1400, 1900), "drug"),
    (0, "0251", "CEFAZOLIN 2 G/50 ML IN DEXTROSE IVPB", 1, (180, 260), "drug"),
    (0, "0251", "LACTATED RINGERS IV SOLP 1,000 ML BAG", 3, (90, 140), "drug"),
    (0, "0251", "BUPIVACAINE 0.125% IN NS 250 ML BAG", 1, (310, 420), "drug"),
    (0, "0302", "HC TYPE AND SCREEN PANEL", 1, (390, 520), "lab11"),
    (0, "0302", "HC RPR SYPHILIS SCREEN", 1, (140, 210), "lab11"),
    (0, "0309", "HC COMPREHENSIVE METABOLIC PANEL", 1, (450, 610), "drug"),
    (0, "0272", "HC PERINEAL REPAIR TRAY", 1, (230, 310), "drug"),
    (1, "0112", "HC PRIVATE ROOM POSTPARTUM", 1, None, "same"),
    (1, "0251", "ACETAMINOPHEN 500 MG TAB", 2, (6, 12), "drug"),
    (1, "0251", "IBUPROFEN 600 MG TAB", 3, (5, 11), "drug"),
    (1, "0251", "SIMETHICONE 80 MG CHEW TAB", 1, (4, 9), "drug"),
    (1, "0637", "PRENATAL MULTIVITAMIN TAB", 1, (7, 14), "drug"),
    (1, "0637", "SENNOSIDES 8.6 MG TAB", 2, (3, 8), "drug"),
    (1, "0309", "HC MAGNESIUM SERUM", 1, (160, 240), "drug"),
    (1, "0302", "HC RUBELLA IGG ANTIBODY", 1, (210, 290), "lab11"),
    (1, "0251", "TDAP VACCINE (PF) 0.5 ML SYRINGE", 1, (95, 150), "drug"),
    (2, "0251", "ACETAMINOPHEN 500 MG TAB", 2, None, "same"),
    (2, "0251", "IBUPROFEN 600 MG TAB", 2, None, "same"),
    (2, "0272", "HC ICE PACK PERINEAL", 2, (18, 34), "drug"),
]


def _lines(rng, start, items):
    L, prices, pxs = [], {}, {}
    for off, rev, desc, qty, rng_price, kind in items:
        if kind == "same":
            amt, px = prices[desc], pxs[desc]
        else:
            amt = float(rng.randrange(*rng_price))
            px = _px(rng, rev, kind)
            prices[desc], pxs[desc] = amt, px
        day = start + timedelta(days=off)
        # two different items with the same date, revenue code and amount read like a double charge
        while any(l.svc == day and l.rev == rev and l.amt == amt and l.desc != desc for l in L):
            amt += 1.0
        L.append(ChargeLine(day, rev, px, desc, qty, amt))
    return L


def mother_lines(rng, admit):
    return _lines(rng, admit, MOTHER_ITEMS)


BABY_ITEMS = [
    (0, "0170", "HC NEWBORN ROUTINE CARE LEVEL 1", 1, (2900, 3600), "drug"),
    (0, "0251", "BACITRACIN 500 UNIT/GM OINT 1 GM", 1, (12, 24), "drug"),
    (0, "0251", "SUCROSE 24% ORAL SOLN 2 ML", 1, (9, 18), "drug"),
    (0, "0302", "HC NEWBORN BLOOD TYPE DIRECT COOMBS", 1, (180, 260), "lab11"),
    (1, "0170", "HC NEWBORN ROUTINE CARE LEVEL 1", 1, None, "same"),
    (1, "0471", "HC HEARING SCREEN OAE NEWBORN", 1, (240, 330), "drug"),
    (1, "0306", "HC BILIRUBIN TOTAL TRANSCUTANEOUS", 1, (95, 140), "drug"),
]


def baby_lines(rng, born):
    return _lines(rng, born, BABY_ITEMS)


@dataclass
class Family:
    mother: Person
    baby: Person
    address: Address
    guarantor_id: str
    member_id: str
    group_number: str
    baby_member_id: str = ""


@dataclass
class Case:
    seed: int
    family: Family
    mother_acct: HospitalAccount
    baby_acct: HospitalAccount
    errors: dict


FIRST_F = ["Anna", "Maria", "Elena", "Sofia", "Laura", "Daniela", "Camila", "Julia"]
LAST = ["Rivera", "Delgado", "Moreno", "Castillo", "Navarro", "Herrera", "Ortega", "Salazar"]
BABY = ["Lucas", "Mateo", "Leo", "Adrian", "Noah", "Gabriel"]
MIDDLE = ["James", "Andres", "Thomas", "Alexander", "Rafael"]
STREETS = ["Harbor View Dr", "Seagrape Ln", "Pelican Bay Ct", "Coral Reef Way", "Palmetto Grove Rd"]
CITIES = [("CORAL HAVEN", "33019"), ("PALM SHORES", "33463"), ("BAYSIDE", "33067")]
DOCTORS = {
    "ob": ["HALVERSEN, MARIT", "OKONKWO, ADAEZE L."],
    "ob2": ["DELACROIX-MENDEZ, PAULO R."],
    "consult": ["ABERNATHY, CORINNE WESTLAKE"],
    "peds": ["VANTERPOOL, GRETA ISOLDE"],
}


def build_case(seed=7, errors=None):
    rng = random.Random(seed)
    last = rng.choice(LAST)
    mother = Person(rng.choice(FIRST_F), last)
    baby = Person(rng.choice(BABY), last, rng.choice(MIDDLE))
    city, zip5 = rng.choice(CITIES)
    addr = Address(f"{rng.randint(1100, 9800)} {rng.choice(STREETS)}", f"Apt {rng.randint(101, 940)}",
                   city.title(), "FL", zip5, f"{rng.randint(1000, 9999)}")
    fam = Family(mother, baby, addr, guarantor_id=f"{rng.randint(100000000, 999999999)}",
                 member_id=f"{rng.randint(10**10, 10**11 - 1)}", group_number=rng.choice(["HHNFL", "HHNX", "HLNXT"]),
                 baby_member_id=f"{rng.randint(10**10, 10**11 - 1)}")
    admit = date(2025, rng.randint(2, 4), rng.randint(3, 24))
    born = admit
    print_date = admit + timedelta(days=rng.randint(52, 60))
    m_lines = mother_lines(rng, admit)
    m_total = sum(l.amt for l in m_lines)
    m_ins = -round(m_total * rng.uniform(0.46, 0.52), 2)
    m_due_ins = round(rng.randrange(8000, 11000), 2)
    base = rng.randint(5_000_000, 5_999_999)
    mother_acct = HospitalAccount(
        account_no=f"50{base}{rng.randint(100, 999)}"[:10], patient=mother, kind="Inpatient",
        admit=admit, discharge=admit + timedelta(days=2), lines=m_lines, ins_pmts_adj=m_ins,
        patient_pmts=0.0, due_from_insurance=float(m_due_ins), print_date=print_date,
        providers={"admitting": (DOCTORS["ob"][0], "Obstetrics and Gynecology"),
                   "attending": (DOCTORS["ob2"][0], "Obstetrics and Gynecology"),
                   "anesthesiologist": ("", ""),
                   "procedure": [DOCTORS["ob"][0]],
                   "consultants": (DOCTORS["consult"][0], "Consulting [2]")})
    b_lines = baby_lines(rng, born)
    b_total = sum(l.amt for l in b_lines)
    b_ins = -round(b_total * rng.uniform(0.70, 0.80), 2)
    baby_acct = HospitalAccount(
        account_no=f"50{base + 1}{rng.randint(100, 999)}"[:10], patient=baby, kind="Newborn",
        admit=born, discharge=born + timedelta(days=1), lines=b_lines, ins_pmts_adj=b_ins,
        patient_pmts=0.0, due_from_insurance=round(b_total + b_ins, 2), print_date=print_date,
        providers={"admitting": (DOCTORS["peds"][0], "Pediatrics"),
                   "attending": (DOCTORS["peds"][0], "Pediatrics"),
                   "anesthesiologist": ("", ""),
                   "procedure": [],
                   "consultants": ("", "")},
        rev_summary_shift=True)
    g = Guarantor(mother.full, addr.street, addr.apt, addr.city_line_caps(), fam.guarantor_id)
    mother_acct.guarantor = baby_acct.guarantor = g
    return Case(seed, fam, mother_acct, baby_acct, errors or {})
