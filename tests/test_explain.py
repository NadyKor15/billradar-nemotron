"""Plain words for lines and codes. Codes here are generic or made up; none come from anyone's real bills."""

import json
from datetime import date
from decimal import Decimal

from billradar import checks, explain
from billradar.case import Line
from billradar.nemotron_client import NemotronClient
from tests.test_checks import load
from tests.fakes import FakeClient


def test_code_families():
    assert explain.code("0452").plain == "Hospital charge for the emergency room."
    assert explain.code("0259").family == "revenue" and "pharmacy" in explain.code("0259").plain
    assert "maternity" in explain.code("59001").plain
    assert "anesthesia" in explain.code("00100").plain
    assert "lab" in explain.code("85610").plain
    assert explain.code("J1234").plain == "Item code for a drug given by injection or infusion."
    assert "your share" in explain.code("PR-2").plain and "coinsurance" in explain.code("PR-2").plain
    assert "writes this off" in explain.code("CO45").plain


def test_diagnosis_codes_are_named_not_explained():
    e = explain.code("W99.ZZ")
    assert e.family == "diagnosis" and "does not explain diagnoses" in e.plain


def test_unknown_codes_are_left_for_the_model():
    assert explain.code("ZZ101") is None and explain.code("FS1") is None
    lines = [Line(line_no=1, code="ZZ101"), Line(line_no=2, code="0452"), Line(line_no=3, code="zz101")]
    assert explain.unknown_codes(lines) == ["ZZ101"]


def test_a_line_says_where_the_money_went():
    ln = Line(line_no=1, service_date=date(2026, 1, 2), code="0309", charges=Decimal("88.00"),
              insurance_paid=Decimal("9.87"), adjustment=Decimal("-77.03"), patient_responsibility=Decimal("1.10"),
              denial_codes="ZQ7")
    text = explain.line(ln)
    assert text.startswith("Hospital charge for laboratory tests.")
    assert "Charged $88.00." in text and "Insurance paid $9.87." in text and "$77.03 was taken off" in text
    assert "Your share: $1.10." in text and "Remark code ZQ7" in text
    assert "Your share: nothing." in explain.line(Line(line_no=2, code="ZZ101", description="widget",
                                                       patient_responsibility=Decimal(0)))


class Replay:
    """Answers like a recorded text-model response. SYNTHETIC: written by hand, not from the model."""

    def __init__(self, items, replayed=True):
        self.items = items

    def events(self, body):
        self.body = body
        return [{"type": "response.output_text.delta", "delta": json.dumps({"codes": self.items})},
                {"type": "response.completed", "response": {"id": "resp_SYNTHETIC", "usage": {}}}]


def test_model_answers_are_shown_only_when_they_stay_on_the_bill():
    t = Replay([{"code": "ZZ101", "plain": "A facility fee for the use of a room."},
                {"code": "ZZ102", "plain": "A diagnosis of a chronic disease."},
                {"code": "ZZ103", "plain": None},
                {"code": "ZZ999", "plain": "Not asked for."}])
    out = explain.with_model(FakeClient(t),
                             [("ZZ101", "FACILITY"), ("ZZ102", ""), ("ZZ103", "")])
    assert list(out) == ["ZZ101"] and out["ZZ101"].source == "Nemotron 3.5 Lightning (replayed)"
    assert t.body["store"] is False and t.body["model"] == "reader"


def test_no_recording_means_no_explanation_not_a_guess(tmp_path):
    from billradar.nemotron_client import ReplayTransport
    client = NemotronClient(transport=ReplayTransport(tmp_path), record_to=None, journal=None)
    assert explain.with_model(client, [("ZZ101", "")]) == {}


def test_every_finding_has_plain_words_and_none_sound_medical():
    report = checks.run(load("anna-birth"))
    assert all(f.explain for f in report.findings + report.worth_asking)
    for text in explain.FINDING.values():
        assert not explain.MEDICAL.search(text)
    assert set(explain.FINDING) == {k.value for k in checks.Kind if k != checks.Kind.NM}
