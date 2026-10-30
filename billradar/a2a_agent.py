"""BillRadar as an A2A agent (Agent2Agent protocol 1.0, a2a-sdk==1.1.2).

The agent card is served at /.well-known/agent-card.json; messages arrive over JSON-RPC
(`SendMessage`). A message carries one data part {"skill": <name>, "args": {...}} where the skill is
one of agent_api's functions; the answer is one data part with the result, or a text part saying why
it was refused. Same gates as everywhere: fictional papers only, nothing is ever sent for the family.
"""

from __future__ import annotations

from typing import Any

from a2a.helpers.proto_helpers import get_data_parts, get_message_text, new_data_message, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill

from billradar import links
from billradar import agent_api

SKILLS: dict[str, tuple[str, str, list[str]]] = {
    # name: (title, description, example)
    "list_sample_packs": ("List sample packs", "Fictional packs of medical bills that can be analyzed without a "
                          "live model run.", ['{"skill": "list_sample_packs"}']),
    "analyze_case": ("Analyze a case", "Check a fictional pack of hospital bills and insurance statements "
                     "against the hospital's assistance policy, the plan and patient-protection laws. "
                     "fictional must be true.",
                     ['{"skill": "analyze_case", "args": {"sample_pack": "brs1-birth-all-errors", "fictional": true}}']),
    "get_findings": ("Findings with quotes", "Every finding with the sheets, lines and exact policy or law "
                     "quotes it rests on.", ['{"skill": "get_findings", "args": {"case_id": "..."}}']),
    "get_protections": ("Patient protections", "The 13 federal and Florida protections and whether each "
                        "applies.", ['{"skill": "get_protections", "args": {"case_id": "..."}}']),
    "list_letters": ("List letters", "Letters drafted for the case.",
                     ['{"skill": "list_letters", "args": {"case_id": "..."}}']),
    "draft_letter": ("Draft a letter", "A letter in English for the family to sign; a translation to read in "
                     "ru or es. Nothing is sent.",
                     ['{"skill": "draft_letter", "args": {"case_id": "...", "kind": "dispute"}}']),
    "get_call_sheet": ("Call cheat sheet", "What to say to the billing office or the insurer, with quotes.",
                       ['{"skill": "get_call_sheet", "args": {"case_id": "...", "to": "hospital"}}']),
    "get_deadlines": ("Deadlines", "Every deadline in the case with its source.",
                      ['{"skill": "get_deadlines", "args": {"case_id": "..."}}']),
    "get_report_pdf": ("PDF report", "The case as a PDF (base64), en, ru or es.",
                       ['{"skill": "get_report_pdf", "args": {"case_id": "...", "lang": "en"}}']),
}


def agent_card(url: str) -> AgentCard:
    return AgentCard(
        name="BillRadar",
        description=("Finds money a family may not owe on hospital bills and insurance statements: missed "
                     "financial assistance, amounts that do not match the insurance, duplicate lines, denied "
                     "claims to appeal. Every finding rests on an exact quote. Fictional papers only for now. "
                     "No medical or legal advice; it never sends anything for the family."),
        version="0.1.0",
        provider=AgentProvider(organization="BillRadar", url=links.tree()),
        documentation_url=links.blob("docs/AGENTS.md"),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["application/json"],
        default_output_modes=["application/json", "text/plain"],
        skills=[AgentSkill(id=k, name=v[0], description=v[1], tags=["medical bills", "insurance", "billing"],
                           examples=v[2]) for k, v in SKILLS.items()],
    )


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    skill = payload.get("skill", "")
    args = payload.get("args") or {}
    fn = getattr(agent_api, skill, None) if skill in SKILLS else None
    if fn is None:
        raise agent_api.AgentError(f"unknown skill {skill!r}; one of {', '.join(SKILLS)}")
    if not isinstance(args, dict):
        raise agent_api.AgentError("args must be an object")
    try:
        return fn(**args)
    except TypeError as exc:
        raise agent_api.AgentError(f"bad args for {skill}: {exc}") from exc


class BillRadarExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        msg = context.message
        data = get_data_parts(msg.parts) if msg else []
        ctx = dict(context_id=context.context_id, task_id=context.task_id)
        if not data:
            text = get_message_text(msg) if msg else ""
            await event_queue.enqueue_event(new_text_message(
                "Send a data part {\"skill\": ..., \"args\": {...}}. Skills: " + ", ".join(SKILLS)
                + (". You wrote: " + text[:200] if text else ""), **ctx))
            return
        try:
            result = handle(data[0] if isinstance(data[0], dict) else {})
        except (agent_api.AgentError, ValueError) as exc:
            await event_queue.enqueue_event(new_text_message(f"Refused: {exc}", **ctx))
            return
        await event_queue.enqueue_event(new_data_message(result, media_type="application/json", **ctx))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(new_text_message("Nothing to cancel: every skill answers at once.",
                                                         context_id=context.context_id, task_id=context.task_id))


def routes(public_url: str, rpc_path: str = "/a2a") -> list:
    card = agent_card(public_url.rstrip("/") + rpc_path)
    handler = DefaultRequestHandler(agent_executor=BillRadarExecutor(), task_store=InMemoryTaskStore(),
                                    agent_card=card)
    return create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_path)
