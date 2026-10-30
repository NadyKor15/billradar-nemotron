"""The family case: every paper sheet and digital file, read into one structure.

Amounts arrive from the reader as text ("$3,215.40", "(312.40)") and are parsed
here, in code, into Decimal cents. The model reads; code counts.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


def parse_money(text: str | None) -> Decimal | None:
    """'$3,215.40' -> 3215.40 · '(312.40)' or '-$312.40' -> -312.40 · '' -> None."""
    if text is None:
        return None
    s = text.strip()
    if not s or s in {"-", "—", "N/A", "n/a"}:
        return None
    negative = s.startswith("(") and s.endswith(")") or s.startswith("-") or s.endswith("CR")
    digits = re.sub(r"[^0-9.]", "", s)
    if not digits or digits.count(".") > 1:
        return None
    try:
        value = Decimal(digits).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_date(text: str | None) -> date | None:
    """ISO 'YYYY-MM-DD' or US 'MM/DD/YYYY'; anything else -> None (never guessed)."""
    if not text:
        return None
    s = text.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
        if not m:
            return None
        mo, d, y = map(int, m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


class DocKind(StrEnum):
    HOSPITAL_STATEMENT = "hospital_statement"
    ITEMIZED_BILL = "itemized_bill"
    EOB = "eob"
    FA_LETTER = "fa_letter"
    PAYMENT_RECEIPT = "payment_receipt"
    OTHER = "other"


class Source(StrEnum):
    PAPER = "paper"
    PDF = "pdf"
    SCREENSHOT = "screenshot"


class Line(BaseModel):
    """One printed line: a charge on a bill, or a claim line on an EOB."""

    line_no: int
    service_date: date | None = None
    code: str = ""
    description: str = ""
    charges: Decimal | None = None
    insurance_paid: Decimal | None = None
    adjustment: Decimal | None = None
    fa_discount: Decimal | None = None
    patient_responsibility: Decimal | None = None
    claim_ref: str = ""
    denial_codes: str = ""  # remark/denial codes as printed, e.g. "X17" or "X05, X42"


class Document(BaseModel):
    doc_id: str  # "S1".."Sn" for paper sheets (number shown on screen), "D1".. for digital files
    source: Source
    kind: DocKind
    patient: str = ""  # a token from the local profile, e.g. "PATIENT_1"; never a real name
    provider: str = ""
    account_ref: str = ""
    claim_ref: str = ""
    statement_date: date | None = None
    service_from: date | None = None
    service_to: date | None = None
    first_after_discharge: bool = False
    insurance_payment_date: date | None = None
    total_charges: Decimal | None = None
    insurance_paid: Decimal | None = None
    fa_discount: Decimal | None = None
    patient_balance: Decimal | None = None
    amount_due_coupon: Decimal | None = None  # the "PAY THIS AMOUNT" box, when the sheet has one
    member_ref: str = ""  # insurance member ID as printed (a token)
    page_no: int | None = None  # "Page 2 of 5" -> 2, 5
    page_count: int | None = None
    lines: list[Line] = Field(default_factory=list)


class Role(StrEnum):
    MOTHER = "mother"
    NEWBORN = "newborn"
    OTHER = "other"


class Person(BaseModel):
    token: str
    role: Role
    name: str = ""  # shown on this device only; the cloud sees the token
    birth_date: str = ""  # local only, for the hospital form
    relationship: str = ""  # "Self", "Daughter", ... as the form asks
    employer: str = ""


class LocalDetails(BaseModel):
    """What only the family's letters and forms need. Never sent to any model."""

    street: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    phone: str = ""
    spouse_monthly_gross: Decimal | None = None
    other_monthly_income: Decimal | None = None
    rent: Decimal | None = None
    electricity: Decimal | None = None
    phone_bill: Decimal | None = None
    checking: Decimal | None = None
    savings: Decimal | None = None
    residence_value: Decimal | None = None
    tokens: dict[str, str] = Field(default_factory=dict)  # "ACCT_1" -> the real account number


class Plan(BaseModel):
    """The family's health plan as the hospital's price file names it, e.g. Aetna / Qualified Health Plan."""

    payer: str
    plan_name: str


class Profile(BaseModel):
    """Kept on the device. Only tokens and the numbers the checks need."""

    household_size: int
    annual_income: Decimal
    liquid_assets: Decimal | None = None
    insured: bool = True
    people: list[Person] = Field(default_factory=list)
    local: LocalDetails = Field(default_factory=LocalDetails)
    context: list[str] = Field(default_factory=list)  # chips the family set, e.g. "er_visit", "out_of_network"
    plan: Plan | None = None  # to compare with the plan's rates in the hospital's public price file

    @field_validator("household_size")
    @classmethod
    def _size(cls, v: int) -> int:
        if v < 1:
            raise ValueError("household_size must be >= 1")
        return v

    def role_of(self, token: str) -> Role:
        return next((p.role for p in self.people if p.token == token), Role.OTHER)


class Case(BaseModel):
    case_id: str
    letterhead: str = ""  # name printed on the sheets (demo: a fictional hospital)
    policy_id: str  # which real public policy the case is checked against
    today: date
    demo: bool = False  # sample data: every generated document carries a SAMPLE stamp
    profile: Profile
    documents: list[Document]

    def doc(self, doc_id: str) -> Document:
        return next(d for d in self.documents if d.doc_id == doc_id)

    def bills(self) -> list[Document]:
        return [d for d in self.documents if d.kind in (DocKind.HOSPITAL_STATEMENT, DocKind.ITEMIZED_BILL)]

    def eobs(self) -> list[Document]:
        return [d for d in self.documents if d.kind == DocKind.EOB]
