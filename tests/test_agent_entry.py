"""The entry for AI agents (Я23): agent_api, the MCP server (spec 2026-07-28, mcp==2.2.0) and the A2A agent
(protocol 1.0, a2a-sdk==1.1.2). Everything runs in this process: MCP in memory and over real Streamable HTTP
through an ASGI transport, A2A JSON-RPC the same way. The cases are the fictional sample packs."""

import asyncio
import base64
from decimal import Decimal

import httpx
import httpx2
import pytest
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

from billradar import a2a_agent, agent_api, agent_entry, mcp_server

KEY = "k" * 32
BASE = "https://billradar.example"
FORBIDDEN = ("send", "submit", "pay", "file_", "email", "mail", "post_")


@pytest.fixture(autouse=True)
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_api, "RUNS", tmp_path / "agents")
    monkeypatch.setenv("BILLRADAR_AGENT_KEYS", KEY)
    monkeypatch.delenv("BILLRADAR_AGENT_LIVE", raising=False)


def run(coro):
    return asyncio.run(coro)


# --- the plain functions -----------------------------------------------------------------------------

def test_sample_pack_case_end_to_end():
    packs = {p["name"] for p in agent_api.list_sample_packs()}
    assert {"brs1-birth-all-errors", "brs1-birth-clean-control"} <= packs
    r = agent_api.analyze_case(sample_pack="brs1-birth-all-errors", fictional=True)
    assert r["replayed"] is True and r["total_may_come_off"] == "1636.53"
    f = agent_api.get_findings(r["case_id"])
    amounts = {x["id"]: x["amount"] for x in f["findings"]}
    assert amounts["F1 #1"] == "1399.71" and amounts["E2 #1"] == "196.82" and amounts["E3 #1"] == "40.00"
    f1 = next(x for x in f["findings"] if x["id"] == "F1 #1")
    assert f1["sources"] and all(s["quote"] for s in f1["sources"])
    assert sum(Decimal(x["amount"]) for x in f["findings"] if x["amount"]) == Decimal(r["total_may_come_off"])
    letters = agent_api.list_letters(r["case_id"])["letters"]
    d = agent_api.draft_letter(r["case_id"], letters[0]["kind"])
    assert d["text"].startswith("SAMPLE - FICTIONAL DATA") and "never sends" in d["note"]
    assert agent_api.get_call_sheet(r["case_id"])["objections"]
    assert agent_api.get_deadlines(r["case_id"])["deadlines"]
    assert agent_api.get_protections(r["case_id"])["cards"]
    assert base64.b64decode(agent_api.get_report_pdf(r["case_id"], "ru")["base64"]).startswith(b"%PDF")


def test_clean_control_pack_has_nothing_to_find():
    r = agent_api.analyze_case(sample_pack="brs1-birth-clean-control", fictional=True)
    assert r["findings"] == 0 and r["total_may_come_off"] == "0.00"


@pytest.mark.parametrize("call,match", [
    (lambda: agent_api.analyze_case(sample_pack="brs1-birth-all-errors"), "only fictional"),
    (lambda: agent_api.analyze_case(sample_pack="../../etc", fictional=True), "unknown sample pack"),
    (lambda: agent_api.analyze_case(fictional=True), "send sample_pack"),
    (lambda: agent_api.analyze_case(photos=["eA=="], fictional=True), "API key"),
    (lambda: agent_api.get_findings("../x"), "unknown case_id"),
    (lambda: agent_api.get_findings("a000000000000"), "unknown case_id"),
])
def test_refusals(call, match):
    with pytest.raises(agent_api.AgentError, match=match):
        call()


def test_own_papers_with_a_key_still_need_the_live_switch():
    agent_api.authorized.set(True)
    try:
        with pytest.raises(agent_api.AgentError, match="live model run"):
            agent_api.analyze_case(photos=["eA=="], fictional=True)
    finally:
        agent_api.authorized.set(False)


def test_translation_without_a_live_model_says_so_and_keeps_english():
    r = agent_api.analyze_case(sample_pack="brs1-birth-all-errors", fictional=True)
    d = agent_api.draft_letter(r["case_id"], "itemized", "es")
    assert d["text"] and d["translation"]["text"] is None and "live model" in d["translation"]["why"]
    with pytest.raises(ValueError):
        agent_api.draft_letter(r["case_id"], "itemized", "de")


# --- MCP ---------------------------------------------------------------------------------------------

def test_mcp_tools_in_memory():
    async def go():
        async with Client(mcp_server.build_server()) as c:
            names = [t.name for t in (await c.list_tools()).tools]
            r = await c.call_tool("analyze_case", {"sample_pack": "brs1-birth-newborn-duplicate", "fictional": True})
            f = await c.call_tool("get_findings", {"case_id": r.structured_content["case_id"]})
            bad = await c.call_tool("analyze_case", {"sample_pack": "brs1-birth-newborn-duplicate", "fictional": False})
            return names, r, f, bad
    names, r, f, bad = run(go())
    assert names == ["list_sample_packs", "analyze_case", "get_findings", "get_protections", "list_letters",
                     "draft_letter", "get_call_sheet", "get_deadlines", "get_report_pdf", "find_policy"]
    assert not any(w in n for n in names for w in FORBIDDEN)  # nothing that sends, submits or pays
    assert not r.is_error and f.structured_content["findings"]
    assert bad.is_error and "only fictional" in bad.content[0].text


async def _http_mcp(gate, headers):
    star = gate.app
    async with star.router.lifespan_context(star):
        hc = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=gate), base_url=BASE, headers=headers)
        async with hc, Client(streamable_http_client(f"{BASE}/mcp", http_client=hc)) as c:
            own = await c.call_tool("analyze_case", {"fictional": True, "photos": ["eA=="]})
            pack = await c.call_tool("analyze_case", {"sample_pack": "brs1-birth-all-errors", "fictional": True})
            return own, pack


def test_mcp_over_streamable_http_anonymous_and_with_key():
    anon_own, anon_pack = run(_http_mcp(agent_entry.app(public_url=BASE), {}))
    assert anon_own.is_error and "API key" in anon_own.content[0].text
    assert not anon_pack.is_error and anon_pack.structured_content["total_may_come_off"] == "1636.53"
    key_own, _ = run(_http_mcp(agent_entry.app(public_url=BASE), {"X-API-Key": KEY}))
    assert key_own.is_error and "live model run" in key_own.content[0].text  # the key reached the tool


def _many(gate, calls):
    """Several requests within one lifespan of the app, as in production (the MCP session manager starts once)."""
    async def go():
        star = gate.app
        async with star.router.lifespan_context(star):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url=BASE) as h:
                return [await h.request(m, p, **kw) for m, p, kw in calls]
    return run(go())


def _raw(gate, method, path, **kw):
    return _many(gate, [(method, path, kw)])[0]


def test_wrong_key_wrong_host_and_wrong_protocol_version_are_refused():
    """A raw 2026-07-28 request: the same body passes with the right headers (control), so each refusal
    below is for its own reason, not a malformed request."""
    m = "io.modelcontextprotocol/"
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {
        m + "protocolVersion": "2026-07-28", m + "clientInfo": {"name": "test", "version": "1"},
        m + "clientCapabilities": {}}}}
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
           "Mcp-Method": "tools/list", "MCP-Protocol-Version": "2026-07-28"}
    ok, wrong_key, evil, old = _many(agent_entry.app(public_url=BASE), [
        ("POST", "/mcp", {"json": body, "headers": hdr}),
        ("POST", "/mcp", {"json": body, "headers": hdr | {"X-API-Key": "nope"}}),
        ("POST", "/mcp", {"json": body, "headers": hdr | {"Host": "evil.example"}}),
        ("POST", "/mcp", {"json": body, "headers": hdr | {"MCP-Protocol-Version": "1999-01-01"}})])
    assert ok.status_code == 200 and len(ok.json()["result"]["tools"]) == 10
    assert wrong_key.status_code == 401
    assert evil.status_code == 421
    assert old.status_code == 400 and old.json()["error"]["code"] == -32020


def test_anonymous_calls_are_limited_per_address_and_big_bodies_refused():
    card = ("GET", "/.well-known/agent-card.json", {})
    rs = _many(agent_entry.app(public_url=BASE, per_minute=3), [card] * 4 + [
        ("GET", "/agents/healthz", {}), ("GET", "/.well-known/agent-card.json", {"headers": {"X-API-Key": KEY}})])
    assert [r.status_code for r in rs[:4]] == [200, 200, 200, 429]
    assert rs[4].status_code == 200  # health is never limited
    assert rs[5].status_code == 200  # a key is not limited
    big = _raw(agent_entry.app(public_url=BASE), "POST", "/a2a", content=b"{}",
               headers={"Content-Length": str(agent_entry.MAX_BODY + 1)})
    assert big.status_code == 413


def test_live_switch_without_keys_refuses_to_start(monkeypatch):
    monkeypatch.setenv("BILLRADAR_AGENT_LIVE", "1")
    monkeypatch.setenv("BILLRADAR_AGENT_KEYS", "")
    with pytest.raises(RuntimeError, match="refusing to start open"):
        agent_entry.app(public_url=BASE)
    monkeypatch.setenv("BILLRADAR_AGENT_KEYS", "short")  # a key under 24 characters does not count
    with pytest.raises(RuntimeError):
        agent_entry.app(public_url=BASE)


# --- A2A -----------------------------------------------------------------------------------------------

def test_agent_card_has_the_required_fields():
    card = _raw(agent_entry.app(public_url=BASE), "GET", "/.well-known/agent-card.json").json()
    for field in ("name", "description", "supportedInterfaces", "version", "capabilities", "defaultInputModes",
                  "defaultOutputModes", "skills"):
        assert card.get(field) is not None, field
    assert card["supportedInterfaces"] == [{"url": f"{BASE}/a2a", "protocolBinding": "JSONRPC",
                                            "protocolVersion": "1.0"}]
    assert {s["id"] for s in card["skills"]} == set(a2a_agent.SKILLS)
    assert not any(w in s["id"] for s in card["skills"] for w in FORBIDDEN)
    assert "never sends anything" in card["description"]


def _msg(data, headers=None, n=1):
    body = {"jsonrpc": "2.0", "id": n, "method": "SendMessage", "params": {"message": {
        "role": "ROLE_USER", "messageId": f"m{n}", "parts": [{"data": data}]}}}
    return ("POST", "/a2a", {"json": body, "headers": {"A2A-Version": "1.0"} | (headers or {})})


def _part(r):
    assert r.status_code == 200, r.text
    return r.json()["result"]["message"]["parts"][0]


def _send(gate, data, headers=None):
    return _part(_raw(gate, *_msg(data, headers)[:2], **_msg(data, headers)[2]))


def test_a2a_send_message_runs_a_skill():
    gate = agent_entry.app(public_url=BASE)
    part = _send(gate, {"skill": "analyze_case", "args": {"sample_pack": "brs1-birth-all-errors", "fictional": True}})
    assert part["data"]["total_may_come_off"] == "1636.53"
    f = _send(agent_entry.app(public_url=BASE), {"skill": "get_findings", "args": {"case_id": part["data"]["case_id"]}})
    assert any(x["id"] == "F1 #1" for x in f["data"]["findings"])


@pytest.mark.parametrize("data,headers,expect", [
    ({"skill": "send_letter", "args": {}}, None, "unknown skill"),
    ({"skill": "analyze_case", "args": {"sample_pack": "brs1-birth-all-errors"}}, None, "only fictional"),
    ({"skill": "analyze_case", "args": {"fictional": True, "photos": ["eA=="]}}, None, "API key"),
    ({"skill": "analyze_case", "args": {"fictional": True, "photos": ["eA=="]}}, {"X-API-Key": KEY}, "live model run"),
    ({"skill": "get_findings", "args": {"case_id": "a1", "extra": 1}}, None, "bad args"),
])
def test_a2a_refusals_come_back_as_text(data, headers, expect):
    part = _send(agent_entry.app(public_url=BASE), data, headers)
    assert part["text"].startswith("Refused:") and expect in part["text"]
