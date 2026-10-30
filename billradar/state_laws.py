"""State laws on hospital financial assistance that may apply, for hospitals outside Florida, from the state's own site.

Florida's protections are checked by hand in protections.json and are not touched here. For a hospital in a state
listed in STATES, Tavily searches only that state's official law site (a .gov host), Nemotron 3 Super picks the
sections about hospital financial assistance and copies their words, and code shows a section only if:
  - the page is on the state's official law host, over https;
  - the quote is in the page's text word for word and carries no dollar amount;
  - the page prints a revision or effective date (shown next to the quote).
What the family sees is the quote, the link, the date and one fixed line: "May apply to you; check." No summary in
our words, no amounts, nothing like "you are protected": whether a law applies depends on the plan (an employer's
self-funded plan is outside state law), the hospital, the dates and the law's own conditions.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

from billradar.nemotron_client import NemotronError
from billradar.tavily_client import TavilyError

MODEL = "nvidia/nemotron-3-super-120b-a12b"
LABEL = "May apply to you; check."
NOTE = ("State law depends on your plan, the hospital, the dates and the law's own conditions (an employer's "
        "self-funded plan is outside state law). This is the law's own text, not legal advice.")
MAX_TEXT = 40_000
MAX_SECTIONS = 2

# The official site of each state's laws, and what to ask it. Hosts are matched exactly.
STATES = {
    "CA": {"hosts": ("leginfo.legislature.ca.gov",), "law_page": r"codes_displaySection",
           "query": "California Health and Safety Code hospital fair pricing financial assistance policy 127405",
           "retry": "Health and Safety Code section 127405 hospital discount payment charity care policy"},
    "TX": {"hosts": ("statutes.capitol.texas.gov",), "law_page": r"GetStatute\.aspx|/Docs/HS/",
           "query": "Texas Health and Safety Code hospital itemized bill patient charity care financial assistance",
           "retry": "Health and Safety Code chapter 311 hospital charity care policy"},
    "NY": {"hosts": ("www.nysenate.gov", "nysenate.gov", "legislation.nysenate.gov"), "law_page": r"/legislation/laws/",
           "query": "New York Public Health Law 2807-k hospital financial assistance policy",
           "retry": "Public Health Law section 2807-K general hospital indigent care financial assistance"},
}
DATE = re.compile(
    r"(?:amended|added|effective|eff\.|enacted|stats\.\s*\d{4}|acts\s+\d{4}|last (?:amended|updated)|as of)"
    r"[^\n]{0,80}?(?:19|20)\d{2}", re.I)

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sections"],
    "properties": {"sections": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["section", "quote", "date_quote"],
        "properties": {"section": {"type": "string"}, "quote": {"type": "string"}, "date_quote": {"type": "string"}}}}},
}
INSTRUCTIONS = (
    "You read one page of a US state's statutes. Pick at most two sections that put a duty on hospitals about "
    "financial assistance or charity care for patients, discounted payment, or billing and collection of patient "
    "bills. For each: section is the section number as printed (e.g. '127405'); quote is ONE sentence or clause of "
    "that section copied word for word, the part that states the hospital's duty; never a dollar amount; never "
    "combine words from different places. date_quote is the history or effective-date line printed with that "
    "section (e.g. 'Amended by Stats. 2024, Ch. 770, Sec. 3. Effective January 1, 2025.'), copied exactly, or an "
    "empty string. If the page has no such section, return an empty list.")


@dataclass
class Section:
    state: str
    section: str
    quote: str
    date_quote: str
    url: str
    title: str
    label: str = LABEL


@dataclass
class StateLaws:
    state: str
    status: str  # shown | link_only | not_found | skipped | error
    sections: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    url: str = ""
    message: str = ""
    note: str = NOTE
    tavily_calls: list[dict] = field(default_factory=list)
    nemotron_calls: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def official(url: str, state: str) -> bool:
    u = urlparse(url or "")
    host = (u.hostname or "").lower()
    return u.scheme == "https" and host.endswith(".gov") and host in STATES.get(state, {}).get("hosts", ())


HISTORY = re.compile(r"\((?:Amended|Added|Repealed and added|Enacted)(?:[^()]|\([^()]{0,40}\)){0,240}?\)")


def page_history(text: str) -> str:
    """The one history line a single-section page prints ('(Amended by Stats. 2024, ... Effective January 1, 2025.)'),
    or '' when there is none or more than one (then each section must bring its own)."""
    lines = {m.group(0) for m in HISTORY.finditer(text or "") if re.search(r"(?:19|20)\d{2}", m.group(0))}
    return lines.pop().strip("()") if len(lines) == 1 else ""


def check_section(sec: dict, text_key: str) -> str:
    """'' when the section stands, else why it is dropped."""
    from billradar.policy_finder import _key, trim_quote
    quote = trim_quote(sec.get("quote", ""))
    if len(_key(quote)) < 30:
        return "quote too short to check"
    if "$" in quote:
        return "the quote carries a dollar amount"
    if _key(quote) not in text_key:
        return "quote not found in the law's text"
    date_quote = (sec.get("date_quote") or "").strip()
    if not date_quote or _key(date_quote) not in text_key or not DATE.search(date_quote):
        return "no revision or effective date printed with it"
    return ""


def for_state(state: str, tavily, nemotron) -> StateLaws:
    """State law sections that may apply to a hospital in `state`. Florida and states not in STATES: skipped."""
    st = (state or "").upper()
    if st == "FL":
        return StateLaws(st, "skipped", message="Florida's protections are shown from BillRadar's checked base")
    if st not in STATES:
        return StateLaws(st, "skipped", message="no official law site configured for this state yet")
    out = StateLaws(st, "not_found")
    first_t, first_n = len(tavily.calls), len(getattr(nemotron, "calls", []))
    try:
        _find(st, tavily, nemotron, out)
    except (TavilyError, NemotronError, ValueError, KeyError) as exc:
        out.status, out.message, out.sections = "error", f"{type(exc).__name__}: {exc}", []
    finally:
        out.tavily_calls = list(tavily.calls[first_t:])
        out.nemotron_calls = [{k: c.get(k) for k in ("label", "model", "answered_by", "response_id", "usd", "live")}
                              for c in getattr(nemotron, "calls", [])[first_n:]]
    return out


def _law_pages(st: str, cfg: dict, query: str, tavily) -> list[dict]:
    """Pages of the statutes themselves (not bills, not PDFs of whole codes) from the state's official law host."""
    res = tavily.search({"query": query, "search_depth": "advanced", "include_domains": list(cfg["hosts"]),
                         "include_raw_content": True, "max_results": 3}, label="laws:search")
    return [dict(r) for r in res.get("results") or []
            if official(r.get("url", ""), st) and re.search(cfg["law_page"], r.get("url", ""))
            and not r.get("url", "").lower().endswith(".pdf")]


def _find(st: str, tavily, nemotron, out: StateLaws) -> None:
    from billradar.policy_finder import _key, excerpt, trim_quote
    cfg = STATES[st]
    pages = _law_pages(st, cfg, cfg["query"], tavily)
    if not pages:  # bills and other pages only (NY): one narrower search for the law itself
        pages = _law_pages(st, cfg, cfg["retry"], tavily)
    if not pages:
        out.message = "no page of the state's statutes came back from its official law site"
        return
    empty = [r["url"] for r in pages if not (r.get("raw_content") or "").strip()][:3]
    if empty:  # Tavily search often returns these pages without text; extract reads them (1 credit for up to 5)
        ex = tavily.extract({"urls": empty, "extract_depth": "basic"}, label="laws:extract")
        got = {x.get("url"): x.get("raw_content") or "" for x in ex.get("results") or [] if official(x.get("url", ""), st)}
        for r in pages:
            r["raw_content"] = r.get("raw_content") or got.get(r["url"], "")
    pages = [r for r in pages if (r.get("raw_content") or "").strip()]
    if not pages:
        out.url = empty[0] if empty else ""
        out.status, out.message = "link_only", "the state's law page came back without text; read it yourself"
        return
    page = max(pages, key=lambda r: float(r.get("score") or 0))
    text = page["raw_content"]
    out.url = page["url"]
    body = {
        "model": MODEL, "instructions": INSTRUCTIONS, "store": False, "max_output_tokens": 2048,
        "reasoning": {"effort": "low"},
        "input": [{"role": "user", "content": [{"type": "input_text", "text": (
            f"State: {st}\nPage: {page.get('title', '')}\n\n{excerpt(text, MAX_TEXT)}")}]}],
        "text": {"format": {"type": "json_schema", "name": "state_law_sections", "schema": SCHEMA, "strict": True}},
    }
    answer = json.loads(nemotron.respond(body, label="laws:sections").text)
    text_key = _key(text)
    history = page_history(text)
    for sec in (answer.get("sections") or [])[:MAX_SECTIONS + 2]:
        if not (sec.get("date_quote") or "").strip() and history:  # a one-section page: its history line is the date
            sec = {**sec, "date_quote": history}
        why = check_section(sec, text_key)
        if why:
            out.dropped.append({**sec, "why": why})
        elif len(out.sections) < MAX_SECTIONS:
            out.sections.append(asdict(Section(st, sec.get("section", "").strip(), trim_quote(sec["quote"]),
                                               sec["date_quote"].strip().strip("()").strip(), page["url"],
                                               page.get("title", ""))))
    out.status = "shown" if out.sections else "link_only"
    if not out.sections:
        out.message = "we found the state's law page, but no section passed the check; read it yourself"
