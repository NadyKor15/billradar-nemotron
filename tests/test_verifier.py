import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from billradar import app as app_module
from billradar import checks, pipeline, verifier, writer
from billradar.checks import Evidence, Kind
from billradar.policies import source
from tests.test_writer import case
from tests.fakes import FakeClient

needs_text = pytest.mark.skipif(not all(source.is_cached(p) for p in source.SOURCES),
                                reason="run scripts/fetch_policies.py first")


def run_all(c):
    r = checks.run(c)
    return r, writer.all_letters(c, r)


@needs_text
def test_clean_case_passes_the_checker():
    c = case()
    r, letters = run_all(c)
    v = verifier.verify(c, r, letters)
    assert v.ok, v.issues
    assert v.checked == len(r.findings) + len(letters)


@needs_text
def test_a_wrong_amount_is_caught_and_held_back():
    c = case()
    r, _ = run_all(c)
    f3 = next(f for f in r.findings if f.kind == Kind.F3)
    f3.amount = Decimal("1100.00")  # as if the discount had been computed on the wrong balance
    v = verifier.check_findings(c, r)
    assert [i.subject.split(" on ")[0] for i in v.issues if i.severity == "error"] == ["F3 #1", "total"]
    shown, _ = pipeline.check_and_hold(c, r)
    assert Kind.F3 not in {f.kind for f in shown.findings}
    assert any("held back by the checker" in f.detail for f in shown.held_back)


@needs_text
def test_wrong_deadline_is_caught():
    c = case()
    r, _ = run_all(c)
    f2 = next(f for f in r.findings if f.kind == Kind.F2)
    f2.deadline = f2.deadline.replace(day=1)
    assert any(i.subject.startswith("F2 #1") for i in verifier.check_findings(c, r).issues)


@needs_text
def test_evidence_must_exist():
    c = case()
    r, _ = run_all(c)
    r.findings[0].evidence.append(Evidence(doc_id="S9", line_no=3))
    assert any("S9" in i.what for i in verifier.check_findings(c, r).issues)


@needs_text
def test_letters_with_made_up_amounts_tokens_or_promises_are_caught():
    c = case()
    r, letters = run_all(c)
    d = next(x for x in letters if x.kind == "dispute")
    d.paragraphs.append(writer.Paragraph("We promise you will get $9,999.99 back for account [ACCT_1]."))
    issues = [i.what for i in verifier.check_letters(c, r, [d]).issues]
    assert any("$9,999.99" in w for w in issues)
    assert any("token" in w for w in issues)
    assert any("we promise" in w for w in issues)


@needs_text
def test_second_read_is_read_only_and_never_sees_names():
    c = case()
    r, letters = run_all(c)
    body = verifier.review_body(c, r, letters)
    text = json.dumps(body)
    assert body["model"] == "checker" and body["store"] is False
    assert "Anna Sample" not in text and "100 Example Street" not in text and "LDH-000123" not in text

    answer = {"items": [{"subject": "F3 #1", "verdict": "confirmed", "reason": "matches sheet 2"},
                        {"subject": "letter dispute", "verdict": "problem", "reason": "line 2 says 2,887.50"}]}

    class Fake:
        def events(self, b):
            return [{"type": "response.output_text.delta", "delta": json.dumps(answer)},
                    {"type": "response.completed", "response": {"id": "r", "usage": {}}}]

    v = verifier.model_review(FakeClient(Fake()), c, r, letters,
                              verifier.Verdict())
    assert len(v.model_notes) == 2
    assert [i.severity for i in v.issues] == ["note"]  # the model never removes anything on its own


@needs_text
def test_reminder_calendar_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "RUNS", tmp_path)
    c = case()
    d = tmp_path / "r1"
    d.mkdir()
    (d / "case.json").write_text(c.model_dump_json())
    (d / "report.json").write_text(checks.run(c).model_dump_json())
    res = TestClient(app_module.app).get("/cases/r1/reminder.ics")
    assert res.headers["content-type"].startswith("text/calendar")
    assert "DTSTART;VALUE=DATE:20260902" in res.text and "TRIGGER:-P7D" in res.text


# --- confidence (Я11) -------------------------------------------------------------------------

@needs_text
def test_every_shown_finding_gets_a_confidence_and_clean_ones_are_sure():
    c = case()
    r, _ = pipeline.check_and_hold(c, checks.run(c))
    assert r.findings and all(f.confidence is not None for f in r.findings + r.worth_asking)
    assert all(f.severity == "high" and f.confidence >= verifier.CONFIRMED for f in r.findings)


@needs_text
def test_a_model_objection_makes_a_finding_borderline_without_its_sum():
    c = case()
    r = checks.run(c)
    v = verifier.verify(c, r, writer.all_letters(c, r))
    names = verifier.subjects(r)
    target = next(f for f in r.findings if f.amount)
    v.model_notes = [{"subject": names[id(target)], "verdict": "problem", "reason": "line 2 is a different service"}]
    graded = verifier.grade(c, r, v, names)
    moved = next(f for f in graded.worth_asking if f.title == target.title)
    assert moved.severity == "borderline" and moved.amount is None and moved.confidence == 0.6
    assert moved.math["amount_if_confirmed"] == str(target.amount) and "second read" in moved.math["why_unsure"]
    assert graded.total_may_come_off == r.total_may_come_off - target.amount
    # must differ: the same finding confirmed by the model stays with its sum
    v.model_notes = [{"subject": names[id(target)], "verdict": "confirmed", "reason": "ok"}]
    kept = next(f for f in verifier.grade(c, r, v, names).findings if f.title == target.title)
    assert kept.amount == target.amount and kept.confidence == 1.0


@needs_text
def test_unknown_savings_lower_confidence_on_free_care():
    c = case()
    r = checks.run(c)
    v = verifier.verify(c, r, writer.all_letters(c, r))
    f = r.findings[0].model_copy(update={"kind": Kind.F1, "policy_refs": ["memorial-2026-05#liquid-assets"]})
    c.profile.liquid_assets = None
    conf, why = verifier.confidence(c, f, "F1 #9", v)
    assert conf == 0.8 and "savings" in why[0]


@needs_text
def test_second_read_runs_only_with_the_flag(monkeypatch):
    c = case()
    r = checks.run(c)
    target = next(f for f in r.findings if f.amount)
    name = verifier.subjects(r)[id(target)]

    class SecondRead:  # SYNTHETIC answer in the recorded shape
        calls = 0

        def events(self, body):
            SecondRead.calls += 1
            assert body["model"] == "checker" and body["store"] is False
            text = json.dumps({"items": [{"subject": name, "verdict": "problem", "reason": "different service"}]})
            return [{"type": "response.output_text.delta", "delta": text},
                    {"type": "response.completed", "response": {"id": "resp_SYNTHETIC", "usage": {}}}]

    client = FakeClient(SecondRead())
    monkeypatch.delenv(pipeline.SECOND_READ, raising=False)
    shown, _ = pipeline.check_and_hold(c, r, client)
    assert SecondRead.calls == 0 and any(f.title == target.title for f in shown.findings)
    monkeypatch.setenv(pipeline.SECOND_READ, "1")
    shown, verdict = pipeline.check_and_hold(c, checks.run(c), client)
    assert SecondRead.calls == 1 and verdict.model_notes
    moved = next(f for f in shown.worth_asking if f.title == target.title)
    assert moved.amount is None and "Nemotron-3-Ultra-550b-a55b" in moved.math["why_unsure"]


@needs_text
def test_a_failed_second_read_keeps_the_code_result_and_says_so(monkeypatch):
    from billradar.model_api import ModelError
    c = case()

    class Down:
        def events(self, body):
            raise ModelError("HTTP 503: SYNTHETIC outage")

    monkeypatch.setenv(pipeline.SECOND_READ, "1")
    shown, verdict = pipeline.check_and_hold(c, checks.run(c), FakeClient(Down()))
    plain, _ = pipeline.check_and_hold(c, checks.run(c))
    assert [f.title for f in shown.findings] == [f.title for f in plain.findings]
    assert any(i.subject == "model review" and i.what.startswith("Nemotron-3-Ultra-550b-a55b review did not run")
               and i.severity == "note" for i in verdict.issues)


def test_the_review_note_names_the_model_that_really_ran():
    from billradar import nemotron_client as nc
    assert verifier.checker_name(FakeClient(object())) == nc.CHECKER_MODEL.split("/")[-1] == "Nemotron-3-Ultra-550b-a55b"


def test_a_second_read_that_calls_a_present_paper_missing_is_overruled_by_code():
    """Ultra once wrote "References sheet S10 which does not exist in case documents" while S10 was in the very
    case it got: code checks that claim, the finding keeps its confidence, and the note says why."""
    import json
    from billradar import example, verifier, writer
    run = example.run_recorded(example.PACK, example.EXAMPLE_ID)
    case, report = run.case, run.report
    assert "S10" in {d.doc_id for d in case.documents}

    class Client:
        model = "nvidia/Nemotron-3-Ultra-550b-a55b"
        def respond(self, body, label=""):
            items = [{"subject": "E2 #1 on S10", "verdict": "problem",
                      "reason": "References sheet S10 which does not exist in case documents."},
                     {"subject": "F1 #1 on D1", "verdict": "problem", "reason": "D1 balance differs from the math."}]
            return type("R", (), {"text": json.dumps({"items": items}), "usage": {}, "seconds": 0.0, "replayed": True})()

    v = verifier.model_review(Client(), case, report, writer.all_letters(case, report), verifier.verify(case, report, []))
    by = {i["subject"]: i for i in v.model_notes}
    assert by["E2 #1 on S10"]["verdict"] == "overruled" and "S10 is in the case" in by["E2 #1 on S10"]["code_check"]
    assert by["F1 #1 on D1"]["verdict"] == "problem"  # must differ: a claim code cannot check stays the model's
