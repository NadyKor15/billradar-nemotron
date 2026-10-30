"""Insurance appeals: which claims were denied, by when to appeal, what goes on the insurer's form.

A denial is a line on an insurance statement (EOB) whose remark code the plan document
itself lists as a denial. A code we cannot find in the plan's own legend is not treated
as a denial: we would have nothing to quote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from billradar import plans
from billradar.case import Case, Document, Line


FEDERAL_APPEAL_REF = "erisa-2560-503-1#appeal-180-days"  # 29 CFR 2560.503-1(h)(3)(i), employer plans


def _law_verified(ref: str) -> bool:
    from billradar import policy
    try:
        return policy.verified(ref)
    except (FileNotFoundError, KeyError, ValueError):
        return False


@dataclass
class Denial:
    plan_id: str
    eob_id: str  # the sheet or file the denial is printed on
    claim_ref: str
    patient: str  # token
    member_ref: str  # token or as printed
    codes: list[str]
    line_nos: list[int]
    description: str
    code: str  # service or revenue code of the first denied line
    service_from: date | None
    service_to: date | None
    patient_responsibility: Decimal | None  # what the EOB leaves for the family on the denied lines
    denial_date: date | None  # the EOB's own date; the notice reaches the family later, so a deadline
    #                           counted from it is never later than the real one

    @property
    def key(self) -> str:
        return f"{self.eob_id}:{self.claim_ref or self.line_nos[0]}"


def _codes(line: Line) -> list[str]:
    return [c.strip().upper() for c in line.denial_codes.replace(";", ",").split(",") if c.strip()]


def denials(case: Case) -> list[Denial]:
    out: list[Denial] = []
    for eob in case.eobs():
        plan_id = plans.for_insurer(eob.provider)
        if not plan_id:
            continue
        plan = plans.load(plan_id)
        groups: dict[str, list[tuple[Line, list[str]]]] = {}
        for ln in eob.lines:
            denied = [c for c in _codes(ln) if plan.is_denial(c)]
            if denied:
                groups.setdefault(ln.claim_ref or eob.claim_ref or f"line-{ln.line_no}", []).append((ln, denied))
        for claim, items in groups.items():
            lines = [ln for ln, _ in items]
            owed = [ln.patient_responsibility for ln in lines if ln.patient_responsibility is not None]
            dates = [ln.service_date for ln in lines if ln.service_date]
            out.append(Denial(
                plan_id=plan_id, eob_id=eob.doc_id, claim_ref=claim if not claim.startswith("line-") else "",
                patient=eob.patient, member_ref=eob.member_ref,
                codes=list(dict.fromkeys(c for _, cs in items for c in cs)),
                line_nos=[ln.line_no for ln in lines], description=lines[0].description, code=lines[0].code,
                service_from=min(dates) if dates else eob.service_from,
                service_to=max(dates) if dates else eob.service_to,
                patient_responsibility=sum(owed, Decimal(0)) if owed else None,
                denial_date=eob.statement_date))
    return out


def letter_kind(d: Denial) -> str:
    """The appeal letter's key, the same in the letter, the tracker and the screen."""
    return f"appeal-{d.eob_id}-{d.claim_ref or d.line_nos[0]}".lower()


@dataclass
class Deadline:
    day: date
    refs: list[str] = field(default_factory=list)
    math: dict[str, str] = field(default_factory=dict)


def deadline(plan: plans.Plan, denial: Denial) -> Deadline | None:
    """The last day to appeal: the plan's own window, and the federal one when its law is in the base.
    The later of the two, since each gives at least that much time."""
    if not denial.denial_date:
        return None
    options = {"plan": (denial.denial_date + timedelta(days=plan.appeal_days),
                        [plan.ref(plan.appeal_days_fragment)])}
    if plan.plan_type == "employer" and _law_verified(FEDERAL_APPEAL_REF):
        try:  # the federal rule's day count lives with the law base (pack B), not here
            from billradar import laws  # type: ignore[attr-defined]
            options["federal"] = (laws.appeal_deadline(denial.denial_date), [FEDERAL_APPEAL_REF])
        except (ImportError, AttributeError):
            pass
    which = max(options, key=lambda k: options[k][0])
    day, refs = options[which]
    return Deadline(day, refs, {k: v[0].isoformat() for k, v in options.items()}
                    | {"from": denial.denial_date.isoformat(), "used": which})


def token_value(case: Case, ref: str, fallback: str) -> str:
    """'[ACCT_1]' -> the real value kept on this device; a printed value stays as it is."""
    ref = (ref or "").strip()
    if ref.startswith("[") and ref.endswith("]"):
        return case.profile.local.tokens.get(ref[1:-1], fallback)
    return ref or fallback


def norm_token(token: str) -> str:
    """'[PATIENT_1]', 'PATIENT 1', 'patient_1' -> 'PATIENT_1': a model may copy a token with a space or brackets."""
    return "_".join((token or "").strip().strip("[]").upper().split())


def person_name(case: Case, token: str, fallback: str) -> str:
    t = norm_token(token)
    return next((p.name for p in case.profile.people if norm_token(p.token) == t and p.name), fallback)


def _date(d: date | None) -> str:
    return f"{d:%m/%d/%Y}" if d else ""


def form_values(case: Case, plan: plans.Plan, denial: Denial) -> dict[str, str]:
    """What goes on the insurer's member appeal form. Empty means: the family writes it."""
    eob: Document = case.doc(denial.eob_id)
    loc = case.profile.local
    dl = deadline(plan, denial)
    span = _date(denial.service_from) + (f" - {_date(denial.service_to)}"
                                         if denial.service_to and denial.service_to != denial.service_from else "")
    return {
        "patient_name": person_name(case, denial.patient, ""),
        "member_id": token_value(case, denial.member_ref, ""),
        "claim_number": denial.claim_ref,
        "service_dates": span,
        "provider": case.letterhead if case.letterhead else "",
        "denial_date": _date(denial.denial_date),
        "denial_codes": ", ".join(denial.codes),
        "amount": f"{denial.patient_responsibility:,.2f}" if denial.patient_responsibility is not None else "",
        "reason": "Please see the attached appeal letter. It quotes the plan document for each point.",
        "street": loc.street, "city": loc.city, "state": loc.state, "zip": loc.zip, "phone": loc.phone,
        "attachments": f"Appeal letter; Explanation of Benefits ({eob.doc_id}); hospital bill",
        "appeal_by": _date(dl.day) if dl else "",
    }


# --- after "Send": a tracker for every letter and appeal ------------------------------------
# prepared -> sent -> waiting -> reply -> next. Kept on the device next to the case (tracker.json).

import io  # noqa: E402
import json  # noqa: E402
from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402

STATUSES = ["prepared", "sent", "waiting", "reply", "next"]
CHECK_IN_DAYS = 30  # our own reminder to check on a letter; not a legal deadline


@dataclass
class Item:
    key: str  # letter.kind
    title: str
    to: str
    status: str = "prepared"
    sent_on: date | None = None
    confirmation: str = ""
    appeal_by: date | None = None  # last day to appeal (appeals only), from a verified quote
    appeal_by_refs: list[str] = field(default_factory=list)
    decide_by: date | None = None  # when the plan says it decides, counted from the day it was sent
    decide_by_refs: list[str] = field(default_factory=list)
    reply: dict | None = None
    history: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        d = self.__dict__.copy()
        for k in ("sent_on", "appeal_by", "decide_by"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def from_json(cls, d: dict) -> Item:
        d = dict(d)
        for k in ("sent_on", "appeal_by", "decide_by"):
            d[k] = date.fromisoformat(d[k]) if d.get(k) else None
        return cls(**d)


@dataclass
class Tracker:
    items: dict[str, Item] = field(default_factory=dict)

    FILE = "tracker.json"

    @classmethod
    def load(cls, run_dir: Path) -> Tracker:
        p = Path(run_dir) / cls.FILE
        if not p.exists():
            return cls()
        return cls({k: Item.from_json(v) for k, v in json.loads(p.read_text()).items()})

    def save(self, run_dir: Path) -> None:
        (Path(run_dir) / self.FILE).write_text(json.dumps({k: v.to_json() for k, v in self.items.items()}, indent=1))

    def _log(self, item: Item, status: str, on: date, note: str = "") -> None:
        item.status = status
        item.history.append({"status": status, "on": on.isoformat(), "note": note})

    def prepare(self, case: Case, letter, on: date) -> Item:
        """Add a letter or appeal as 'prepared' (once). Appeals carry their last day to appeal."""
        if letter.kind in self.items:
            return self.items[letter.kind]
        item = Item(letter.kind, letter.subject, letter.to)
        d = next((x for x in denials(case) if letter.kind == letter_kind(x)), None)
        if d:
            dl = deadline(plans.load(d.plan_id), d)
            if dl:
                item.appeal_by, item.appeal_by_refs = dl.day, dl.refs
        self.items[letter.kind] = item
        self._log(item, "prepared", on)
        return item

    def mark_sent(self, case: Case, key: str, on: date, confirmation: str = "", note: str = "") -> Item:
        item = self.items[key]
        item.sent_on, item.confirmation = on, confirmation
        if key.startswith("appeal-"):
            plan_id = next((d.plan_id for d in denials(case)), None)
            plan = plans.load(plan_id) if plan_id else None
            if plan and plan.decide_days_post_service and plan.decide_days_fragment:
                item.decide_by = on + timedelta(days=plan.decide_days_post_service)
                item.decide_by_refs = [plan.ref(plan.decide_days_fragment)]
        self._log(item, "sent", on, note or (f"confirmation {confirmation}" if confirmation else ""))
        return item

    def record_reply(self, key: str, reply: Reply, on: date) -> Item:
        item = self.items[key]
        item.reply = reply.to_json()
        self._log(item, "reply", on, reply.decision)
        if reply.next_step:
            self._log(item, "next", on, reply.next_step)
        return item

    def status_on(self, key: str, today: date) -> dict:
        """What the screen shows for one item today: status, and for a sent one 'day N of 30'."""
        item = self.items[key]
        status = item.status
        out = {"key": key, "title": item.title, "status": status, "confirmation": item.confirmation}
        if status == "sent" and item.sent_on and today > item.sent_on:
            out["status"] = "waiting"
            out["day"] = (today - item.sent_on).days
            out["of"] = CHECK_IN_DAYS
        if item.appeal_by and status == "prepared":
            out["appeal_by"] = item.appeal_by.isoformat()
            out["days_left"] = (item.appeal_by - today).days
        if item.decide_by:
            out["decide_by"] = item.decide_by.isoformat()
        return out


def reminders_ics(tracker: Tracker, case_id: str) -> str:
    """Calendar file: a check-in 30 days after each letter was sent, the plan's decision date for an
    appeal, and the last day to appeal for one not sent yet (alarms 30 and 7 days before)."""
    events = []

    def event(uid: str, day: date, summary: str, desc: str, alarms: tuple[int, ...] = ()) -> list[str]:
        ev = ["BEGIN:VEVENT", f"UID:{case_id}-{uid}@billradar", f"DTSTAMP:{day:%Y%m%d}T000000Z",
              f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"SUMMARY:{summary}", f"DESCRIPTION:{desc}"]
        for n in alarms:
            ev += ["BEGIN:VALARM", f"TRIGGER:-P{n}D", "ACTION:DISPLAY", f"DESCRIPTION:{summary} in {n} days",
                   "END:VALARM"]
        return ev + ["END:VEVENT"]

    for key, it in tracker.items.items():
        if it.sent_on:
            events += event(f"{key}-check", it.sent_on + timedelta(days=CHECK_IN_DAYS),
                            f"Check on: {it.title}", "BillRadar reminder to check for an answer. Not a legal deadline.")
        if it.decide_by:
            events += event(f"{key}-decide", it.decide_by, f"Plan should have decided: {it.title}",
                            "Your plan document says when it decides an appeal.")
        if it.appeal_by and not it.sent_on:
            events += event(f"{key}-appeal-by", it.appeal_by, f"Last day to appeal: {it.title}",
                            "From your plan document. You review and sign every letter.", (30, 7))
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//BillRadar//EN"] + events
                       + ["END:VCALENDAR", ""])


# --- reading the insurer's reply from a photo -------------------------------------------------

REPLY_MODEL = "reader"
DECISIONS = ["approved", "partly_approved", "denied", "asks_for_information", "unclear"]
_s = {"type": "string"}
REPLY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["decision", "decision_quote", "claim_ref", "amount_you_owe", "date", "next_level_quote",
                 "asks_for_quote"],
    "properties": {"decision": {"type": "string", "enum": DECISIONS}, "decision_quote": _s, "claim_ref": _s,
                   "amount_you_owe": _s, "date": _s, "next_level_quote": _s, "asks_for_quote": _s},
}
REPLY_INSTRUCTIONS = """You read a photo of a letter an insurance company sent about an appeal, for BillRadar, a helper for hospital bills and insurance. You never give medical advice.

Return:
- decision: approved, partly_approved, denied, asks_for_information, or unclear if the letter does not say.
- decision_quote: the exact sentence from the letter that states the decision, copied character for character.
- claim_ref: the claim number as printed, or "".
- amount_you_owe: the amount the letter says the patient now owes, copied as printed (e.g. "$412.30"), or "".
- date: the letter's date as YYYY-MM-DD, or "".
- next_level_quote: the exact sentence about the next level of appeal or an external review, or "".
- asks_for_quote: if the plan asks for more information, the exact sentence that says what, or "".
Copy sentences exactly as printed. Never calculate or guess."""


@dataclass
class Reply:
    decision: str
    decision_quote: str = ""
    claim_ref: str = ""
    amount_you_owe: Decimal | None = None
    letter_date: date | None = None
    next_level_quote: str = ""
    asks_for_quote: str = ""
    next_step: str = ""
    next_refs: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # model text that is not on the photo
    replayed: bool = True

    def to_json(self) -> dict:
        d = self.__dict__.copy()
        d["amount_you_owe"] = str(self.amount_you_owe) if self.amount_you_owe is not None else None
        d["letter_date"] = self.letter_date.isoformat() if self.letter_date else None
        return d


def reply_body(photo_jpeg: bytes) -> dict:
    from billradar.model_api import data_url
    return {
        "model": REPLY_MODEL, "store": False, "stream": True, "reasoning": {"effort": "low"},
        "max_output_tokens": 2000,
        "text": {"format": {"type": "json_schema", "name": "billradar_reply", "schema": REPLY_SCHEMA, "strict": True}},
        "input": [
            {"role": "developer", "content": [{"type": "input_text", "text": REPLY_INSTRUCTIONS}]},
            {"role": "user", "content": [{"type": "input_text", "text": "The insurer's letter:"},
                                         {"type": "input_image", "image_url": data_url(photo_jpeg, "image/jpeg")}]},
        ],
    }


def photo_text(photo_jpeg: bytes) -> str:
    """Text on the photo, read on the device (RapidOCR), to check the model's quotes against."""
    from PIL import Image

    from billradar import redact
    return " ".join(line.text for line in redact.ocr(Image.open(io.BytesIO(photo_jpeg))))


def _key(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", s.lower())


def read_reply(client, case: Case, photo_jpeg: bytes, plan: plans.Plan | None = None,
               ocr_text: Callable[[bytes], str] = photo_text) -> Reply:
    """The reader reads the reply; every sentence and amount it returns must also be on the photo, read by
    local OCR. Whatever is not found is dropped, never shown as the insurer's words."""
    from billradar.case import parse_date, parse_money
    if client.live and not case.demo:
        from billradar.pipeline import RealDataGate
        raise RealDataGate("live model calls are allowed only for a fictional case")
    res = client.respond(reply_body(photo_jpeg), label="insurer-reply")
    raw = json.loads(res.text)
    on_photo = _key(ocr_text(photo_jpeg))
    r = Reply(decision=raw.get("decision", "unclear") if raw.get("decision") in DECISIONS else "unclear",
              replayed=res.replayed)
    for k in ("decision_quote", "next_level_quote", "asks_for_quote", "claim_ref"):
        v = (raw.get(k) or "").strip()
        if v and _key(v) in on_photo:
            setattr(r, k, v)
        elif v:
            r.dropped.append(f"{k}: not on the photo")
    if not r.decision_quote and r.decision != "unclear":
        r.dropped.append(f"decision {r.decision!r} without its sentence on the photo")
        r.decision = "unclear"
    owed = (raw.get("amount_you_owe") or "").strip()
    if owed and _key(owed) in on_photo:
        r.amount_you_owe = parse_money(owed)
    elif owed:
        r.dropped.append("amount_you_owe: not on the photo")
    r.letter_date = parse_date(raw.get("date"))
    r.next_step, r.next_refs = next_step(r, plan)
    return r


def next_step(r: Reply, plan: plans.Plan | None) -> tuple[str, list[str]]:
    if r.decision == "approved":
        return "Check the next bill from the hospital: the denied amount should be gone.", []
    if r.decision == "asks_for_information":
        return (f"Send what the plan asks for: “{r.asks_for_quote}”" if r.asks_for_quote
                else "Send what the plan asks for in its letter."), []
    if r.decision in ("partly_approved", "denied"):
        if r.next_level_quote:
            return f"Next level, in the plan's own words: “{r.next_level_quote}”", []
        if plan and "external-review" in plan.fragments:
            frag = plan.fragment("external-review")
            return (f"Ask for the next level of review. Your plan document says (page {frag.page}): "
                    f"“{frag.quote}”"), [frag.ref]
        return "Ask the plan in writing how to appeal to the next level.", []
    return "We could not read a decision on this letter. Please check it, or take a clearer photo.", []
