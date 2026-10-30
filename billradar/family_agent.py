"""An agent that uses BillRadar: Nemotron 3 Super, acting for the family, calls BillRadar's MCP tools.

This is the other side of the agent entry: any agent can do the same. The model gets the tools exactly
as BillRadar's MCP server lists them (name, description, JSON Schema), decides which to call, and we run
each call through an MCP client and hand back the result (function calling; the history is sent each turn,
results in the order of their calls). The agent can only read and draft: BillRadar has no tool that sends, submits or pays.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from mcp.client.client import Client

from billradar.nemotron_client import NemotronClient

MODEL = "reader"
MAX_TURNS = 12
MAX_RESULT_CHARS = 30_000
SKIP = {"get_report_pdf"}  # base64 PDFs are for people, not for the model's context

INSTRUCTIONS = """You act for a family who asked you to check their hospital bills with BillRadar. You have BillRadar's tools.

Work like this: analyze the case, read the findings and the deadlines, look at the letters, then tell the family in a few short lines: what may come off and why (name the source of each point as BillRadar gives it), the earliest deadline, and which letters are ready for them to review and sign.
Use only what the tools return. Do not add amounts, dates or rules of your own. You never send anything and you give no medical advice."""


@dataclass
class Call:
    name: str
    arguments: dict
    is_error: bool
    summary: str  # first characters of the result, for the log


@dataclass
class AgentTurns:
    answer: str = ""
    calls: list[Call] = field(default_factory=list)
    stopped: str = ""
    replayed: bool = True


def tools_for_model(mcp_tools) -> list[dict]:
    return [{"type": "function", "name": t.name, "description": t.description or "",
             "parameters": t.input_schema if hasattr(t, "input_schema") else t.inputSchema}
            for t in mcp_tools if t.name not in SKIP]


def _output(events: list[dict]) -> list[dict]:
    final = next((e for e in reversed(events) if e.get("type") == "response.completed"), {})
    return (final.get("response") or {}).get("output") or []


def _result_text(r) -> str:
    if getattr(r, "structured_content", None) is not None:
        text = json.dumps(r.structured_content, ensure_ascii=False)
    else:
        text = " ".join(getattr(c, "text", "") for c in r.content)
    return text[:MAX_RESULT_CHARS]


async def run(client: NemotronClient, server, task: str) -> AgentTurns:
    """`server`: what mcp.client.Client accepts: BillRadar's MCPServer in process, or an MCP URL."""
    out = AgentTurns()
    async with Client(server) as mcp:
        tools = tools_for_model((await mcp.list_tools()).tools)
        history: list[dict] = [{"role": "user", "content": [{"type": "input_text", "text": task}]}]
        for _ in range(MAX_TURNS):
            body = {"model": MODEL, "store": False, "stream": True, "reasoning": {"effort": "low"},
                    "max_output_tokens": 4000, "instructions": INSTRUCTIONS, "tools": tools, "input": history}
            res = client.respond(body, label="family-agent")
            out.replayed = res.replayed
            items = _output(res.events)
            calls = [i for i in items if i.get("type") == "function_call"]
            if not calls:
                out.answer = res.text.strip()
                return out
            history += [i for i in items if i.get("type") != "message"]
            for c in calls:
                try:
                    args = json.loads(c.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if c.get("name") in SKIP or c.get("name") not in {t["name"] for t in tools}:
                    text, err = json.dumps({"error": f"no tool {c.get('name')!r}"}), True
                else:
                    r = await mcp.call_tool(c["name"], args)
                    text, err = _result_text(r), bool(r.is_error)
                out.calls.append(Call(c.get("name", ""), args, err, text[:160]))
                history.append({"type": "function_call_output", "call_id": c.get("call_id"), "output": text})
        out.stopped = "turn budget reached"
    return out
