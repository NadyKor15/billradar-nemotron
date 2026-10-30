"""Context chips: what kind of care this was, in one tap. Each chip says which rules it switches on.

The family picks chips while the papers are read (Profile.context holds their ids). A chip never
adds money by itself: it makes a protection apply ("You marked an emergency visit") or tells a
check to look, and the papers still have to show the problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from billradar import laws


@dataclass(frozen=True)
class Chip:
    id: str
    label: str
    laws: list[str] = field(default_factory=list)  # protection ids from protections.json
    checks: list[str] = field(default_factory=list)  # Kind values


CHIPS: list[Chip] = [
    Chip("birth", "Birth", [], ["F3", "B1"]),
    Chip("er_visit", "ER / emergency", ["nsa-emergency"], ["N1"]),
    Chip("inpatient", "Hospital stay (1+ night)", ["fap-240-days"], ["F1", "F2"]),
    Chip("planned_surgery", "Planned surgery", ["estimate-and-dispute"], []),
    Chip("doctor_visit", "Doctor visit", [], []),
    Chip("labs_imaging", "Labs / imaging", ["nsa-ancillary"], ["N1"]),
    Chip("rooming_in", "Baby stayed with me", [], ["F3", "B1"]),
    Chip("hdhp", "High deductible plan", [], ["E1"]),
    Chip("out_of_network", "A doctor was out of network", ["nsa-ancillary"], ["N1"]),
    Chip("insurance_denied", "Insurance denied a claim", ["appeal-180-days"], ["A1"]),
    Chip("in_collections", "Bill went to collections",
         ["no-collections-120-days", "collections-paused", "lawsuit-3-years"], []),
    Chip("already_paid", "I already paid part of it", ["fap-refund"], []),
]
BY_ID = {c.id: c for c in CHIPS}


def chip(chip_id: str) -> Chip:
    return BY_ID[chip_id]


def valid(ids: list[str]) -> list[str]:
    """Known chip ids in the order given, unknown ones dropped (the form may be older than the code)."""
    return [i for i in dict.fromkeys(ids) if i in BY_ID]


def rules_on(ids: list[str]) -> dict[str, list[str]]:
    """What the chosen chips switch on, for the screen: {"laws": [...], "checks": [...]}."""
    on_laws, on_checks = [], []
    for c in (BY_ID[i] for i in valid(ids)):
        on_laws += [x for x in c.laws if x not in on_laws]
        on_checks += [x for x in c.checks if x not in on_checks]
    known = {p["id"] for p in laws.registry()["protections"]}
    return {"laws": [x for x in on_laws if x in known], "checks": on_checks}
