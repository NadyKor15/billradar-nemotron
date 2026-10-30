"""Questions about the case. Model answers here are SYNTHETIC: written by hand in the recorded shape."""

import json

from billradar import ask, checks
from billradar.nemotron_client import NemotronClient
from billradar.nemotron_client import ReplayTransport
from tests.test_checks import load, needs_text
from tests.fakes import FakeClient

pytestmark = needs_text


class Fake:
    def __init__(self, reply):
        self.reply, self.bodies = reply, []

    def events(self, body):
        self.bodies.append(body)
        return [{"type": "response.output_text.delta", "delta": json.dumps(self.reply)},
                {"type": "response.completed", "response": {"id": "resp_SYNTHETIC", "usage": {}}}]


def run(reply, question="Why does sheet 2 ask so much?"):
    case = load("anna-birth")
    fake = Fake(reply)
    out = ask.answer(FakeClient(fake), case, checks.run(case), question)
    return out, fake


def test_a_cited_answer_is_shown_with_its_sources():
    out, fake = run({"answer": "Sheet 2 has no assistance discount, while the policy gives a discount at your income.",
                     "refused": False,
                     "citations": [{"source": "sheet", "ref": "S2", "quote": "$1,240.00"},
                                   {"source": "policy", "ref": "federal-501r#application-period",
                                    "quote": "the 240th day after the date that the first post-discharge billing statement"}]})
    assert not out.refused and len(out.citations) == 2 and out.dropped == []
    assert out.citations[1].url.startswith("https://")
    body = json.dumps(fake.bodies[0])
    assert fake.bodies[0]["store"] is False and "Why does sheet 2" in body
    assert "PATIENT_1" in body  # tokens only; the profile holds no names in this fixture


def test_a_made_up_quote_is_dropped_and_an_answer_without_sources_is_not_shown():
    out, _ = run({"answer": "The hospital must forgive the whole bill.", "refused": False,
                  "citations": [{"source": "policy", "ref": "federal-501r#application-period",
                                 "quote": "the hospital must forgive every bill"},
                                {"source": "sheet", "ref": "S2", "quote": "$9,999.99"},
                                {"source": "sheet", "ref": "S77", "quote": "1,240.00"}]})
    assert out.refused and out.reason == "no_citation" and len(out.dropped) == 3
    assert "could not find" in out.text


def test_health_questions_are_declined_before_any_model_call():
    out, fake = run({"answer": "x", "refused": False, "citations": []}, "Should I take ibuprofen for the pain?")
    assert out.refused and out.reason == "health" and fake.bodies == []
    # must differ: a billing question that mentions medicine still goes to the model
    out, fake = run({"answer": "x", "refused": False, "citations": [{"source": "sheet", "ref": "S2", "quote": "1,240.00"}]},
                    "Why is the pharmacy charge on my bill so high?")
    assert fake.bodies and not out.refused


def test_the_model_can_decline_and_medical_sounding_answers_are_not_shown():
    out, _ = run({"answer": "", "refused": True, "citations": []})
    assert out.refused and out.reason == "health"
    out, _ = run({"answer": "You should take it easy; this diagnosis is common.", "refused": False,
                  "citations": [{"source": "sheet", "ref": "S2", "quote": "1,240.00"}]})
    assert out.refused and out.reason == "sounds_medical"


def test_no_recording_means_no_answer_not_a_guess(tmp_path):
    case = load("anna-birth")
    client = NemotronClient(transport=ReplayTransport(tmp_path), record_to=None, journal=None)
    out = ask.answer(client, case, checks.run(case), "What do I owe?")
    assert out.refused and out.reason == "no_model"
