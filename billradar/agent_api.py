"""What another AI agent can ask BillRadar, the same answers a person gets on the case screen:
findings with their quotes, the patient protections, letters, the call cheat sheet, deadlines and the
PDF report. Plain functions with JSON-ready results; the MCP server and the A2A agent are thin layers
over them (mcp_server.py, a2a_agent.py).

The same gates as for people, held here:
  - only fictional data: a call without fictional=true is refused;
  - nothing is ever sent, submitted or paid: there is no such function, and every result says the
    family reviews and signs every letter;
  - no free-text questions, so there is nothing to answer with medical advice;
  - live model calls only when both BILLRADAR_LIVE=1 and BILLRADAR_AGENT_LIVE=1 and the caller holds
    an API key. Without them an agent can run the fictional sample packs: the readings come from each
    pack's generator and the results say REPLAYED.
"""

from __future__ import annotations

import base64
import binascii
import contextvars
import json
import os
import re
import secrets
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from billradar import appeal, callprep, checks, i18n, laws, pipeline, plans, policy, providers, report_pdf, writer
from billradar.case import Case, Person, Profile, Role, Source
from billradar.model_api import ReplayMissing
from billradar.reader import to_documents

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs" / "agents"
PACKS = ROOT / "eval" / "packs" / "work"
MAX_PHOTOS = 12
MAX_FILE_BYTES = 8 * 1024 * 1024
DISCLAIMER = ("BillRadar helps with hospital bills and insurance. It is not medical or legal advice. "
              "It never sends anything: the family reviews and signs every letter.")

# Set by the HTTP layer when the request carries a valid X-API-Key (stdio, run locally, sets it too).
authorized: contextvars.ContextVar[bool] = contextvars.ContextVar("billradar_agent_authorized", default=False)


class AgentError(ValueError):
    """A refusal or a bad request, with a message meant for the calling agent."""


def _jsonable(x):
    if is_dataclass(x):
        x = asdict(x)
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, date):
        return x.isoformat()
    if hasattr(x, "value") and type(x).__module__.startswith("billradar"):
        return x.value
    return x


def live_enabled() -> bool:
    return (os.environ.get("BILLRADAR_LIVE") == "1" and os.environ.get("BILLRADAR_AGENT_LIVE") == "1"
            and providers.make_client().live)


# --- cases -----------------------------------------------------------------------------------

def _case_dir(case_id: str) -> Path:
    if not re.fullmatch(r"a[0-9a-f]{12}", case_id or ""):
        raise AgentError("unknown case_id")
    d = RUNS / case_id
    if not (d / "case.json").exists():
        raise AgentError("unknown case_id")
    return d


def _load(case_id: str) -> tuple[Path, Case, checks.Report, dict]:
    d = _case_dir(case_id)
    info = json.loads((d / "info.json").read_text()) if (d / "info.json").exists() else {}
    return (d, Case.model_validate_json((d / "case.json").read_text()),
            checks.Report.model_validate_json((d / "report.json").read_text()), info)


def list_sample_packs() -> list[dict]:
    """Fictional packs an agent can run without a live model."""
    out = []
    for p in sorted(PACKS.iterdir()) if PACKS.exists() else []:
        prof = p / "profile.json"
        if not (prof.exists() and (p / "answers" / "reading.json").exists()):
            continue
        raw = json.loads(prof.read_text())
        if not raw.get("fictional"):
            continue
        sheets = json.loads((p / "answers" / "reading.json").read_text()).get("documents", [])
        out.append({"name": p.name, "letterhead": raw.get("letterhead", ""), "policy_id": raw["policy_id"],
                    "documents": len(sheets), "fictional": True})
    return out


def _sample_case(name: str, case_id: str) -> Case:
    if name not in {p["name"] for p in list_sample_packs()}:
        raise AgentError(f"unknown sample pack {name!r}; see list_sample_packs")
    p = PACKS / name
    raw = json.loads((p / "profile.json").read_text())
    reading = json.loads((p / "answers" / "reading.json").read_text())
    expected = {d["doc_id"]: (Source.PDF if d["doc_id"].startswith("D") else Source.PAPER)
                for d in reading.get("documents", [])}
    docs, _ = to_documents(reading, expected)
    return Case(case_id=case_id, letterhead=raw.get("letterhead", ""), policy_id=raw["policy_id"],
                today=date.fromisoformat(raw["today"]), demo=True,
                profile=Profile.model_validate(raw["profile"]), documents=docs)


def _decode(b64: str, what: str) -> bytes:
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AgentError(f"{what} is not valid base64") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise AgentError(f"{what} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    return raw


def analyze_case(sample_pack: str = "", photos: list[str] | None = None, files: list[dict] | None = None,
                 household_size: int = 0, annual_income: str = "", insured: bool = True,
                 policy_id: str = "memorial-2026-05", letterhead: str = "", fictional: bool = False,
                 private: bool = False) -> dict:
    """Run a case: a fictional sample pack (no live model needed) or the agent's own fictional papers
    (photos of sheets and PDFs, base64; needs a live model and an API key)."""
    if not fictional:
        raise AgentError("BillRadar takes only fictional (sample) papers for now: set fictional=true. "
                         "Real bills need on-device covering and the owner's yes first.")
    case_id = "a" + secrets.token_hex(6)
    if sample_pack:
        case = _sample_case(sample_pack, case_id)
        report, verdict = pipeline.check_and_hold(case, checks.run(case))
        source, replayed = f"sample pack {sample_pack}", True
    else:
        photos, files = photos or [], files or []
        if not photos and not files:
            raise AgentError("send sample_pack, or photos and/or files")
        if not authorized.get():
            raise AgentError("your own papers need an API key (X-API-Key); without one, use a sample pack")
        if not live_enabled():
            raise AgentError("your own papers need a live model run, which is off here; use a sample pack "
                             "(list_sample_packs)")
        if len(photos) > MAX_PHOTOS:
            raise AgentError(f"at most {MAX_PHOTOS} photos")
        if policy_id not in [p for p in policy.available() if policy.load(p).tiers]:
            raise AgentError(f"unknown policy_id {policy_id!r}")
        if household_size < 1:
            raise AgentError("household_size must be at least 1")
        try:
            income = Decimal(annual_income)
        except Exception as exc:
            raise AgentError("annual_income must be a number like 61000") from exc
        prof = Profile(household_size=household_size, annual_income=income, insured=insured,
                       people=[Person(token="PATIENT_1", role=Role.MOTHER)])
        imgs = [_decode(x, f"photo {i + 1}") for i, x in enumerate(photos)]
        pdfs = [(f.get("name") or f"file{i + 1}.pdf", _decode(f.get("base64", ""), f"file {i + 1}"))
                for i, f in enumerate(files)]
        try:
            run_ = pipeline.run(providers.make_client(), imgs, pdfs, prof, policy_id, letterhead, date.today(), case_id,
                                fictional=True, private=private)
        except ReplayMissing as exc:
            raise AgentError("no live answer for these papers") from exc
        run_.case.demo = True
        case, report, verdict = run_.case, run_.report, run_.verdict
        source, replayed = "papers sent by the agent", run_.replayed
    d = RUNS / case_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "case.json").write_text(case.model_dump_json())
    (d / "report.json").write_text(report.model_dump_json())
    (d / "info.json").write_text(json.dumps({"source": source, "replayed": replayed,
                                             "checked": verdict.checked if verdict else 0}))
    return {"case_id": case_id, "source": source, "replayed": replayed, **_summary(case, report),
            "next": ["get_findings", "get_protections", "list_letters", "get_call_sheet", "get_deadlines",
                     "get_report_pdf"], "note": DISCLAIMER}


def _summary(case: Case, report: checks.Report) -> dict:
    sure = [f for f in report.findings if f.severity != "borderline"]
    return {"total_may_come_off": str(report.total_may_come_off),
            "apply_by": report.apply_by.isoformat() if report.apply_by else None,
            "findings": len(sure), "worth_asking": len(report.findings) - len(sure),
            "held_back": len(report.held_back), "policy_id": case.policy_id}


def _sources(refs: list[str]) -> list[dict]:
    return [writer.source_of(r) for r in dict.fromkeys(refs) if writer.verified(r)]


def _finding(f: checks.Finding, n: int) -> dict:
    return {"id": f"{f.kind.value} #{n}", "kind": f.kind.value, "title": f.title, "detail": f.detail,
            "amount": str(f.amount) if f.amount is not None else None,
            "deadline": f.deadline.isoformat() if f.deadline else None, "severity": f.severity,
            "confidence": f.confidence,
            "evidence": [e.model_dump() for e in f.evidence],
            "sources": _sources(list(f.policy_refs) + list(f.laws))}


def get_findings(case_id: str) -> dict:
    _, case, report, info = _load(case_id)
    counts: dict[str, int] = {}
    sure, maybe = [], []
    for f in report.findings:
        counts[f.kind.value] = counts.get(f.kind.value, 0) + 1
        (maybe if f.severity == "borderline" else sure).append(_finding(f, counts[f.kind.value]))
    for m in maybe:  # worth asking: no sum, outside the total
        m["amount"] = None
    nm_count: dict[str, int] = {}
    no_money = []
    for f in report.no_money:
        nm_count[f.kind.value] = nm_count.get(f.kind.value, 0) + 1
        no_money.append(_finding(f, nm_count[f.kind.value]))
    return {"case_id": case_id, "replayed": info.get("replayed", True), **_summary(case, report),
            "findings": sure, "worth_asking": maybe, "no_money_here": no_money, "note": DISCLAIMER}


def get_protections(case_id: str) -> dict:
    _, case, _, _ = _load(case_id)
    return {"case_id": case_id, **_jsonable(laws.for_case(case)), "note": DISCLAIMER}


def _letters(case: Case, report: checks.Report) -> list[writer.Letter]:
    return writer.all_letters(case, report) + writer.appeal_letters(case)


def list_letters(case_id: str) -> dict:
    _, case, report, _ = _load(case_id)
    return {"case_id": case_id, "letters": [{"kind": x.kind, "subject": x.subject, "to": x.to}
                                            for x in _letters(case, report)], "note": DISCLAIMER}


def draft_letter(case_id: str, kind: str, lang: str = "en") -> dict:
    """The letter in English (the one the family signs and sends) and, if asked, a translation to read."""
    _, case, report, _ = _load(case_id)
    i18n.check(lang)
    letter = next((x for x in _letters(case, report) if x.kind == kind), None)
    if letter is None:
        raise AgentError(f"no letter {kind!r} in this case; see list_letters")
    out = {"case_id": case_id, "kind": kind, "subject": letter.subject, "to": letter.to,
           "text": writer.plain_text(letter, case), "sources": writer.sources(letter),
           "left_out": letter.dropped, "note": DISCLAIMER}
    if lang != "en":
        try:
            tr = i18n.letter(providers.make_client(), case, letter, lang)
            out["translation"] = {"lang": lang, "text": i18n.plain_text(tr), "kept_in_english": tr.failed}
        except ReplayMissing:
            out["translation"] = {"lang": lang, "text": None,
                                  "why": "translation needs a live model run; the English letter is above"}
    return out


def get_call_sheet(case_id: str, to: str = "hospital") -> dict:
    _, case, report, _ = _load(case_id)
    if to not in ("hospital", "insurer"):
        raise AgentError("to must be 'hospital' or 'insurer'")
    sheet = callprep.build(case, report, to)
    if sheet is None:
        raise AgentError("no denied claim in this case, so no call to the insurer")
    data = _jsonable(sheet)
    for o in data["objections"]:
        o["sources"] = _sources(o["refs"])
    return {"case_id": case_id, **data, "note": DISCLAIMER}


def get_deadlines(case_id: str) -> dict:
    _, case, report, _ = _load(case_id)
    out = []
    f2 = next((f for f in report.findings if f.kind == checks.Kind.F2), None)
    if f2 and f2.deadline:
        out.append({"what": "Apply for hospital financial assistance", "by": f2.deadline.isoformat(),
                    "sources": _sources(f2.policy_refs)})
    for d in appeal.denials(case):
        dl = appeal.deadline(plans.load(d.plan_id), d)
        if dl:
            out.append({"what": f"Appeal denied claim {d.claim_ref or ''}".strip(), "by": dl.day.isoformat(),
                        "sources": _sources(dl.refs)})
    for card in laws.for_case(case).cards:
        if card.deadline:
            out.append({"what": card.deadline_label or card.title, "by": card.deadline.isoformat(),
                        "sources": [{"ref": q.ref, "title": q.title, "quote": q.text, "url": q.url}
                                    for q in card.quotes]})
    out.sort(key=lambda x: x["by"])
    return {"case_id": case_id, "today": case.today.isoformat(), "deadlines": out, "note": DISCLAIMER}


def get_report_pdf(case_id: str, lang: str = "en") -> dict:
    _, case, report, info = _load(case_id)
    i18n.check(lang)
    pdf = report_pdf.build(case, report, lang=lang, replayed=info.get("replayed", True))
    return {"case_id": case_id, "filename": f"billradar-{case_id}-{lang}.pdf", "mime_type": "application/pdf",
            "base64": base64.b64encode(pdf).decode(), "note": DISCLAIMER}


# --- a hospital's own policy (policy_finder) ------------------------------------------------------

def find_policy(hospital: str, state: str, city: str = "", zip: str = "", website: str = "",
                confirmed: bool = False) -> dict:
    """The financial assistance policy of a hospital, searched with Tavily only on the hospital's own site.
    Only these five fields are taken, so nothing from a bill can reach the search. Replays the recorded run
    unless the live switches are on (BILLRADAR_LIVE=1, BILLRADAR_AGENT_LIVE=1, a Tavily key) and the caller holds
    an API key."""
    from billradar import nemotron_client as nc
    from billradar import policy_finder, tavily_client as tc
    hospital, state, city, zip, website = (str(x or "").strip() for x in (hospital, state, city, zip, website))
    if not hospital or len(hospital) > 120:
        raise AgentError("hospital: the hospital's name as printed on the bill (up to 120 characters)")
    if not re.fullmatch(r"[A-Za-z]{2}", state):
        raise AgentError("state: two letters, e.g. TX")
    if zip and not re.fullmatch(r"\d{5}(-\d{4})?", zip):
        raise AgentError("zip: five digits")
    if len(city) > 60 or len(website) > 200:
        raise AgentError("city up to 60 characters, website up to 200")
    live = (policy_finder.enabled() and os.environ.get("BILLRADAR_LIVE") == "1"
            and os.environ.get("BILLRADAR_AGENT_LIVE") == "1" and bool(os.environ.get("TAVILY_API_KEY"))
            and authorized.get())  # flag off: the recorded run only, never a live search
    tavily = tc.TavilyClient() if live else tc.TavilyClient(transport=tc.ReplayTransport(), record_to=None, journal=None)
    nemotron = None if live else nc.NemotronClient(transport=nc.ReplayTransport(), record_to=None, journal=None)
    hint = policy_finder.Hint(name=hospital, city=city, zip=zip, state=state.upper(), website=website)
    found = policy_finder.find(hint, tavily, nemotron, confirmed=bool(confirmed and website)).to_dict()
    if not live and found["status"] == "error" and "no Tavily recording" in found["message"]:
        found["message"] = ("this hospital is not in the recorded run and live search is off here; recorded: "
                            + "; ".join(sorted({r["hint"]["name"] for r in policy_finder.run_plan()["runs"]})))
    return {**found, "replayed": not live, "note": f"{found['note']} {DISCLAIMER}"}
