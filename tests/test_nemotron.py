"""Nemotron on Token Factory behind the NemotronClient interface. Every answer here is SYNTHETIC: no model was called."""

import io
import json
from datetime import date

import numpy as np
import pytest
from reportlab.pdfgen import canvas

from billradar import nemotron_client as nc
from billradar import pipeline, providers, reader, sheets_vl, verifier
from billradar import sheets as sg
from billradar.model_api import data_url
from tests.fakes import QUADS, SIZE, FakeClient, FakeTable, reading_from_fixture, table_photo
from tests.test_pipeline import needs_text, profile


def chat_answer(text: str, model: str = "nvidia/Nemotron-3.5-Super-VL", finish: str = "stop", **msg) -> dict:
    return {"id": "chatcmpl-SYNTHETIC", "model": model, "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": text, **msg}}],
            "usage": {"prompt_tokens": 9000, "completion_tokens": 1500,
                      "completion_tokens_details": {"reasoning_tokens": 400}}}


class FakeTF:
    """Answers chat bodies the way Token Factory would, routed by what is asked."""

    def __init__(self, quads=QUADS, size=SIZE, reading: dict | None = None):
        self.quads, self.size, self.reading = quads, size, reading
        self.chats: list[dict] = []

    def answer(self, chat: dict) -> nc.Answer:
        self.chats.append(chat)
        name = chat.get("response_format", {}).get("json_schema", {}).get("name")
        if name == "sheets_on_table":
            W, H = self.size
            boxes = [{"box": [int(q[:, 0].min() / W * 1000), int(q[:, 1].min() / H * 1000),
                              int(q[:, 0].max() / W * 1000 + 1), int(q[:, 1].max() / H * 1000 + 1)],
                      "confidence": 0.9} for q in reversed(self.quads)]  # any order: code sorts them
            return nc.Answer(chat_answer(json.dumps({"sheets": boxes})), "req-SYNTHETIC-1")
        if name == "billradar_case":
            return nc.Answer(chat_answer(json.dumps(self.reading)), "req-SYNTHETIC-2")
        raise AssertionError(f"unexpected request {name}")


def nclient(fake, tmp_path, **kw) -> nc.NemotronClient:
    return nc.NemotronClient(transport=fake, record_to=kw.get("record_to"), journal=tmp_path / "requests.jsonl")


def tiny_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


# --- body translation ---------------------------------------------------------------------------

def test_reader_body_becomes_a_chat_for_super_vl():
    jpeg = sg.to_jpeg(sg.load_photo(table_photo(QUADS[:1], SIZE)), 400)
    body = reader.request_body([("S1", jpeg)], [("D1", "eob.pdf", tiny_pdf("SAMPLE EOB claim CLM-1 $120.00"))])
    chat = nc.chat_body(body)

    assert chat["model"] == "nvidia/Nemotron-3.5-Super-VL"
    assert chat["messages"][0] == {"role": "system", "content": reader.INSTRUCTIONS}
    parts = chat["messages"][1]["content"]
    assert [p["type"] for p in parts].count("image_url") == 1
    assert parts[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    pdf_part = next(p["text"] for p in parts if p["type"] == "text" and p["text"].startswith("Text layer of eob.pdf"))
    assert "CLM-1 $120.00" in pdf_part and "all 1 page(s)" in pdf_part and "[page" not in pdf_part
    assert chat["response_format"]["json_schema"]["schema"] == reader.SCHEMA
    assert chat["response_format"]["json_schema"]["strict"] is True
    assert chat["reasoning_effort"] == "low" and chat["max_tokens"] == 65536  # Super VL thinks in full: bigger budget
    assert (chat["temperature"], chat["top_p"]) == (0.6, 0.95)  # NVIDIA card: vision tasks
    assert "input" not in chat and "store" not in chat


def test_checker_goes_to_the_text_checker_model_and_refuses_images():
    body = {"model": "checker", "reasoning": {"effort": "medium"},
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "check"}]}]}
    chat = nc.chat_body(body)
    assert chat["model"] == nc.CHECKER_MODEL == "nvidia/Nemotron-3-Ultra-550b-a55b"  # measured: eval_review.py
    assert chat["messages"] == [{"role": "user", "content": "check"}]
    assert (chat["temperature"], chat["top_p"]) == (1.0, 0.95)

    body["input"][0]["content"].append({"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"})
    with pytest.raises(nc.NemotronError, match="reads text only"):
        nc.chat_body(body)


def test_unknown_models_are_refused_not_mapped():
    with pytest.raises(nc.NemotronError, match="no Nemotron model"):
        nc.nemotron_model("reader-contributor")
    assert nc.nemotron_model("nvidia/Nemotron-3-Ultra-550b-a55b") == "nvidia/Nemotron-3-Ultra-550b-a55b"


def test_pdf_without_text_layer_is_an_error():
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.rect(72, 72, 100, 100)  # a drawing, no text
    c.save()
    with pytest.raises(nc.NemotronError, match="no text layer"):
        nc.pdf_text(data_url(buf.getvalue(), "application/pdf"), "scan.pdf")


# --- answer checks ------------------------------------------------------------------------------

def _review_body():
    return {"model": "checker", "input": [{"role": "user", "content": "x"}],
            "text": {"format": {"type": "json_schema", "name": "billradar_review",
                                "schema": verifier.REVIEW_SCHEMA, "strict": True}}}


def test_cut_empty_and_off_schema_answers_are_errors():
    body = _review_body()
    ok = json.dumps({"items": [{"subject": "F3 #1", "verdict": "confirmed", "reason": "matches"}]})
    assert nc.answer_text(chat_answer(ok), body) == ok
    with pytest.raises(nc.NemotronError, match="cut at max_tokens"):
        nc.answer_text(chat_answer(ok, finish="length"), body)
    with pytest.raises(nc.NemotronError, match="only reasoning"):
        nc.answer_text(chat_answer(None, reasoning="thinking..."), body)
    with pytest.raises(nc.NemotronError, match="not JSON"):
        nc.answer_text(chat_answer("Sure! Here is"), body)
    bad = json.dumps({"items": [{"subject": "F3 #1", "verdict": "fine", "reason": "x"}]})
    with pytest.raises(nc.NemotronError, match=r"not one of"):
        nc.answer_text(chat_answer(bad), body)
    with pytest.raises(nc.NemotronError, match=r"missing items"):
        nc.answer_text(chat_answer("{}"), body)


def test_schema_check_catches_types_and_nesting():
    assert nc.schema_errors({"documents": []}, reader.SCHEMA) == []
    errs = nc.schema_errors({"documents": [{"doc_id": 3}]}, reader.SCHEMA)
    assert any("doc_id: expected string" in e for e in errs) and any("missing lines" in e for e in errs)
    assert nc.schema_errors(True, {"type": "integer"}) == ["$: expected integer, got bool"]


# --- client, journal, replay --------------------------------------------------------------------

class OneAnswer:
    def __init__(self, raw, request_id="req-SYNTHETIC"):
        self.raw, self.request_id = raw, request_id

    def answer(self, chat):
        return nc.Answer(self.raw, self.request_id)


def test_journal_proves_which_nemotron_answered(tmp_path):
    ok = json.dumps({"items": []})
    c = nclient(OneAnswer(chat_answer(ok, model=nc.CHECKER_MODEL)), tmp_path)
    res = c.respond(_review_body(), label="review")
    assert res.text == ok and res.response_id == "chatcmpl-SYNTHETIC"
    assert res.usage == {"input_tokens": 9000, "output_tokens": 1500, "output_tokens_details": {"reasoning_tokens": 400}}

    entry = json.loads((tmp_path / "requests.jsonl").read_text().splitlines()[0])
    assert entry["provider"] == "nebius" and entry["endpoint"].startswith("https://api.tokenfactory.nebius.com/v1")
    assert entry["model"] == entry["answered_by"] == nc.CHECKER_MODEL
    assert entry["asked_as"] == "checker" and entry["request_id"] == "req-SYNTHETIC"
    assert entry["usd"] == pytest.approx((9000 * 1.00 + 1500 * 3.00) / 1e6)  # Ultra prices
    assert "data:" not in json.dumps(entry)


def test_refused_answer_is_still_journaled(tmp_path):
    c = nclient(OneAnswer(chat_answer("{}", finish="length")), tmp_path)
    with pytest.raises(nc.NemotronError):
        c.respond(_review_body())
    assert len((tmp_path / "requests.jsonl").read_text().splitlines()) == 1  # it was billed, so it is logged


def test_live_answer_is_recorded_and_replays(tmp_path):
    ok = json.dumps({"items": []})

    class LiveFake(nc.LiveTransport, OneAnswer):  # counts as live, answers without the network
        def __init__(self):
            OneAnswer.__init__(self, chat_answer(ok, model=nc.CHECKER_MODEL))

        answer = OneAnswer.answer

    rec = tmp_path / "rec"
    live = nclient(LiveFake(), tmp_path, record_to=rec)
    assert live.live
    live.respond(_review_body())
    assert len(list(rec.glob("Nemotron-3-Ultra-550b-a55b-*.json"))) == 1

    replay = nclient(nc.ReplayTransport(rec), tmp_path)
    assert not replay.live
    res = replay.respond(_review_body())
    assert res.text == ok and res.replayed
    with pytest.raises(nc.ReplayMissing):
        replay.respond({**_review_body(), "max_output_tokens": 99})


def test_default_is_replay_without_key_and_flag(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.setenv("BILLRADAR_LIVE", "1")
    assert not nc.NemotronClient().live
    monkeypatch.setenv("NEBIUS_API_KEY", "sk-SYNTHETIC-not-a-key")
    monkeypatch.setenv("BILLRADAR_LIVE", "0")
    assert not nc.NemotronClient().live


# --- live transport: spend cap and retries (requests.post replaced, no network) ------------------

class Resp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code, self.payload, self.headers = status, payload, headers or {}
        self.text = json.dumps(payload) if payload is not None else "error"

    def json(self):
        return self.payload


def test_spend_cap_stops_before_the_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(nc.requests, "post", lambda *a, **k: calls.append(1))
    t = nc.LiveTransport("sk-SYNTHETIC", ledger=nc.SpendLedger(tmp_path / "spend.jsonl", cap=0.001))
    chat = nc.chat_body(_review_body())
    with pytest.raises(nc.SpendCapReached):
        t.answer(chat)
    assert calls == []


def test_errors_release_the_reservation_and_success_settles_the_real_cost(tmp_path, monkeypatch):
    replies = [Resp(429, headers={"Retry-After": "0"}),
               Resp(200, chat_answer("{}", model=nc.CHECKER_MODEL), {"x-request-id": "req-1"})]
    monkeypatch.setattr(nc.requests, "post", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr(nc.time, "sleep", lambda s: None)
    ledger = nc.SpendLedger(tmp_path / "spend.jsonl", cap=5)
    ans = nc.LiveTransport("sk-SYNTHETIC", ledger=ledger).answer(nc.chat_body(_review_body()))
    assert ans.request_id == "req-1"
    assert ledger.spent() == pytest.approx((9000 * 1.00 + 1500 * 3.00) / 1e6)  # the real cost at Ultra prices


def test_non_retryable_error_is_raised_with_its_body(tmp_path, monkeypatch):
    monkeypatch.setattr(nc.requests, "post", lambda *a, **k: Resp(402, {"error": "credit_balance_exhausted"}))
    ledger = nc.SpendLedger(tmp_path / "spend.jsonl", cap=5)
    with pytest.raises(nc.NemotronError, match="HTTP 402.*credit_balance_exhausted"):
        nc.LiveTransport("sk-SYNTHETIC", ledger=ledger).answer(nc.chat_body(_review_body()))
    assert ledger.spent() == pytest.approx(0)


# --- sheets: Super VL boxes + local corners ------------------------------------------------------

def test_super_vl_boxes_become_straightened_sheets(tmp_path):
    fake = FakeTF()
    res = sheets_vl.find_sheets(nclient(fake, tmp_path), table_photo(QUADS, SIZE))

    assert [s.doc_id for s in res.sheets] == ["S1", "S2", "S3", "S4", "S5"]
    for s, q in zip(res.sheets, QUADS):
        assert s.straightened, s.doc_id
        got = np.array(s.corners, dtype=float)
        want = sg._order_corners(q.astype(np.float32))
        assert np.abs(got - want).max() < 30, (s.doc_id, got, want)  # the paper's own corners, not the box
        w, h = s.image.size
        assert 1.15 < h / w < 1.45  # letter paper, straightened
    sent = fake.chats[0]
    assert sent["model"] == "nvidia/Nemotron-3.5-Super-VL" and sent["reasoning_effort"] == "none"  # boxes: no thinking


def test_bad_boxes_are_dropped_with_a_note():
    text = json.dumps({"sheets": [{"box": [10, 10], "confidence": 1}, {"box": [500, 500, 400, 600], "confidence": 1},
                                  {"box": [-50, 0, 2000, 1000], "confidence": 0.5}]})
    dets, notes = sheets_vl.parse(text, (2000, 1000))
    assert len(dets) == 1 and dets[0].box == (0.0, 0.0, 2000.0, 1000.0)  # clamped to the image
    assert len(notes) == 2


# --- the switch and the whole pipeline -----------------------------------------------------------

def test_the_client_is_nemotron_on_nebius():
    c = providers.make_client()
    assert isinstance(c, nc.NemotronClient) and providers.name() == "nebius"
    assert providers.sheet_finder(c) is sheets_vl.find_sheets
    assert providers.models(c)["read"] == "nvidia/Nemotron-3.5-Super-VL"


@needs_text
def test_photo_to_findings_on_nemotron(tmp_path):
    fake = FakeTF(reading=reading_from_fixture({"S6": "S5"}))
    c = nclient(fake, tmp_path)
    out = pipeline.run(c, [table_photo(QUADS, SIZE)], [], profile(), "memorial-2026-05",
                       "Lakeview Demo Hospital", date(2026, 6, 15), "anna-birth")

    assert [s.doc_id for s in out.sheets] == ["S1", "S2", "S3", "S4", "S5"]
    assert not [p for p in out.problems if p.startswith("reader")]
    assert out.report.total_may_come_off is not None and out.report.findings
    models = [json.loads(line)["model"] for line in (tmp_path / "requests.jsonl").read_text().splitlines()]
    assert models == ["nvidia/Nemotron-3.5-Super-VL", "nvidia/Nemotron-3.5-Super-VL"]
    read_chat = fake.chats[1]
    assert sum(p["type"] == "image_url" for p in read_chat["messages"][1]["content"]) == 5



def test_more_than_eight_sheets_are_read_in_groups(tmp_path):
    jpeg = sg.to_jpeg(sg.load_photo(table_photo(QUADS[:1], SIZE)), 200)
    sheets = [(f"S{i}", jpeg) for i in range(1, 11)]
    files = [("D1", "eob.pdf", tiny_pdf("SAMPLE EOB"))]
    groups = reader.batches(sheets, files, 8)
    assert [len(g) for g, _ in groups] == [8, 2] and groups[0][1] == [] and groups[1][1] == files
    assert reader.batches(sheets, files, None) == [(sheets, files)]

    class Echo:
        def __init__(self):
            self.chats = []

        def answer(self, chat):
            self.chats.append(chat)
            ids = chat["messages"][1]["content"][0]["text"].split(": ", 1)[1].split(".")[0].split(", ")
            docs = [{"doc_id": d, "kind": "other", "lines": []} for d in ids]
            return nc.Answer(chat_answer(json.dumps({"documents": docs})))

    class Loose(nc.NemotronClient):  # the reading schema needs every key; this test only counts documents
        def respond(self, body, label=""):
            body = {**body, "text": {}}
            return super().respond(body, label)

    fake = Echo()
    res = reader.read_case(Loose(transport=fake, record_to=None, journal=None), sheets, files)
    assert [d.doc_id for d in res.documents] == [f"S{i}" for i in range(1, 11)] + ["D1"]
    assert [sum(p["type"] == "image_url" for p in c["messages"][1]["content"]) for c in fake.chats] == [8, 2]
    assert res.usage["input_tokens"] == 18000


def test_too_many_images_are_refused_before_sending(tmp_path):
    jpeg = sg.to_jpeg(sg.load_photo(table_photo(QUADS[:1], SIZE)), 200)
    body = reader.request_body([(f"S{i}", jpeg) for i in range(1, 10)], [])
    c = nclient(OneAnswer(chat_answer("{}")), tmp_path)
    with pytest.raises(nc.NemotronError, match="at most 8"):
        c.respond(body)


def test_instructions_become_the_system_message_and_text_reading_goes_to_lightning():
    body = {"model": "reader", "instructions": "Translate.", "reasoning": {"effort": "low"},
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "Hola"}]}]}
    chat = nc.chat_body(body)
    assert chat["messages"][0] == {"role": "system", "content": "Translate."}
    assert chat["model"] == "nvidia/Nemotron-3_5-Lightning" and chat["reasoning_effort"] == "none"  # Lightning: only none is light


def test_tools_and_unknown_fields_are_refused_not_dropped(tmp_path):
    with pytest.raises(nc.NotOnNemotron, match="computer"):
        nc.chat_body({"model": "checker", "tools": [{"type": "computer"}], "input": []})
    with pytest.raises(nc.NemotronError, match="previous_response_id"):
        nc.chat_body({"model": "checker", "previous_response_id": "resp_1", "input": []})
    with pytest.raises(nc.NotOnNemotron, match="type the note"):
        nclient(OneAnswer({}), tmp_path).transcribe({}, b"RIFF", label="voice:note")


class FakeTFWithReview(FakeTF):
    def __init__(self, review: dict | None, **kw):
        super().__init__(**kw)
        self.review = review

    def answer(self, chat):
        if chat.get("response_format", {}).get("json_schema", {}).get("name") == "billradar_review":
            self.chats.append(chat)
            if self.review is None:
                raise nc.NemotronError("HTTP 503: SYNTHETIC outage")
            return nc.Answer(chat_answer(json.dumps(self.review), model="nvidia/nemotron-3-super-120b-a12b"))
        return super().answer(chat)


def _run_with_review(tmp_path, monkeypatch, flag: str | None, review):
    if flag is None:
        monkeypatch.delenv("BILLRADAR_SECOND_READ", raising=False)
    else:
        monkeypatch.setenv("BILLRADAR_SECOND_READ", flag)
    fake = FakeTFWithReview(review, reading=reading_from_fixture({"S6": "S5"}))
    out = pipeline.run(nclient(fake, tmp_path), [table_photo(QUADS, SIZE)], [], profile(), "memorial-2026-05",
                       "Lakeview Demo Hospital", date(2026, 6, 15), "anna-birth")
    names = [c.get("response_format", {}).get("json_schema", {}).get("name") for c in fake.chats]
    return out, names


@needs_text
def test_model_review_is_off_by_default(tmp_path, monkeypatch):
    out, names = _run_with_review(tmp_path, monkeypatch, None, {"items": []})
    assert "billradar_review" not in names and out.verdict.model_notes == []


@needs_text
def test_model_review_notes_a_problem_but_code_decides_what_is_shown(tmp_path, monkeypatch):
    base, _ = _run_with_review(tmp_path, monkeypatch, None, None)
    review = {"items": [{"subject": "F3 #1", "verdict": "problem", "reason": "SYNTHETIC: the discount line is on another sheet"}]}
    out, names = _run_with_review(tmp_path, monkeypatch, "1", review)
    assert names[-1] == "billradar_review"
    assert out.verdict.model_notes == review["items"]
    notes = [i for i in out.verdict.issues if "SYNTHETIC" in i.what]
    assert notes and all(i.severity == "note" for i in notes)
    held = lambda r: sorted(f.kind.value for f in r.held_back)  # noqa: E731
    assert held(out.report) == held(base.report)  # holding back stays with code; the model only lowers confidence

# A failed second read: pack B's verifier keeps the code-checked result and adds a note (its own test, a64a27d).


def test_text_steps_go_to_lightning_ask_to_super_and_review_to_ultra_by_default():
    text = {"model": "reader", "input": [{"role": "user", "content": "Explain code A3003."}]}
    assert nc.chat_body(text)["model"] == "nvidia/Nemotron-3_5-Lightning"
    review = {"model": "checker", "input": [{"role": "user", "content": "check"}]}
    assert nc.chat_body(review)["model"] == "nvidia/Nemotron-3-Ultra-550b-a55b"
    ask_body = {"model": "reader", "input": [{"role": "user", "content": "When is the deadline?"}]}
    assert nc.chat_body(ask_body, "ask")["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert providers.models(nc.NemotronClient(transport=OneAnswer({}), journal=None))["text"] == nc.TEXT_MODEL


def test_receipt_counts_each_step_and_prices_the_biggest_model(tmp_path):
    from billradar import receipt
    calls = [
        {"label": "read", "model": "nvidia/Nemotron-3.5-Super-VL", "answered_by": "nvidia/Nemotron-3.5-Super-VL",
         "seconds": 60.0, "live": True, "request_id": "r1",
         "usage": {"input_tokens": 20000, "output_tokens": 10000, "output_tokens_details": {"reasoning_tokens": 6000}}},
        {"label": "review", "model": "nvidia/nemotron-3-super-120b-a12b", "answered_by": "", "seconds": 5.0,
         "live": True, "request_id": "r2", "usage": {"input_tokens": 10000, "output_tokens": 2000}},
    ]
    r = receipt.build(calls)
    vl = (20000 * 0.30 + 10000 * 0.90) / 1e6
    sup = (10000 * 0.30 + 2000 * 0.90) / 1e6
    ultra = (10000 * 1.00 + 2000 * 3.00) / 1e6
    assert [x["step"] for x in r["rows"]] == ["Read every sheet", "Second read of the case"]
    assert r["total_usd"] == pytest.approx(vl + sup) and r["total_seconds"] == 65.0
    assert r["all_on_biggest_usd"] == pytest.approx(vl + ultra)  # photos stay on Super VL: no larger vision model
    assert r["rows"][1]["model"] == "nvidia/nemotron-3-super-120b-a12b" and not r["replayed"]
    assert receipt.build([]) is None


@needs_text
def test_pipeline_writes_the_receipt_for_its_own_calls_only(tmp_path, monkeypatch):
    monkeypatch.delenv("BILLRADAR_SECOND_READ", raising=False)
    fake = FakeTF(reading=reading_from_fixture({"S6": "S5"}))
    c = nclient(fake, tmp_path)
    c.calls.append({"label": "earlier case", "model": "nvidia/Nemotron-3_5-Lightning", "usage": {}})
    out = pipeline.run(c, [table_photo(QUADS, SIZE)], [], profile(), "memorial-2026-05",
                       "Lakeview Demo Hospital", date(2026, 6, 15), "anna-birth")
    assert [r["step"] for r in out.receipt["rows"]] == ["Find sheets", "Read every sheet"]
    pipeline.save(out, tmp_path / "run")
    assert json.loads((tmp_path / "run" / "receipt.json").read_text())["rows"][0]["model"] == "nvidia/Nemotron-3.5-Super-VL"
    plain = FakeClient(FakeTable(QUADS, SIZE, reading_from_fixture({"S6": "S5"})))  # a client without a call list
    plain_out = pipeline.run(plain, [table_photo(QUADS, SIZE)], [], profile(), "memorial-2026-05",
                             "Lakeview Demo Hospital", date(2026, 6, 15), "anna-birth")
    assert plain_out.receipt is None


def test_function_calling_round_trip_for_the_family_agent(tmp_path):
    tools = [{"type": "function", "name": "analyze_case", "description": "Run BillRadar",
              "parameters": {"type": "object", "properties": {"fictional": {"type": "boolean"}}}}]
    history = [{"role": "user", "content": [{"type": "input_text", "text": "Check our bills."}]},
               {"type": "function_call", "call_id": "call_1", "name": "analyze_case", "arguments": '{"fictional": true}'},
               {"type": "function_call_output", "call_id": "call_1", "output": '{"case_id": "c1"}'}]
    body = {"model": "reader", "instructions": "Act for the family.", "tools": tools,
            "reasoning": {"effort": "low"}, "input": history}
    chat = nc.chat_body(body, "family-agent")
    assert chat["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert chat["tools"][0]["function"]["name"] == "analyze_case" and chat["tool_choice"] == "auto"
    assert chat["messages"][2] == {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "analyze_case", "arguments": '{"fictional": true}'}}]}
    assert chat["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"case_id": "c1"}'}

    raw = chat_answer(None, model="nvidia/nemotron-3-super-120b-a12b", finish="tool_calls", tool_calls=[
        {"id": "call_2", "type": "function", "function": {"name": "list_findings", "arguments": '{"case_id": "c1"}'}}])
    res = nclient(OneAnswer(raw), tmp_path).respond(body, label="family-agent")
    assert res.text == ""
    out = res.events[-1]["response"]["output"]
    assert out == [{"type": "function_call", "call_id": "call_2", "name": "list_findings", "arguments": '{"case_id": "c1"}'}]
    with pytest.raises(nc.NemotronError, match="empty answer"):  # no tools asked, no text: still an error
        nc.answer_text(chat_answer(None, tool_calls=[{"id": "x"}]), {"model": "reader", "input": []})
