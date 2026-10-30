"""Call cheat sheet (pack A). Fixtures are SYNTHETIC (fictional families, hospital letterhead, insurer)."""

import re
from decimal import Decimal

import pytest

from billradar import callprep, checks, plans, policy, writer
from billradar.policies import source
from tests import test_appeal, test_writer

needs_text = pytest.mark.skipif(not all(source.is_cached(p) for p in source.SOURCES),
                                reason="run scripts/fetch_policies.py first")


def known_amounts(case, report=None) -> set[Decimal]:
    blob = case.model_dump_json() + (report.model_dump_json() if report else "")
    return {Decimal(x).quantize(Decimal("0.01")) for x in re.findall(r'"(-?\d+(?:\.\d+)?)"', blob)}


def all_text(sheet: callprep.CallSheet) -> str:
    return " ".join([sheet.say_first] + [n.value for n in sheet.numbers]
                    + [o.they_say + " " + o.answer for o in sheet.objections] + sheet.ask_for)


@needs_text
def test_hospital_sheet_for_the_demo_case():
    c = test_writer.case()
    r = checks.run(c)
    s = callprep.build(c, r, "hospital")
    assert s.title == "Before you call Lakeview Demo Hospital"
    assert "Anna Sample" in s.say_first and "Baby Sample" in s.say_first
    assert {n.value for n in s.numbers} >= {"LDH-000123", "LDH-000124", "C-1002"}
    says = {o.they_say for o in s.objections}
    assert "The discount is only for uninsured patients." in says
    assert "That is what your insurance says you owe." in says
    insured = next(o for o in s.objections if o.they_say.startswith("The discount"))
    assert policy.load("memorial-2026-05").fragment("insured-balances").quote in insured.answer
    assert "(page 3)" in insured.answer
    assert not s.dropped


@needs_text
def test_every_answer_rests_on_a_verified_quote_or_the_papers():
    c = test_writer.case()
    r = checks.run(c)
    s = callprep.build(c, r)
    assert all(writer.verified(ref) for ref in s.refs)
    for o in s.objections:  # an answer with a rule shows that rule's own words
        if o.refs:
            assert any(writer.source_of(x)["quote"] in o.answer for x in o.refs), o.they_say
    for amount in re.findall(r"\$([\d,]+\.\d\d)", all_text(s)):
        assert Decimal(amount.replace(",", "")) in known_amounts(c, r), amount


def test_unverified_rule_drops_the_answer(monkeypatch):
    real = writer.verified
    monkeypatch.setattr(writer, "verified", lambda ref: False if ref.startswith("memorial-2026-05") else real(ref))
    c = test_writer.case()
    s = callprep.build(c, checks.run(c))
    assert "The discount is only for uninsured patients." not in {o.they_say for o in s.objections}
    assert any("insured-balances" in d for d in s.dropped)


def test_insurer_sheet_for_a_denied_claim():
    c = test_appeal.case()
    s = callprep.build(c, checks.run(c), "insurer")
    assert s.who == "Sample Health Plan" and "CLM-7765301" in s.say_first and "P31" in s.say_first
    assert {n.value for n in s.numbers} == {"SHP-000-4417", "CLM-7765301"}
    plan = plans.load("sample-health-plan-2026")
    text = all_text(s)
    for fid in ("no-auth-for-childbirth-stay", "emergency-auth", "appeal-180-days", "appeal-address", "copies-free"):
        assert plan.fragment(fid).quote in text, fid
    assert "April 13, 2027" in text
    assert all(writer.verified(ref) for ref in s.refs)


def test_no_insurer_sheet_without_a_denial():
    c = test_appeal.case()
    c.doc("S2").lines[0].denial_codes = ""
    assert callprep.build(c, checks.run(c), "insurer") is None


def test_no_childbirth_answer_without_a_birth():
    c = test_appeal.case()
    c.profile.people = [p for p in c.profile.people if p.role.value != "newborn"]
    s = callprep.build(c, checks.run(c), "insurer")
    assert "childbirth" not in all_text(s)
    c.profile.context = ["birth"]
    assert "childbirth" in all_text(callprep.build(c, checks.run(c), "insurer"))


def test_tokens_never_reach_the_sheet_without_local_values():
    c = test_appeal.case(local=False)
    s = callprep.build(c, checks.run(c), "insurer")
    assert not re.search(r"\[(ACCT|PATIENT|MEMBER)_\d+\]", all_text(s))
    assert "Member ID" not in {n.label.split(" (")[0] for n in s.numbers}


def test_unknown_target_is_an_error():
    c = test_appeal.case()
    with pytest.raises(ValueError):
        callprep.build(c, checks.run(c), "pharmacy")


@needs_text
def test_deadline_answer_quotes_the_rule_that_gives_that_date():
    c = test_writer.case()
    r = checks.run(c)
    f2 = next(f for f in r.findings if f.kind == checks.Kind.F2)
    late = next(o for o in callprep.build(c, r).objections if "too late" in o.they_say)
    used = next(k for k, v in f2.math.items() if v == f2.deadline.isoformat())
    assert used.startswith("policy")  # in this case the hospital's own window is the later one
    assert late.refs == ["memorial-2026-05#window"] and "September 2, 2026" in late.answer
