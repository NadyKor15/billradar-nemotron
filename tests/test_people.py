"""Names as a live model reads them, linked to the profile's people. SYNTHETIC packs and people."""

from decimal import Decimal

import pytest

from billradar import people
from billradar.case import Person, Profile, Role
from tests.test_pages import READS, case_from


# --- names -> tokens -------------------------------------------------------------------------



@pytest.mark.parametrize("run", sorted(READS))
def test_every_name_form_in_live_reads_links_to_the_right_person(run):
    c = case_from(READS[run])
    linked = people.link(c.documents, c.profile)
    roles = {d.doc_id: c.profile.role_of(d.patient) for d in linked if d.patient}
    for d, before in zip(linked, c.documents):
        if before.patient and before.patient.upper() not in ("PATIENT_3",):
            assert d.patient in ("PATIENT_1", "PATIENT_2"), (d.doc_id, before.patient)
    babies = [k for k, r in roles.items() if r == Role.NEWBORN]
    assert any(r == Role.MOTHER for r in roles.values())
    mother_first = c.profile.people[0].name.split()[0].lower()
    for d in c.documents:
        if d.patient and mother_first in d.patient.lower():
            assert roles[d.doc_id] == Role.MOTHER, d.patient
        elif d.patient and d.patient.upper() not in ("PATIENT_1", "PATIENT_2", "PATIENT_3"):
            assert d.doc_id in babies, d.patient


def test_a_shared_surname_alone_is_not_a_guess():
    prof = Profile(household_size=2, annual_income=Decimal(1), people=[
        Person(token="PATIENT_1", role=Role.MOTHER, name="Anna Sample"),
        Person(token="PATIENT_2", role=Role.NEWBORN, name="Leo Max Sample")])
    assert people.token_for("SAMPLE, LEO", prof) == "PATIENT_2"
    assert people.token_for("Sample", prof) is None
    assert people.token_for("[PATIENT_2]", prof) == "PATIENT_2"
    assert people.token_for("PATIENT_9", prof) is None
    assert people.token_for("Mark Other", prof) is None
