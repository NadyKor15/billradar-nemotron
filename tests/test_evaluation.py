import json
from datetime import date
from decimal import Decimal

import pytest

from billradar import checks
from billradar import evaluation as ev
from billradar import nemotron_client as nc
from billradar.policies import source
from tests.test_writer import case
from tests.fakes import FakeClient

needs_text = pytest.mark.skipif(not all(source.is_cached(p) for p in source.SOURCES),
                                reason="run scripts/fetch_policies.py first")

KEY = ev.Key(pack_id="anna-birth", split="work", items=[
    ev.KeyItem(cls="E1", sheets=["S1", "S4"], amount=Decimal("46.86")),
    ev.KeyItem(cls="F3", sheets=["S1", "S2"], amount=Decimal("1054.00")),
    ev.KeyItem(cls="F2", sheets=["S3"], deadline=date(2026, 9, 2)),
])


@needs_text
def test_billradar_on_its_own_case_finds_everything():
    s = ev.score(KEY, ev.from_report(checks.run(case())))
    assert {c: (x.found, x.n, x.false, x.wrong_amount, x.wrong_deadline) for c, x in s.by_class.items()} == {
        "E1": (1, 1, 0, 0, 0), "F3": (1, 1, 0, 0, 0), "F2": (1, 1, 0, 0, 0)}


def test_scoring_counts_misses_falses_and_wrong_numbers():
    preds = [ev.Prediction(cls="F3", sheets=["S2"], amount=Decimal("900.00")),
             ev.Prediction(cls="F2", sheets=[], deadline=date(2027, 1, 14)),
             ev.Prediction(cls="E2", sheets=["S1"], amount=Decimal("10.00"))]
    s = ev.score(KEY, preds)
    assert (s.by_class["E1"].found, s.by_class["E1"].n) == (0, 1)
    assert s.by_class["F3"].wrong_amount == 1 and s.by_class["F2"].wrong_deadline == 1
    assert s.by_class["E2"].false == 1
    t = ev.table({"X": s})
    assert "| E1 | 0 of 1 |" in t and "1 wrong $" in t and "| E2 | 0 of 0 (1 false) |" in t


def test_a_prediction_matches_only_one_key_item():
    key = ev.Key(pack_id="p", split="work", items=[ev.KeyItem(cls="E2", sheets=["S1"]), ev.KeyItem(cls="E2", sheets=["S1"])])
    s = ev.score(key, [ev.Prediction(cls="E2", sheets=["S1"])])
    assert (s.by_class["E2"].found, s.by_class["E2"].n) == (1, 2)


def test_seal_refuses_a_changed_key(tmp_path):
    k = tmp_path / "pack1" / "key.json"
    k.parent.mkdir()
    k.write_text(KEY.model_dump_json())
    ledger = tmp_path / "SEALED.txt"
    ev.seal([k], ledger)
    assert ev.check_seal(k, ledger)
    k.write_text(json.dumps(json.loads(k.read_text()), indent=4))  # formatting does not matter
    assert ev.check_seal(k, ledger)
    data = json.loads(k.read_text())
    data["items"][0]["amount"] = "50.00"
    k.write_text(json.dumps(data))
    assert not ev.check_seal(k, ledger)


@needs_text
def test_bodies_are_valid_requests():
    sheets = [("S1", b"\xff\xd8fake"), ("S2", b"\xff\xd8fake")]
    simple = ev.simple_body(sheets, [])
    strong = ev.strong_body(sheets, [], "memorial-2026-05", {"size": 4})
    judge = ev.judge_body("You may qualify for a discount.", KEY)
    for b in (simple, strong, judge):
        assert b["store"] is False and nc.chat_body(b)["model"]  # Token Factory takes each of them as it is
    assert "text" not in simple  # the simple path answers freely
    assert simple["input"][0]["content"][0]["text"] == ev.SIMPLE_QUESTION
    assert strong["input"][0]["role"] == "developer" and strong["text"]["format"]["strict"] is True
    files = [c for c in strong["input"][1]["content"] if c["type"] == "input_file"]
    assert files and files[0]["filename"] == "memorial-2026-05.pdf"


def test_judge_and_strong_answers_parse():
    preds, extra = ev.from_judge(json.dumps({"items": [{"index": 1, "found": True, "amount": "$1,054.00", "deadline": ""},
                                                       {"index": 0, "found": False, "amount": "", "deadline": ""}],
                                             "other_money_claims": 2}), KEY)
    assert [p.cls for p in preds] == ["F3"] and preds[0].amount == Decimal("1054.00") and extra == 2
    strong = ev.from_strong(json.dumps({"findings": [{"cls": "F2", "sheets": ["S3"], "amount": "", "deadline": "2026-09-02",
                                                      "explanation": "..."}]}))
    assert strong[0].deadline == date(2026, 9, 2)


@needs_text
def test_run_pack_end_to_end_on_synthetic_answers(tmp_path):
    import importlib.util
    from pathlib import Path

    from tests.fakes import QUADS, SIZE, FakeTable, reading_from_fixture, table_photo

    spec = importlib.util.spec_from_file_location("eval_run", Path(__file__).parents[1] / "scripts" / "eval_run.py")
    eval_run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eval_run)

    pack = tmp_path / "anna"
    pack.mkdir()
    (pack / "table.jpg").write_bytes(table_photo(QUADS, SIZE))
    fixture = json.loads((Path(__file__).parent / "fixtures/cases/anna-birth.json").read_text())
    (pack / "profile.json").write_text(json.dumps({"policy_id": "memorial-2026-05", "today": "2026-06-15",
                                                   "fictional": True, "profile": fixture["profile"]}))
    (pack / "key.json").write_text(KEY.model_dump_json())

    answers = {
        "billradar_case": json.dumps(reading_from_fixture({"S6": "S5"})),
        "judge": json.dumps({"items": [{"index": 1, "found": True, "amount": "", "deadline": ""}], "other_money_claims": 1}),
        "strong_baseline": json.dumps({"findings": [{"cls": "F3", "sheets": ["S2"], "amount": "1054.00", "deadline": "",
                                                     "explanation": ""}]}),
        None: "You may be able to ask the hospital for help with the baby's bill.",
    }
    table = FakeTable(QUADS, SIZE)

    class Fake:
        def events(self, body):
            name = (body.get("text") or {}).get("format", {}).get("name")
            if name == "sheets_on_table":
                return table.events(body)
            name = (body.get("text") or {}).get("format", {}).get("name")
            return [{"type": "response.output_text.delta", "delta": answers[name]},
                    {"type": "response.completed", "response": {"id": "r", "usage": {}}}]

    scores = eval_run.run_pack(FakeClient(Fake()), pack, tmp_path / "out")
    t = ev.table(scores)
    assert "| F3 | 1 of 1 (1 no $) | 1 of 1 | 1 of 1 |" in t and "| E1 | 0 of 1 | 0 of 1 | 1 of 1 |" in t
    assert "| other money claims not in the key | 1 | 0 | 0 |" in t
    assert scores["BillRadar"].by_class["E1"].found == 1 and scores["BillRadar"].by_class["F2"].found == 1
    assert scores["Simple prompt"].by_class["F3"].found == 1 and scores["Simple prompt"].extra_false == 1
    assert scores["Strong prompt"].by_class["E1"].found == 0
    assert (tmp_path / "out" / "simple.txt").read_text().startswith("You may")
