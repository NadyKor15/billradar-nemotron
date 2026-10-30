"""Insurance appeal (pack A): the plan document as quoted data, denials, the deadline, the letter and the form.
Fixture `sample-appeal` is SYNTHETIC: fictional family, hospital letterhead and insurer."""

import io
import json
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader

from billradar import appeal, forms, plans, writer
from billradar.case import Case
from billradar.verifier import RISKY_WORDS

FIX = Path(__file__).parent / "fixtures" / "cases" / "sample-appeal.json"
PLAN = "sample-health-plan-2026"
PEOPLE = [{"token": "PATIENT_1", "role": "mother", "name": "Maria Sample"},
          {"token": "PATIENT_2", "role": "newborn", "name": "Leo Sample"}]
LOCAL = {"street": "12 Example Lane", "city": "Riverton", "state": "ST", "zip": "12345",
         "phone": "(555) 010-0199", "tokens": {"ACCT_1": "LDH-45821", "MEMBER_1": "SHP-000-4417"}}


def case(local: bool = True) -> Case:
    raw = json.loads(FIX.read_text())
    raw["profile"]["people"] = PEOPLE
    if local:
        raw["profile"]["local"] = LOCAL
    return Case.model_validate(raw)


def text_of(letter: writer.Letter) -> str:
    return " ".join(p.text for p in letter.paragraphs)


# --- the plan document ---------------------------------------------------------------------

def test_every_plan_quote_is_on_its_page():
    plan = plans.load(PLAN)
    assert plan.sample and len(plan.fragments) >= 10
    for frag in plan.fragments.values():
        assert plans.verify(frag) == (True, "ok"), frag.fragment_id


def test_made_up_quote_and_wrong_page_are_rejected():
    frag = plans.load(PLAN).fragment("emergency-auth")
    assert plans.verify(replace(frag, quote="Covered services will always be paid in full."))[0] is False
    ok, why = plans.verify(replace(frag, page=1))
    assert ok is False and "page 2" in why


def test_committed_pdf_matches_its_text_source():
    rebuilt = PdfReader(io.BytesIO(plans.render_pdf(plans.source_pages(PLAN))))
    assert [p.extract_text() for p in rebuilt.pages] == list(plans.page_texts(PLAN))
    assert all("SAMPLE" in t for t in plans.page_texts(PLAN))


def test_appeal_days_come_from_the_quote():
    plan = plans.load(PLAN)
    assert f"{plan.appeal_days} days" in plan.fragment(plan.appeal_days_fragment).quote


def test_insurer_name_finds_the_plan():
    assert plans.for_insurer("Sample Health Plan") == PLAN
    assert plans.for_insurer("SAMPLE  HEALTH PLAN") == PLAN
    assert plans.for_insurer("Harborlight Health Next") is None


# --- denials and the deadline --------------------------------------------------------------

def test_only_codes_the_plan_calls_denials():
    found = appeal.denials(case())
    assert len(found) == 1
    d = found[0]
    assert (d.eob_id, d.claim_ref, d.codes, d.line_nos) == ("S2", "CLM-7765301", ["P31"], [1])
    assert d.patient_responsibility == Decimal("6840.00") and d.denial_date == date(2026, 10, 15)


def test_unknown_code_is_not_a_denial():
    c = case()
    c.doc("S2").lines[0].denial_codes = "ZZ9"
    assert appeal.denials(c) == []


def test_deadline_is_180_days_from_the_notice():
    d = appeal.denials(case())[0]
    dl = appeal.deadline(plans.load(PLAN), d)
    assert dl.day == date(2027, 4, 13)
    assert dl.refs == [f"plan:{PLAN}#appeal-180-days"]


# --- the letter ----------------------------------------------------------------------------

def test_appeal_letter_quotes_the_plan():
    c = case()
    [letter] = writer.appeal_letters(c)
    t = text_of(letter)
    plan = plans.load(PLAN)
    for fid in ("childbirth-covered", "no-auth-for-childbirth-stay", "emergency-auth"):
        assert plan.fragment(fid).quote in t, fid
    assert "Prior authorization was not obtained for this service." in t
    assert "claim CLM-7765301" in t and "Maria Sample" in t and "$6,840.00" in t
    assert "April 13, 2027" in t and "October 15, 2026" in t
    assert "SHP-000-4417" in letter.subject
    assert letter.to.startswith("Appeals Department, Sample Health Plan")
    assert all(writer.verified(r) for r in letter.refs)


def test_federal_paragraph_waits_for_the_law_base():
    """Until pack B's law base has 29 CFR 2560.503-1, that paragraph is dropped and reported, never sent."""
    [letter] = writer.appeal_letters(case())
    if writer.verified(appeal.FEDERAL_APPEAL_REF):
        assert "2560.503-1" in text_of(letter)
    else:
        assert "2560.503-1" not in text_of(letter)
        assert any(appeal.FEDERAL_APPEAL_REF in x for x in letter.dropped)


def test_every_amount_comes_from_the_case_and_no_token_is_left():
    c = case()
    blob = c.model_dump_json()
    known = {Decimal(x).quantize(Decimal("0.01")) for x in re.findall(r'"(-?\d+(?:\.\d+)?)"', blob)}
    for letter in writer.appeal_letters(c):
        t = text_of(letter)
        for amount in re.findall(r"\$([\d,]+\.\d\d)", t):
            assert Decimal(amount.replace(",", "")) in known, amount
        assert not re.search(r"\[(ACCT|PATIENT|MEMBER)_\d+\]", t + letter.subject)
        assert not any(w in t.lower() for w in RISKY_WORDS)


def test_unverified_plan_quote_drops_its_paragraph(monkeypatch):
    real = plans.verified
    monkeypatch.setattr(plans, "verified", lambda ref: False if ref.endswith("#emergency-auth") else real(ref))
    [letter] = writer.appeal_letters(case())
    assert "emergency situations" not in text_of(letter)
    assert any("emergency-auth" in x for x in letter.dropped)


def test_no_letter_without_a_ground_to_quote(monkeypatch):
    plan = plans.load(PLAN)
    monkeypatch.setattr(plan, "grounds", {})
    assert writer.appeal_letters(case()) == []


def test_without_local_values_no_member_id_is_invented():
    c = case(local=False)
    [letter] = writer.appeal_letters(c)
    assert "member ID" not in letter.subject and "SHP-" not in letter.subject
    assert appeal.form_values(c, plans.load(PLAN), appeal.denials(c)[0])["member_id"] == ""


def test_sources_and_plain_text():
    c = case()
    [letter] = writer.appeal_letters(c)
    src = writer.sources(letter)
    assert src and all(s["quote"] and s["page"] for s in src)
    # the plan's own quotes, plus the federal minimum once its quote verifies (29 CFR 2560.503-1)
    titles = {s["title"] for s in src}
    assert plans.load(PLAN).title in titles
    assert titles <= {plans.load(PLAN).title, "29 CFR 2560.503-1, claims procedure"}
    txt = writer.plain_text(letter, c)
    assert txt.startswith("SAMPLE - FICTIONAL DATA") and "Subject: Appeal of denied claim CLM-7765301" in txt
    assert "Sincerely," in txt and "Maria Sample" in txt and "12 Example Lane" in txt and txt.endswith("\n")


# --- the insurer's form --------------------------------------------------------------------

def test_insurer_appeal_form_is_filled_and_stamped():
    c = case()
    d = appeal.denials(c)[0]
    filled = forms.fill_insurer_appeal(c, plans.load(PLAN), d)
    page = PdfReader(io.BytesIO(filled.pdf)).pages[0].extract_text()
    for value in ("Sample Health Plan", "Member Appeal Form", "Maria Sample", "SHP-000-4417", "CLM-7765301",
                  "07/14/2026", "10/15/2026", "P31", "6,840.00", "SAMPLE - FICTIONAL DATA"):
        assert value in page, value
    assert {"signature", "date"} <= set(filled.left_for_you)
    assert "member_id" in filled.filled


@pytest.mark.parametrize("demo", [True, False])
def test_form_is_always_marked_as_fictional(demo):
    c = case()
    c.demo = demo
    page = PdfReader(io.BytesIO(forms.fill_insurer_appeal(c, plans.load(PLAN), appeal.denials(c)[0]).pdf)
                     ).pages[0].extract_text()
    assert "SAMPLE - FICTIONAL" in page


def test_birth_grounds_need_a_birth_in_the_case():
    c = case()
    c.profile.people = [p for p in c.profile.people if p.role.value != "newborn"]
    [letter] = writer.appeal_letters(c)
    t = text_of(letter)
    assert "childbirth" not in t and "for the birth" not in t
    assert plans.load(PLAN).fragment("emergency-auth").quote in t
    c.profile.context = ["birth"]
    assert "for the birth of my child" in text_of(writer.appeal_letters(c)[0])


def test_letter_states_no_fact_the_case_does_not_hold():
    """Our sentences around the quotes ask or point to the rule; they never claim how long the stay was
    or why authorization was missing."""
    t = text_of(writer.appeal_letters(case())[0]).lower()
    for claim in ("within this limit", "could not be scheduled", "was an emergency", "48 hours after my"):
        assert claim not in t
