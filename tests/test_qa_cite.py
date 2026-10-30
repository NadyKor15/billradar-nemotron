"""Questions with quotes picked by code. Model answers here are SYNTHETIC: written by hand in the recorded shape."""

import json

from billradar import ask, checks, qa_cite
from tests.fakes import FakeClient
from tests.test_ask import Fake
from tests.test_checks import load, needs_text

pytestmark = needs_text


def run(reply, question="Why does sheet 2 ask so much?"):
    case = load("anna-birth")
    report = checks.run(case)
    fake = Fake(reply)
    out = qa_cite.answer(FakeClient(fake), case, report, question)
    return out, fake, qa_cite.candidates(case, report)


def test_every_candidate_passes_the_same_checker_as_a_typed_quote():
    case = load("anna-birth")
    cands = qa_cite.candidates(case, checks.run(case))
    assert cands and all(ask.check_citation(case, c.citation()) for c in cands)
    assert any(c.source == "sheet" and c.ref == "S2" for c in cands)
    assert any(c.source == "policy" for c in cands)
    assert len({c.id for c in cands}) == len(cands)


def test_a_picked_id_becomes_the_code_s_own_quote():
    case = load("anna-birth")
    cands = qa_cite.candidates(case, checks.run(case))
    s2 = next(c for c in cands if c.source == "sheet" and c.ref == "S2")
    pol = next(c for c in cands if c.source == "policy")
    out, fake, _ = run({"answer": "Sheet 2 asks this much.", "refused": False, "cite": [s2.id, pol.id, s2.id]})
    assert not out.refused and [c.quote for c in out.citations] == [s2.quote, pol.quote]
    assert out.citations[1].url.startswith("https://")
    body = fake.bodies[0]
    assert body["store"] is False and body["text"]["format"]["name"] == "billradar_answer_cited"
    sent = json.loads(body["input"][1]["content"][0]["text"])
    assert {x["id"] for x in sent["candidates"]} == {c.id for c in cands}
    assert "PATIENT_1" in json.dumps(sent)  # tokens only, as in ask


def test_unknown_ids_are_dropped_and_an_answer_with_none_left_is_not_shown():
    out, _, _ = run({"answer": "The hospital must forgive the whole bill.", "refused": False, "cite": ["C99999", "S2"]})
    assert out.refused and out.reason == "no_citation" and len(out.dropped) == 2
    # must differ: one real id among unknown ones keeps the answer
    out, _, cands = run({"answer": "x", "refused": False, "cite": ["C99999", "C1"]})
    assert not out.refused and len(out.citations) == 1 and out.citations[0].quote == cands[0].quote


def test_health_and_medical_sounding_answers_are_not_shown():
    out, fake, _ = run({"answer": "x", "refused": False, "cite": ["C1"]}, "Should I take ibuprofen for the pain?")
    assert out.refused and out.reason == "health" and fake.bodies == []
    out, _, _ = run({"answer": "You should take a lower dose.", "refused": False, "cite": ["C1"]})
    assert out.refused and out.reason == "sounds_medical"


def test_each_finding_carries_the_ids_of_what_the_code_used():
    case = load("anna-birth")
    report = checks.run(case)
    cands = qa_cite.candidates(case, report)
    by_id = {c.id: c for c in cands}
    body = qa_cite.request_body(case, report, "How much could assistance take off?", cands)
    sent = json.loads(body["input"][1]["content"][0]["text"])
    assert sent["findings"] and all("evidence_ids" in f for f in sent["findings"])
    for item, f in zip(sent["findings"], report.findings):
        allowed = {e.doc_id for e in f.evidence} | set(f.policy_refs) | set(f.laws)
        assert all(by_id[i].ref in allowed for i in item["evidence_ids"])
    assert any(f["evidence_ids"] for f in sent["findings"])  # must light: at least one finding has its sources


def test_ask_answer_picks_quotes_by_default_and_types_them_with_the_flag_off(monkeypatch):
    monkeypatch.setattr(qa_cite, "ENABLED", True)  # the default; the test also runs under BILLRADAR_QA_CITE=0
    case = load("anna-birth")
    report = checks.run(case)
    fake = Fake({"answer": "x", "refused": False, "cite": ["C1"]})
    out = ask.answer(FakeClient(fake), case, report, "What do I owe?")
    assert qa_cite.ENABLED and not out.refused
    assert fake.bodies[0]["text"]["format"]["name"] == "billradar_answer_cited"
    monkeypatch.setattr(qa_cite, "ENABLED", False)  # must differ: the old path sends the old schema
    fake = Fake({"answer": "x", "refused": False, "citations": [{"source": "sheet", "ref": "S2", "quote": "1,240.00"}]})
    out = ask.answer(FakeClient(fake), case, report, "What do I owe?")
    assert not out.refused and fake.bodies[0]["text"]["format"]["name"] == "billradar_answer"
