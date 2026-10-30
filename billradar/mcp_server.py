"""BillRadar as an MCP server, so another AI agent can check a family's (fictional) medical bills.

MCP specification 2026-07-28 through the official Python SDK (`mcp==2.2.0`). Two transports:
  - Streamable HTTP, stateless, at /mcp inside agent_entry.app() (the web app mounts it);
  - stdio for an agent running on the same machine: `python -m billradar.mcp_server`.

Every tool is a thin layer over agent_api; refusals come back as tool errors with a message the
calling agent can act on. There is no tool that sends, submits or pays for anything.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from billradar import links
from billradar import agent_api

INSTRUCTIONS = (
    "BillRadar checks a family's hospital bills and insurance statements against the hospital's own "
    "financial assistance policy, the insurance plan and patient-protection laws, and drafts letters. "
    "Every finding comes with the exact quote and page it rests on. Start with list_sample_packs and "
    "analyze_case(sample_pack=..., fictional=true), then get_findings, get_protections, list_letters, "
    "draft_letter, get_call_sheet, get_deadlines, get_report_pdf. Only fictional papers are accepted. "
    "BillRadar never sends anything and gives no medical or legal advice: the family reviews and signs "
    "every letter.")

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
FIND = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
RUN = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


def _call(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except agent_api.AgentError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:  # e.g. an unknown language
        raise ToolError(str(exc)) from exc


def build_server() -> MCPServer:
    server = MCPServer(name="billradar", title="BillRadar", version="0.1.0", instructions=INSTRUCTIONS,
                       website_url=links.tree())

    @server.tool(annotations=READ, description="List the fictional sample packs of medical bills an agent can "
                                               "analyze without a live model run.")
    def list_sample_packs() -> dict[str, Any]:
        return {"packs": agent_api.list_sample_packs(), "note": agent_api.DISCLAIMER}

    @server.tool(annotations=RUN, description=(
        "Analyze a case. Either sample_pack (a name from list_sample_packs) or your own fictional papers: "
        "photos (base64 JPEG/PNG of the sheets) and files ([{name, base64}] PDFs) with household_size, "
        "annual_income, insured and policy_id; own papers need an API key and a live model. fictional must "
        "be true. Returns a case_id and the total that may come off."))
    def analyze_case(fictional: bool, sample_pack: str = "", photos: list[str] | None = None,
                     files: list[dict[str, str]] | None = None, household_size: int = 0, annual_income: str = "",
                     insured: bool = True, policy_id: str = "memorial-2026-05", letterhead: str = "",
                     private: bool = False) -> dict[str, Any]:
        return _call(agent_api.analyze_case, sample_pack=sample_pack, photos=photos, files=files,
                     household_size=household_size, annual_income=annual_income, insured=insured,
                     policy_id=policy_id, letterhead=letterhead, fictional=fictional, private=private)

    @server.tool(annotations=READ, description=(
        "Findings of a case: each with the sheets and lines it rests on and the exact quotes (title, page) "
        "behind it; 'worth asking' items without a sum; and what was checked and holds no money."))
    def get_findings(case_id: str) -> dict[str, Any]:
        return _call(agent_api.get_findings, case_id)

    @server.tool(annotations=READ, description="The 13 patient protections (federal and Florida) and whether "
                                               "each applies to this case, with quotes and deadlines.")
    def get_protections(case_id: str) -> dict[str, Any]:
        return _call(agent_api.get_protections, case_id)

    @server.tool(annotations=READ, description="Letters BillRadar drafted for this case (kind, subject, to).")
    def list_letters(case_id: str) -> dict[str, Any]:
        return _call(agent_api.list_letters, case_id)

    @server.tool(annotations=READ, description=(
        "One letter as plain text in English, the version the family signs and sends, with its sources. "
        "lang 'ru' or 'es' adds a translation to read, never to send. BillRadar does not send letters."))
    def draft_letter(case_id: str, kind: str, lang: str = "en") -> dict[str, Any]:
        return _call(agent_api.draft_letter, case_id, kind, lang)

    @server.tool(annotations=READ, description=(
        "Call cheat sheet for the hospital or the insurer: what to say first, numbers to have ready, answers "
        "to what they usually say (each with its quote), what to ask for."))
    def get_call_sheet(case_id: str, to: str = "hospital") -> dict[str, Any]:
        return _call(agent_api.get_call_sheet, case_id, to)

    @server.tool(annotations=READ, description="Every deadline in the case, earliest first, each with its source.")
    def get_deadlines(case_id: str) -> dict[str, Any]:
        return _call(agent_api.get_deadlines, case_id)

    @server.tool(annotations=READ, description="The whole case as a PDF report (base64), in en, ru or es.")
    def get_report_pdf(case_id: str, lang: str = "en") -> dict[str, Any]:
        return _call(agent_api.get_report_pdf, case_id, lang)

    @server.tool(annotations=FIND, description=(
        "Find a hospital's own financial assistance policy (charity care): Tavily searches only the hospital's "
        "site, Nemotron copies the income tiers (percent of the poverty level -> free care or discount) word for "
        "word, and code keeps a tier only if its quote and numbers are in the policy text. Give the hospital's "
        "name and state, and the site printed on the bill (website) or the city/ZIP from its address. "
        "status 'ask_user' returns a candidate site: ask the family, then call again with website=candidate.site "
        "and confirmed=true. Send only these fields, never a bill. Not a promise of a discount."))
    def find_policy(hospital: str, state: str, city: str = "", zip: str = "", website: str = "",
                    confirmed: bool = False) -> dict[str, Any]:
        return _call(agent_api.find_policy, hospital, state, city, zip, website, confirmed)

    return server


def main() -> None:
    """stdio: the agent runs on this machine and is trusted like the person using it."""
    agent_api.authorized.set(True)
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
