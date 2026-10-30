import io
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from billradar import app as app_module
from billradar import checks, forms, policy, writer
from billradar.case import Case
from billradar.policies import source

FIX = Path(__file__).parent / "fixtures" / "cases" / "anna-birth.json"
needs_text = pytest.mark.skipif(
    not all(source.is_cached(p) for p in source.SOURCES) or not source.form_path("memorial-fef").exists(),
    reason="run scripts/fetch_policies.py first")

PEOPLE = [{"token": "PATIENT_1", "role": "mother", "name": "Anna Sample", "birth_date": "03/14/1994",
           "relationship": "Self", "employer": "Sample Bakery LLC"},
          {"token": "PATIENT_2", "role": "newborn", "name": "Baby Sample", "birth_date": "08/04/2025",
           "relationship": "Daughter"}]
LOCAL = {"street": "100 Example Street", "city": "Hollywood", "state": "FL", "zip": "33021",
         "phone": "(555) 010-0000", "checking": "2400", "tokens": {"ACCT_1": "LDH-000123", "ACCT_2": "LDH-000124"}}


def case(**changes) -> Case:
    raw = json.loads(FIX.read_text())
    raw["profile"]["people"] = PEOPLE
    raw["profile"]["local"] = LOCAL
    raw.update(changes)
    return Case.model_validate(raw)


def text_of(letter: writer.Letter) -> str:
    return " ".join(p.text for p in letter.paragraphs)


@needs_text
def test_letters_for_the_demo_case():
    c = case()
    letters = {x.kind: x for x in writer.all_letters(c, checks.run(c))}
    assert set(letters) == {"itemized", "apply-discount", "dispute", "application"}
    assert all(not x.dropped for x in letters.values())

    assert "$1,240.00" in text_of(letters["apply-discount"]) and "LDH-000124" in text_of(letters["apply-discount"])
    d = text_of(letters["dispute"])
    assert "$2,887.50" in d and "$2,575.10" in d and "$312.40" in d and "claim C-1002" in d
    a = text_of(letters["application"])
    assert "257.6%" in a and "an 85% discount" in a and "September 2, 2026" in a


@needs_text
def test_every_number_in_a_letter_comes_from_the_case():
    c = case()
    r = checks.run(c)
    blob = c.model_dump_json() + r.model_dump_json()
    known = {Decimal(x).quantize(Decimal("0.01")) for x in re.findall(r'"(-?\d+(?:\.\d+)?)"', blob)}
    for letter in writer.all_letters(c, r):
        for amount in re.findall(r"\$([\d,]+\.\d\d)", text_of(letter)):
            assert Decimal(amount.replace(",", "")) in known, (letter.kind, amount)


@needs_text
def test_letters_cite_only_verified_quotes():
    c = case()
    for letter in writer.all_letters(c, checks.run(c)):
        assert all(policy.verified(r) for r in letter.refs), letter.kind


def test_paragraph_with_unverified_quote_is_dropped(monkeypatch):
    monkeypatch.setattr(policy, "verified", lambda ref: not ref.startswith("fl-395-301"))
    c = case()
    letter = writer.itemized_bill(c)
    assert "Florida law" not in text_of(letter)
    assert letter.dropped and "fl-395-301" in letter.dropped[0]


@needs_text
def test_account_numbers_stay_tokens_without_local_values():
    c = case()
    c.profile.local.tokens = {}
    letter = writer.itemized_bill(c)
    assert "[account number]" in text_of(letter) and "ACCT_" not in text_of(letter)


@needs_text
def test_no_discount_or_dispute_letter_when_nothing_found():
    c = case()
    for d in c.documents:  # bill matches the insurance and both bills already have the discount
        for ln in d.lines:
            if ln.claim_ref == "C-1002":
                ln.patient_responsibility = ln.patient_responsibility if d.kind.value == "eob" else None
    c.doc("S2").fa_discount = c.doc("S2").patient_balance
    kinds = {x.kind for x in writer.all_letters(c, checks.run(c))}
    assert "apply-discount" not in kinds and "dispute" not in kinds


# --- the hospital form ---------------------------------------------------------------------------

@needs_text
def test_memorial_form_is_filled_on_page_one_and_keeps_all_pages():
    filled = forms.fill_memorial(case(demo=True))
    pdf = PdfReader(io.BytesIO(filled.pdf))
    assert len(pdf.pages) == 4
    page1 = pdf.pages[0].extract_text()
    for value in ("LDH-000123", "Anna Sample", "100 Example Street", "7,083.33", "2,400.00", "Sample Bakery LLC",
                  "Baby Sample", "SAMPLE - FICTIONAL DATA"):
        assert value in page1, value
    assert {"signature", "date", "witness", "spouse"} <= set(filled.left_for_you)


@needs_text
def test_a_household_member_the_case_does_not_name_is_a_blank_row_left_for_you():
    c = case(demo=True)
    c.profile.household_size = len(c.profile.people) + 1
    filled = forms.fill_memorial(c)
    page1 = PdfReader(io.BytesIO(filled.pdf)).pages[0].extract_text()
    assert "name to add" not in page1 and "Household member" not in page1  # no placeholder printed on the form
    assert f"member{len(c.profile.people)}" in filled.left_for_you  # the row is listed for the family to write


@needs_text
def test_form_without_demo_flag_has_no_stamp():
    page1 = PdfReader(io.BytesIO(forms.fill_memorial(case()).pdf)).pages[0].extract_text()
    assert "FICTIONAL" not in page1 and "Anna Sample" in page1


# --- routes --------------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "RUNS", tmp_path)
    return TestClient(app_module.app), tmp_path


def save(tmp_path, c: Case, case_id: str = "w1"):
    d = tmp_path / case_id
    d.mkdir()
    (d / "case.json").write_text(c.model_dump_json())
    (d / "report.json").write_text(checks.run(c).model_dump_json())


@needs_text
def test_letter_and_form_routes(client):
    tc, tmp = client
    save(tmp, case(demo=True))
    page = tc.get("/cases/w1").text
    assert "/cases/w1/letters/apply-discount" in page and "/cases/w1/form.pdf" in page
    letter = tc.get("/cases/w1/letters/dispute").text
    assert "Where this comes from" in letter and "SAMPLE" in letter and "Anna Sample" in letter
    assert tc.get("/cases/w1/letters/nope").status_code == 404
    pdf = tc.get("/cases/w1/form.pdf")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")


@needs_text
def test_no_form_for_a_policy_without_one(client):
    tc, tmp = client
    save(tmp, case(policy_id="baptist-2026-09"), "w2")
    assert tc.get("/cases/w2/form.pdf").status_code == 404


def test_itemized_request_leaves_out_what_was_not_read_and_lists_an_account_once():
    c = case()
    bills = [b for b in c.bills() if b.patient_balance]
    first = bills[0]
    twin = first.model_copy(update={"doc_id": "S99"})                      # another page of the same bill
    blank = first.model_copy(update={"doc_id": "S98", "account_ref": "", "service_from": None, "service_to": None})
    c.documents += [twin, blank]
    text = text_of(writer.itemized_bill(c))
    assert "to ;" not in text and "services to" not in text and "[account number]" not in text
    assert text.count(f"account {writer._account(c, first)}") == 1


def test_call_sheet_shows_names_and_numbers_not_covering_tokens():
    from billradar import callprep
    c = case()
    c.profile.local.tokens.update({"CLAIM_1": "CLM-555001"})
    eob = next(iter(c.eobs()), None)
    bill = next(b for b in c.bills() if b.patient_balance)
    c.documents.append(bill.model_copy(update={"doc_id": "S97", "patient": "PATIENT 1"}))  # read with a space
    if eob is not None and eob.lines:
        eob.lines[0].claim_ref = "[CLAIM_1]"
        eob.lines[-1].claim_ref = "[CLAIM_404]"                                            # no value on this device
    sheet = callprep.hospital(c, checks.run(c))
    shown = sheet.say_first + " ".join(f"{n.label} {n.value}" for n in sheet.numbers)
    assert "PATIENT" not in shown and "[CLAIM" not in shown and "CLAIM_404" not in shown
    assert "Anna Sample" in sheet.say_first
    if eob is not None and eob.lines:
        assert any(n.value == "CLM-555001" for n in sheet.numbers)
        eob.lines[0].claim_ref = "ICIAIM 81"                                              # a token copied badly
        assert all(n.value != "ICIAIM 81" for n in callprep.hospital(c, checks.run(c)).numbers)


def test_private_values_order_does_not_depend_on_set_order():
    import subprocess
    import sys
    code = ("import json; from tests.test_writer import case; from billradar import i18n; "
            "print(json.dumps(i18n.private_values(case())))")
    runs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**__import__("os").environ, "PYTHONHASHSEED": seed}).stdout for seed in ("1", "2", "3")}
    assert len(runs) == 1 and runs.pop().strip()
