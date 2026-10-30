"""Web app: upload the pile -> run the case -> the case screen.

uvicorn billradar.app:app --reload
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from billradar import links
from billradar import policy_finder
from billradar import (agent_entry, appeal, ask, callprep, checks, context, example, forms, history, i18n, laws, pipeline, plans,
                       policy, providers, report_pdf, view, voice, writer)
from billradar.case import Case, Person, Profile, Role
from billradar.model_api import ModelError, ReplayMissing

WEB = Path(__file__).parent / "web"
RUNS = Path(__file__).resolve().parents[1] / "runs"

# Entry for AI agents (MCP, A2A): mounted last, so every route below wins; its lifespan runs exactly once.
agent = agent_entry.app()


@asynccontextmanager
async def lifespan(_app):
    async with agent.app.router.lifespan_context(agent.app):
        yield


app = FastAPI(title="BillRadar", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
templates = Jinja2Templates(directory=WEB / "templates")
templates.env.filters["quotes"] = lambda t: view.mark_quotes(t)
templates.env.filters["letter_name"] = lambda k: view.letter_name(k)


def _run_dir(case_id: str) -> Path:
    if case_id in example.READ_ONLY:
        example.ensure(RUNS)
    d = history.run_dir(RUNS, case_id)
    if d is None:
        raise HTTPException(404)
    return d


def _run_info(d: Path) -> dict:
    return json.loads((d / "problems.json").read_text()) if (d / "problems.json").exists() else {}


def _sent(case_id: str) -> list[str]:
    """What left the device, as the models got it. Only runs in private mode have it."""
    d = history.run_dir(RUNS, case_id)
    return [f"/cases/{case_id}/overlay/{p.name}" for p in sorted(d.glob("sent-*.jpg"))] if d else []


STATIC = os.environ.get("BILLRADAR_STATIC") == "1"
CSS_V = hashlib.sha256((WEB / "static" / "app.css").read_bytes()).hexdigest()[:10]  # a new stylesheet gets a new address


def asset(path: str) -> str:
    """/static/brand/x.png -> /static/brand/x.png?v=<hash>: a changed picture gets a new address (no stale cache)."""
    p = WEB / path.lstrip("/")
    return f"{path}?v={hashlib.sha256(p.read_bytes()).hexdigest()[:10]}" if p.exists() else path


templates.env.globals["asset"] = asset
templates.env.globals["repo_tree"] = links.tree()  # the repo link on every page, from one constant
templates.env.globals["repo_blob"] = links.blob
REPO_URL = links.tree()


def _page(request: Request, name: str, **ctx):
    ctx.setdefault("nav", "")
    ctx.setdefault("mdl", providers.labels())
    ctx.setdefault("public", history.public())
    ctx.setdefault("static", STATIC)  # a read-only copy of the example pages (scripts/export_static.py)
    ctx.setdefault("css_v", CSS_V)
    return templates.TemplateResponse(request, name, ctx)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    example.ensure(RUNS)
    sheets = [f"/cases/{example.EXAMPLE_ID}/sheet/{s}.jpg" for s in ("S4", "S1", "S12")]
    d, case, report = _load(example.EXAMPLE_ID)
    letter = next((x for x in writer.all_letters(case, report) if x.kind == "itemized"), None)
    story = view.story(case, report, letter, [f"/cases/{example.EXAMPLE_ID}/sheet/S{i}.jpg" for i in (1, 2, 4, 5, 8)])  # the five fullest sheets
    found = WEB / "static" / "story" / "found.json"  # scripts/story_assets.py
    if found.exists():
        story["found"] = view.story_found(json.loads(found.read_text()), lambda s: f"/cases/{example.EXAMPLE_ID}/sheet/{s}.jpg")
    receipt = d / "receipt.json"
    if receipt.exists():
        r = json.loads(receipt.read_text())
        story["receipt"] = {"calls": len(r["rows"]), "usd": round(r["total_usd"], 3)}
    return _page(request, "home.html", nav="home", sheets=sheets, steps=view.steps_static(), story=story)


def _upload_page(request: Request, status: int = 200, error: str = "", chosen: list[str] | None = None):
    rules = {c.id: context.rules_on([c.id]) for c in context.CHIPS}
    r = _page(request, "upload.html", nav="upload", error=error, live=providers.make_client().live, chips=context.CHIPS,
              chosen=context.valid(chosen or []), rules=rules,
              policies=[policy.load(p) for p in policy.available() if policy.load(p).tiers])
    r.status_code = status
    return r


@app.get("/upload", response_class=HTMLResponse)
def upload(request: Request, ctx: list[str] = Query(default=[], alias="context")):
    return _upload_page(request, chosen=ctx)


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    example.ensure(RUNS)
    ex = f"/cases/{example.EXAMPLE_ID}"
    art = {"photo": f"{ex}/overlay/overlay-1.jpg", "sheets": [f"{ex}/sheet/S1.jpg", f"{ex}/sheet/S2.jpg"],
           "check": f"{ex}/sheet/S10.jpg", "covered": f"/cases/{example.PRIVATE_ID}/sheet/S4.jpg"}
    return _page(request, "how.html", nav="how", models=view.model_status(), art=art)


@app.get("/safe", response_class=HTMLResponse)
def safe(request: Request, case: str = ""):
    example.ensure(RUNS)
    own = history.run_dir(RUNS, case) if case else None
    sent = (_sent(case) if own else []) or _sent(example.PRIVATE_ID)
    deletable = bool(own) and history.may_delete(own, request.cookies.get(history.cookie_name(case)))
    pair_case = case if own and _sent(case) else example.PRIVATE_ID
    d = history.run_dir(RUNS, pair_case)
    pair = (f"/cases/{pair_case}/sheet/S1.jpg", f"/cases/{pair_case}/sent-sheet/S1.jpg") \
        if d and (d / "sent-sheets" / "S1.jpg").exists() else None
    return _page(request, "safe.html", nav="safe", sent=sent, sent_is_example=not (own and _sent(case)),
                 case_id=case if own else "", deletable=deletable, pair=pair)


@app.get("/agents", response_class=HTMLResponse)
def for_agents(request: Request):
    base = "http://127.0.0.1:8000" if STATIC else str(request.base_url).rstrip("/")  # the read-only copy has no server
    return _page(request, "agents.html", nav="agents", base=base, packs=sorted(
        p.name for p in (example.ROOT / "eval" / "packs" / "work").iterdir() if (p / "profile.json").exists()))


@app.get("/cases", response_class=HTMLResponse)
def all_cases(request: Request, deleted: str = ""):
    rows = [] if history.public() else history.list_runs(RUNS)
    return _page(request, "cases.html", nav="cases", rows=rows, deleted=deleted)


@app.get("/cases/{case_id}/summary.json")
def case_summary(case_id: str):
    return history.summary(_run_dir(case_id)).to_dict()


@app.post("/cases/{case_id}/delete")
def delete_case(request: Request, case_id: str):
    d = _run_dir(case_id)
    if not history.delete(RUNS, case_id, request.cookies.get(history.cookie_name(case_id))):
        raise HTTPException(403, "this case can only be deleted from the browser that made it")
    r = RedirectResponse(f"/cases?deleted={d.name}", status_code=303)
    r.delete_cookie(history.cookie_name(case_id), path=f"/cases/{case_id}")
    return r


@app.post("/cases")
async def create_case(
    request: Request,
    photos: list[UploadFile] = File(default=[]),
    files: list[UploadFile] = File(default=[]),
    policy_id: str = Form(...),
    letterhead: str = Form(""),
    household_size: int = Form(...),
    annual_income: Decimal = Form(...),
    liquid_assets: Decimal | None = Form(None),
    insured: bool = Form(False),
    fictional: bool = Form(False),
    mother_name: str = Form(""),
    baby_name: str = Form(""),
    ctx: list[str] = Form(default=[], alias="context"),
):
    if policy_id not in policy.available():
        raise HTTPException(400, "unknown policy")
    people = [Person(token="PATIENT_1", role=Role.MOTHER, name=mother_name)]
    if baby_name:
        people.append(Person(token="PATIENT_2", role=Role.NEWBORN, name=baby_name))
    prof = Profile(household_size=household_size, annual_income=annual_income, liquid_assets=liquid_assets,
                   insured=insured, people=people, context=context.valid(ctx))
    photo_bytes = [await p.read() for p in photos if p.filename]
    file_items = [(f.filename or "file.pdf", await f.read()) for f in files if f.filename]
    case_id = "c" + secrets.token_hex(5)
    try:
        result = pipeline.run(providers.make_client(), photo_bytes, file_items, prof, policy_id, letterhead, date.today(), case_id,
                              fictional=fictional)
    except ReplayMissing:  # no live model calls here yet, and nothing recorded for these papers: say so, never fake it
        return _upload_page(request, 503, "Checking new papers starts when the model calls are switched on. "
                            "Until then, the example case shows the whole path with sample papers.")
    except pipeline.RealDataGate as e:
        return _upload_page(request, 400, f"Not sent: {e}.")
    result.case.demo = fictional
    pipeline.save(result, RUNS / case_id)
    token = history.issue_token(RUNS / case_id)
    r = RedirectResponse(f"/cases/{case_id}", status_code=303)
    r.set_cookie(history.cookie_name(case_id), token, path=f"/cases/{case_id}", httponly=True, samesite="strict",
                 secure=history.public(), max_age=60 * 60 * 24 * 365)
    return r


def _sent_sheets(case_id: str) -> list[str]:
    """Private mode: each sheet as the model got it, covered, in sheet order."""
    d = history.run_dir(RUNS, case_id)
    files = sorted((d / "sent-sheets").glob("S*.jpg"), key=lambda p: int(p.stem[1:])) if d else []
    return [f"/cases/{case_id}/sent-sheet/{p.name}" for p in files]


def _receipt(d: Path) -> dict | None:
    p = d / "receipt.json"
    return json.loads(p.read_text()) if p.exists() else None


def _load(case_id: str) -> tuple[Path, Case, checks.Report]:
    d = _run_dir(case_id)
    case = Case.model_validate_json((d / "case.json").read_text())
    report = checks.Report.model_validate_json((d / "report.json").read_text())
    return d, case, report


FORM_FOR_POLICY = {"memorial-2026-05": forms.fill_memorial}


def _case_page(request: Request, case_id: str, is_example: bool = False):
    d, case, report = _load(case_id)
    info = _run_info(d)
    overlays = [f"/cases/{case_id}/overlay/{p.name}" for p in sorted(d.glob("overlay-*.jpg"))]
    sheet_ids = sorted((p.stem for p in (d / "sheets").glob("S*.jpg")), key=lambda x: int(x[1:]))
    screen = view.build(case, report, overlays, info.get("replayed", True))
    sent = _sent(case_id)
    sent_from_example = False
    if case_id == example.EXAMPLE_ID:  # the same pack in private mode: the photos as they leave the device
        sent, sent_from_example = _sent(example.PRIVATE_ID), True
    return _page(
        request, "case.html", nav="cases", s=screen, case=case, case_id=case_id, report=report,
        board=view.board(case, report, info), steps=view.steps(case, info, len(sheet_ids)),
        span=view.service_span(case), sheets=sheet_ids, letters=writer.all_letters(case, report),
        appeals=writer.appeal_letters(case), calls=callprep.all_sheets(case, report), langs=i18n.LANGS,
        laws=laws.for_case(case), chips=[context.chip(c) for c in context.valid(case.profile.context)],
        fap_policy_by=max((f.deadline for f in report.findings + report.worth_asking if f.kind.value == "F2" and f.deadline),
                          default=None),  # the hospital's own last day to apply, beside the federal minimum
        has_form=case.policy_id in FORM_FOR_POLICY, checker=info.get("checker"), held=len(report.held_back),
        redaction=info.get("redacted", False), sent=sent, sent_from_example=sent_from_example,
        receipt=_receipt(d), receipt_rows=view.receipt_rows(_receipt(d)),
        previews={p.stem for p in (d / "files").glob("D*.jpg")}, sent_sheets=_sent_sheets(
            example.PRIVATE_ID if sent_from_example else case_id),
        example=is_example, created=history.summary(d).created,
        created_on=datetime.fromisoformat(history.summary(d).created).strftime("%b %-d, %Y"),
        amount=view.money(report.total_may_come_off.to_integral_value(rounding=ROUND_DOWN)),
        deletable=history.may_delete(d, request.cookies.get(history.cookie_name(case_id))),
        **_policy_block(d, case_id, is_example))


def _policy_block(d: Path, case_id: str, is_example: bool) -> dict:
    """The case's policy_finder result (policy-finder.json), and where its "yes / type the site" forms post."""
    p = d / "policy-finder.json"
    found = json.loads(p.read_text()) if p.exists() and policy_finder.enabled() else None  # flag off: screen as before
    form = f"/cases/{case_id}/policy-finder" if found and not STATIC and not is_example else ""
    return {"found_policy": found, "pf_form": form}


@app.post("/cases/{case_id}/policy-finder")
def find_policy_for_case(request: Request, case_id: str, website: str = Form(""), place: str = Form(""),
                         state: str = Form(""), confirmed: str = Form("")):
    """The family answers "Is this your hospital?" or types the site / city or ZIP / state from the bill."""
    if not policy_finder.enabled() or case_id in example.READ_ONLY:
        raise HTTPException(404)
    d = _run_dir(case_id)
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:  # a form posted from another site
        raise HTTPException(403)
    if not history.may_delete(d, request.cookies.get(history.cookie_name(case_id))):
        raise HTTPException(403)
    p = d / "policy-finder.json"
    old = json.loads(p.read_text()) if p.exists() else {}
    _, case, _ = _load(case_id)
    hint = policy_finder.hint_for(case) or policy_finder.Hint(name=old.get("hospital", ""))
    if not hint.name:
        raise HTTPException(400, "the bills name no hospital")
    place = " ".join(place.split())
    hint.website = website.strip()
    if place and place[0].isdigit():  # a ZIP replaces the city saved before, and the other way round
        hint.zip, hint.city = place, ""
    elif place:
        hint.city, hint.zip = place, ""
    else:
        hint.city, hint.zip = old.get("city", ""), old.get("zip", "")
    hint.state = state.strip() or old.get("state", "")
    try:
        hint = hint.clean()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    found = policy_finder.find(hint, confirmed=confirmed == "1" and bool(hint.website)).to_dict()
    p.write_text(json.dumps(found, indent=2))
    return RedirectResponse(f"/cases/{case_id}#policy", status_code=303)


@app.get("/policy-finder", response_class=HTMLResponse)
def policy_finder_page(request: Request):
    return _page(request, "policy_finder.html", nav="how", **_recorded_policy_run())


@functools.cache
def _recorded_policy_run() -> dict:
    return policy_finder.replay_run()


@app.get("/cases/{case_id}", response_class=HTMLResponse)
def case_screen(request: Request, case_id: str):
    return _case_page(request, case_id, is_example=case_id in example.READ_ONLY)


@app.get("/example", response_class=HTMLResponse)
def example_case(request: Request):
    return _case_page(request, example.EXAMPLE_ID, is_example=True)


@app.get("/cases/{case_id}/sent-sheet/{name}")
def sent_sheet(case_id: str, name: str):
    if not re.fullmatch(r"S\d{1,3}\.jpg", name):
        raise HTTPException(404)
    p = _run_dir(case_id) / "sent-sheets" / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


@app.get("/cases/{case_id}/file/{name}")
def file_preview(case_id: str, name: str):
    if not re.fullmatch(r"D\d{1,3}\.jpg", name):
        raise HTTPException(404)
    p = _run_dir(case_id) / "files" / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


@app.get("/cases/{case_id}/sheet/{name}")
def sheet(case_id: str, name: str):
    if not re.fullmatch(r"S\d{1,3}\.jpg", name):
        raise HTTPException(404)
    p = _run_dir(case_id) / "sheets" / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/cases/{case_id}/reminder.ics")
def reminder(case_id: str):
    """A calendar file: the last day to apply, with reminders 30 and 7 days before."""
    _, case, report = _load(case_id)
    if not report.apply_by:
        raise HTTPException(404, "no application deadline in this case")
    day = report.apply_by.strftime("%Y%m%d")
    stamp = f"{case.today:%Y%m%d}T000000Z"
    body = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//BillRadar//EN", "BEGIN:VEVENT",
        f"UID:{case_id}-apply-by@billradar", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{day}",
        "SUMMARY:Last day to apply for hospital financial assistance",
        f"DESCRIPTION:BillRadar case {case_id}. Apply before this day. You review and sign every letter.",
        "BEGIN:VALARM", "TRIGGER:-P30D", "ACTION:DISPLAY", "DESCRIPTION:30 days left to apply", "END:VALARM",
        "BEGIN:VALARM", "TRIGGER:-P7D", "ACTION:DISPLAY", "DESCRIPTION:7 days left to apply", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", ""])
    return Response(body, media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="{case_id}-apply-by.ics"'})


def _letters(case: Case, report: checks.Report) -> list[writer.Letter]:
    return writer.all_letters(case, report) + writer.appeal_letters(case)


def _lang(lang: str) -> str:
    if lang not in i18n.LANGS:
        raise HTTPException(400, f"language must be one of {sorted(i18n.LANGS)}")
    return lang


NOT_LIVE = "The translation appears when the model is switched on. Below is the English letter, the one that is sent."


def _letter(case_id: str, kind: str, lang: str):
    """The letter, and its translation when one is asked for and can be had (never a made-up one)."""
    d, case, report = _load(case_id)
    found = next((x for x in _letters(case, report) if x.kind == kind), None)
    if not found:
        raise HTTPException(404)
    tr, note = None, ""
    if _lang(lang) != "en":
        try:
            tr = i18n.letter(providers.make_client(), case, found, lang)
            note = tr.note
        except (ReplayMissing, pipeline.RealDataGate):
            note = NOT_LIVE
    return d, case, report, found, tr, note


@app.get("/cases/{case_id}/letters/{kind}", response_class=HTMLResponse)
def letter(request: Request, case_id: str, kind: str, lang: str = "en"):
    _, case, report, found, tr, note = _letter(case_id, kind, lang)
    chips = [list({c.label: c for c in (view.source_chip(r) for r in dict.fromkeys(p.refs))}.values())
             for p in found.paragraphs]  # two quotes from one statute: one chip
    return _page(request, "letter.html", nav="cases", l=found, case=case, case_id=case_id,
                 sources=writer.sources(found), signature=writer.signature(case), today=case.today,
                 letters=_letters(case, report), chips=chips, apply_by=report.apply_by, lang=lang,
                 langs=i18n.LANGS, tr=tr, note=note, appeal=view.appeal_panel(case, found),
                 calls=callprep.all_sheets(case, report),
                 greeting=writer.greeting(found),
                 demo_rules=writer.DEMO_RULES if case.demo and case.policy_id.startswith("memorial") else "")


@app.get("/cases/{case_id}/letters/{kind}/text")
def letter_text(case_id: str, kind: str, lang: str = "en"):
    _, case, _, found, tr, _ = _letter(case_id, kind, lang)
    text = i18n.plain_text(tr) if tr else writer.plain_text(found, case)
    return Response(text, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{case_id}-{kind}-{lang}.txt"'})


@app.get("/cases/{case_id}/letters/{kind}/pdf")
def letter_pdf(case_id: str, kind: str, lang: str = "en"):
    _, case, _, found, tr, _ = _letter(case_id, kind, lang)
    return Response(report_pdf.letter_pdf(case, found, tr), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{case_id}-{kind}.pdf"'})


@app.get("/cases/{case_id}/report.pdf")
def report_as_pdf(case_id: str, lang: str = "en"):
    d, case, report = _load(case_id)
    pdf = report_pdf.build(case, report, lang=_lang(lang), replayed=_run_info(d).get("replayed", True))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{case_id}-report-{lang}.pdf"'})


@app.get("/cases/{case_id}/call/{to}", response_class=HTMLResponse)
def call_sheet(request: Request, case_id: str, to: str):
    d, case, report = _load(case_id)
    if to not in ("hospital", "insurer"):
        raise HTTPException(404)
    sheet = callprep.build(case, report, to)
    if sheet is None:
        raise HTTPException(404, "nothing to call about here")
    answers = [[view.source_chip(r) for r in dict.fromkeys(o.refs)] for o in sheet.objections]
    return _page(request, "call.html", nav="cases", case=case, case_id=case_id, sheet=sheet, answers=answers,
                 sheets=callprep.all_sheets(case, report), voice_langs=voice.LANGUAGES, span=view.service_span(case),
                 example=case_id in example.READ_ONLY)


@app.post("/cases/{case_id}/call/{to}/note")
async def call_note(case_id: str, to: str, request: Request, lang: str = "en"):
    """Her voice note after the call -> who she spoke with, the reference number, what was promised -> a paragraph."""
    d, case, report = _load(case_id)
    sheet = callprep.build(case, report, to) if to in ("hospital", "insurer") else None
    if sheet is None:
        raise HTTPException(404)
    if lang not in voice.LANGUAGES:
        raise HTTPException(400, "voice notes: English or Spanish")
    wav = await request.body()
    try:
        tx = voice.transcribe(providers.make_client(), case, wav, lang)
        note = voice.read_note(providers.make_client(), case, tx, who=sheet.who)
    except ReplayMissing:
        return JSONResponse({"error": "Voice notes start when the model is switched on."}, status_code=503)
    except pipeline.RealDataGate as e:
        return JSONResponse({"error": f"Not sent: {e}."}, status_code=400)
    except (ValueError, ModelError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    para = note.paragraph(case, lang)
    return {"transcript": tx.text, "replayed": tx.replayed and note.replayed, "rep_name": note.rep_name,
            "reference": note.reference, "promises": [{"words": w, "english": e} for w, e in note.promises],
            "paragraph": para.text if para else ""}


def _today(case_id: str, case: Case) -> date:
    return case.today if case_id in example.READ_ONLY else date.today()


def _tracker(d: Path, case: Case, report: checks.Report, today: date) -> appeal.Tracker:
    tr = appeal.Tracker.load(d)
    for letter_ in _letters(case, report):
        tr.prepare(case, letter_, today)
    return tr


def _owner_only(request: Request, d: Path) -> None:
    if not history.may_delete(d, request.cookies.get(history.cookie_name(d.name))):
        raise HTTPException(403, "only the browser that made this case can change it; the examples are read-only")


@app.post("/cases/{case_id}/ask")
async def ask_case(case_id: str, request: Request):
    """A question about this case; answered only with checked quotes from her sheets or the policy."""
    _, case, report = _load(case_id)
    q = ((await request.json()).get("question") or "").strip()[:500]
    if not q:
        raise HTTPException(400, "empty question")
    try:
        a = ask.answer(providers.make_client(), case, report, q)
    except ReplayMissing:
        a = ask.Answer("No answer yet: the model is not connected.", refused=True, reason="no_model")
    except pipeline.RealDataGate as e:
        return JSONResponse({"error": f"Not sent: {e}."}, status_code=400)
    cites = []
    for c in a.citations:
        if c.source == "sheet":
            doc = c.ref.split("#", 1)[0]
            cites.append({"kind": "sheet", "label": f"{'Sheet' if doc.startswith('S') else 'File'} {doc[1:]}", "quote": c.quote})
        else:
            chip = view.source_chip(c.ref)
            cites.append({"kind": "policy", "label": chip.label, "quote": c.quote, "url": c.url})
    return {"text": a.text, "refused": a.refused, "reason": a.reason, "citations": cites, "replayed": a.replayed}


@app.get("/cases/{case_id}/appeals", response_class=HTMLResponse)
def appeals(request: Request, case_id: str, key: str = "", msg: str = ""):
    d, case, report = _load(case_id)
    today = _today(case_id, case)
    tr = _tracker(d, case, report, today)
    rows = [tr.status_on(k, today) | {"to": it.to, "reply": it.reply, "appeal_by_on": it.appeal_by,
                                       "sent_on": it.sent_on} for k, it in tr.items.items()]
    rows.sort(key=lambda r: (not r["key"].startswith("appeal-"), r["key"]))
    current = next((r for r in rows if r["key"] == key), rows[0] if rows else None)
    has_dates = any(it.sent_on or it.decide_by or (it.appeal_by and not it.sent_on) for it in tr.items.values())
    return _page(request, "appeals.html", nav="cases", case=case, case_id=case_id, rows=rows, current=current,
                 has_dates=has_dates,
                 steps=appeal.STATUSES, msg=msg, today=today,
                 can_change=history.may_delete(d, request.cookies.get(history.cookie_name(case_id))))


@app.post("/cases/{case_id}/appeals/{key}/sent")
def appeal_sent(request: Request, case_id: str, key: str, on: date = Form(...), confirmation: str = Form("")):
    d, case, report = _load(case_id)
    _owner_only(request, d)
    tr = _tracker(d, case, report, _today(case_id, case))
    if key not in tr.items:
        raise HTTPException(404)
    tr.mark_sent(case, key, on, confirmation.strip()[:80])
    tr.save(d)
    return RedirectResponse(f"/cases/{case_id}/appeals?key={key}", status_code=303)


@app.post("/cases/{case_id}/appeals/{key}/reply")
async def appeal_reply(request: Request, case_id: str, key: str, photo: UploadFile = File(...)):
    d, case, report = _load(case_id)
    _owner_only(request, d)
    tr = _tracker(d, case, report, _today(case_id, case))
    if key not in tr.items:
        raise HTTPException(404)
    plan_id = next((x.plan_id for x in appeal.denials(case) if appeal.letter_kind(x) == key), None)
    try:
        reply = appeal.read_reply(providers.make_client(), case, await photo.read(), plans.load(plan_id) if plan_id else None)
    except ReplayMissing:
        return RedirectResponse(f"/cases/{case_id}/appeals?key={key}&msg=not-live", status_code=303)
    except pipeline.RealDataGate:
        return RedirectResponse(f"/cases/{case_id}/appeals?key={key}&msg=gate", status_code=303)
    tr.record_reply(key, reply, _today(case_id, case))
    tr.save(d)
    return RedirectResponse(f"/cases/{case_id}/appeals?key={key}", status_code=303)


@app.get("/cases/{case_id}/appeals.ics")
def appeals_ics(case_id: str):
    d, case, report = _load(case_id)
    tr = _tracker(d, case, report, _today(case_id, case))
    return Response(appeal.reminders_ics(tr, case_id), media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="{case_id}-reminders.ics"'})


@app.get("/cases/{case_id}/form.pdf")
def form_pdf(case_id: str):
    _, case, _ = _load(case_id)
    fill = FORM_FOR_POLICY.get(case.policy_id)
    if not fill:
        raise HTTPException(404, "no form for this policy yet")
    return Response(fill(case).pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{case_id}-financial-assistance-form.pdf"'})


@app.get("/cases/{case_id}/overlay/{name}")
def overlay(case_id: str, name: str):
    if not re.fullmatch(r"(overlay|sent)-\d+\.jpg", name):
        raise HTTPException(404)
    return FileResponse(_run_dir(case_id) / name)


@app.get("/cases/{case_id}/report.json")
def report_json(case_id: str):
    return FileResponse(_run_dir(case_id) / "report.json")


app.mount("/", agent)  # last: /mcp, /a2a, /.well-known/agent-card.json, /agents/healthz
