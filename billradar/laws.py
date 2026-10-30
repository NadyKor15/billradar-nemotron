"""The Laws block: 13 patient protections, each tied to quotes from the law itself.

The registry is `protections.json`. A protection is shown only if every quote it cites is on its
page in the cached text of the law (policy.verified); otherwise it is held back, never shown from
memory. For each case the code decides whether a protection applies, from the hospital's state and
nonprofit status, the family's insurance, the context chips and the papers themselves, and adds a
date where the law gives one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cache
from pathlib import Path

from billradar import policy
from billradar.case import Case
from billradar.policies import source

REGISTRY = Path(__file__).parent / "protections.json"

# Day counts the code uses, each one printed in a verified quote (see protections.json refs).
APPLICATION_DAYS = 240  # federal-501r#application-period
NO_COLLECTIONS_DAYS = 120  # federal-501r-6#no-eca-120-days
APPEAL_DAYS = 180  # erisa-2560-503-1#appeal-180-days
DISPUTE_DAYS = 120  # nsa-149-620#ppdr-120-days
APPEAL_REF = "erisa-2560-503-1#appeal-180-days"


@dataclass(frozen=True)
class Quote:
    ref: str
    text: str
    url: str
    title: str


@dataclass
class LawCard:
    id: str
    n: int
    title: str
    plain: str
    cite: str
    applies: str  # "yes" | "check" | "no" | "other_state"
    why: str  # one line: why it applies, or what would make it apply
    quotes: list[Quote] = field(default_factory=list)
    deadline: date | None = None
    deadline_label: str = ""
    checks: list[str] = field(default_factory=list)  # Kind values of the findings that use it

    @property
    def refs(self) -> list[str]:
        return [q.ref for q in self.quotes]


@dataclass
class Laws:
    cards: list[LawCard]
    held_back: list[str]  # protection ids whose quotes did not verify
    what_next: str


@cache
def registry() -> dict:
    return json.loads(REGISTRY.read_text())


def protection(pid: str) -> dict:
    return next(p for p in registry()["protections"] if p["id"] == pid)


def refs_for(pid: str) -> list[str]:
    """The verified refs of one protection, for a finding's `laws`. Unverified refs are left out."""
    return [r for r in protection(pid)["refs"] if policy.verified(r)]


def appeal_deadline(notice_date: date) -> date:
    """Last day of the federal minimum appeal window, counted from the denial notice."""
    return notice_date + timedelta(days=APPEAL_DAYS)


def hospital(case: Case) -> dict:
    return registry()["hospitals"].get(case.policy_id, {})


def first_bill_date(case: Case) -> date | None:
    from billradar.checks import after_stay_bills
    bills = [b for b in after_stay_bills(case) if b.statement_date]
    flagged = [b for b in bills if b.first_after_discharge]
    return min((b.statement_date for b in flagged or bills), default=None)


def denial_date(case: Case) -> date | None:
    """Earliest insurance statement with a denied line (a denial code and nothing paid)."""
    dates = [e.statement_date for e in case.eobs() if e.statement_date and any(denied(ln) for ln in e.lines)]
    return min(dates, default=None)


def denied(line) -> bool:
    return bool(line.denial_codes.strip()) and not line.insurance_paid and bool(line.charges)


def quote(ref: str) -> Quote:
    pid, fid = ref.split("#", 1)
    pol = policy.load(pid)
    src = source.SOURCES.get(pid)
    return Quote(ref, pol.fragment(fid).quote, pol.url, src.hospital if src else pol.title)


def _status(case: Case, p: dict) -> tuple[str, str]:
    h = hospital(case)
    ctx = set(case.profile.context)
    to = p["applies_to"]
    if to == "florida_hospital":
        if h.get("state") == "FL":
            return "yes", "The hospital is licensed in Florida."
        return "other_state", "This is Florida law; see what next for other states."
    if to == "nonprofit_hospital":
        if h.get("nonprofit"):
            return "yes", "The hospital is a nonprofit 501(c)(3) hospital."
        return "check", "Applies if the hospital is a nonprofit 501(c)(3) hospital."
    if to == "uninsured":
        if not case.profile.insured:
            return "yes", "You told us you had no insurance for this care."
        return "no", "Only for care without insurance."
    if not case.profile.insured:
        return "no", "Only for care billed to a health plan."
    if p["id"] == "nsa-emergency":
        return ("yes", "You marked an emergency visit.") if "er_visit" in ctx else (
            "check", "Applies to emergency care.")
    if p["id"] == "nsa-ancillary":
        return ("yes", "You marked an out-of-network doctor.") if "out_of_network" in ctx else (
            "check", "Applies if a doctor at the hospital was out of network.")
    if p["id"] == "appeal-180-days":
        if denial_date(case):
            return "yes", "Your insurance statement shows a denied line."
        if "insurance_denied" in ctx:
            return "yes", "You marked a denial from your insurance."
        return "check", "Applies if your insurance denied a claim."
    return "check", ""


def _deadline(case: Case, pid: str) -> tuple[date | None, str]:
    first = first_bill_date(case)
    if pid == "fap-240-days" and first:
        return first + timedelta(days=APPLICATION_DAYS), "apply at least until"
    if pid == "no-collections-120-days" and first:
        return first + timedelta(days=NO_COLLECTIONS_DAYS), "no collections before"
    if pid == "appeal-180-days" and (d := denial_date(case)):
        return appeal_deadline(d), "appeal by"
    if pid == "estimate-and-dispute" and not case.profile.insured and first:
        return first + timedelta(days=DISPUTE_DAYS), "dispute by"
    return None, ""


def for_case(case: Case) -> Laws:
    cards, held = [], []
    reg = registry()
    for p in sorted(reg["protections"], key=lambda p: p["n"]):
        if not p["refs"] or not all(policy.verified(r) for r in p["refs"]):
            held.append(p["id"])
            continue
        applies, why = _status(case, p)
        deadline, label = _deadline(case, p["id"]) if applies == "yes" else (None, "")
        cards.append(LawCard(p["id"], p["n"], p["title"], p["plain"], p["cite"], applies, why,
                             [quote(r) for r in p["refs"]], deadline, label, list(p["checks"])))
    order = {"yes": 0, "check": 1, "other_state": 2, "no": 3}
    cards.sort(key=lambda c: (order[c.applies], c.n))
    return Laws(cards, held, reg["what_next_other_states"])
