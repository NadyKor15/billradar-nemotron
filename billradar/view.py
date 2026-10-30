"""What the case screen shows, built only from the case and the report (no model calls here)."""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, Decimal

from billradar import policy
from billradar.case import Case, Document, DocKind, Role, Source
from billradar.checks import Finding, Kind, Report

KIND_LABEL = {
    DocKind.HOSPITAL_STATEMENT: "Hospital statement",
    DocKind.ITEMIZED_BILL: "Itemized bill",
    DocKind.EOB: "EOB",
    DocKind.FA_LETTER: "Financial assistance letter",
    DocKind.PAYMENT_RECEIPT: "Payment",
    DocKind.OTHER: "Document",
}
TONE = {Kind.F1: "good", Kind.F2: "time", Kind.F3: "problem", Kind.E1: "problem", Kind.E2: "problem",
        Kind.E3: "good", Kind.E4: "problem", Kind.B1: "problem", Kind.B2: "time", Kind.P1: "time", Kind.NM: "quiet"}
SHEET_COLORS = 6


@dataclass
class Chip:
    label: str
    kind: str  # "sheet", "policy", "profile"
    color: int | None = None  # sheet color index
    title: str = ""


@dataclass
class Card:
    kind: str
    tone: str
    title: str
    detail: str  # one or two short lines, like the picked screen
    chips: list[Chip]
    pill: str = ""
    full: str = ""  # the whole explanation from the checks, on hover / expand


@dataclass
class DocRow:
    doc_id: str
    number: str
    color: int | None
    label: str


@dataclass
class Screen:
    case_title: str
    letterhead: str
    headline: str
    subline: str
    cards: list[Card]
    quiet: list[Card]
    paper: list[DocRow]
    digital: list[DocRow]
    overlays: list[str] = field(default_factory=list)
    replayed: bool = True


def money(x: Decimal) -> str:
    return f"${x:,.2f}" if x != x.to_integral() else f"${x:,.0f}"


def person(case: Case, token: str) -> str:
    from billradar.appeal import norm_token
    for p in case.profile.people:
        if norm_token(p.token) == norm_token(token):
            if p.name:
                return p.name
            return {Role.MOTHER: "Mom", Role.NEWBORN: "Baby"}.get(p.role, token)
    return token or "—"


def doc_label(case: Case, d: Document) -> str:
    parts = [KIND_LABEL[d.kind], person(case, d.patient)]
    if d.first_after_discharge and d.statement_date:
        parts = ["First bill after discharge", f"{d.statement_date:%b %d, %Y}"]
    elif d.kind == DocKind.EOB and d.lines:
        parts.append(d.lines[-1].description or "")
    elif d.patient_balance is not None:
        parts.append(money(d.patient_balance))
    return " · ".join(p for p in parts if p)


def _policy_chip(ref: str) -> Chip:
    pid, fid = ref.split("#", 1)
    frag = policy.load(pid).fragment(fid)
    if pid.startswith("federal"):
        return Chip("Federal rule", "policy", title=frag.quote)
    return Chip(f"Policy p.{frag.page}", "policy", title=frag.quote)


def _sheet(doc_id: str) -> str:
    return f"sheet {doc_id[1:]}" if doc_id.startswith("S") else f"file {doc_id[1:]}"


def _tier_range(policy_id: str, pct: Decimal) -> str:
    pol = policy.load(policy_id)
    tier = pol.tier_for(pct)
    if not tier:
        return ""
    i = pol.tiers.index(tier)
    low = pol.tiers[i - 1].max_fpl_pct + 1 if i else 0
    return f"{low}–{tier.max_fpl_pct}% → {tier.discount_pct}% off" if low else f"up to {tier.max_fpl_pct}% → {tier.discount_pct}% off"


def short(f: Finding, case: Case, policy_id: str) -> str:
    """The one-glance line for the card. Numbers come from the finding's own math."""
    m = f.math
    ev = [e.doc_id for e in f.evidence]
    if f.kind == Kind.F1 and "fpl_pct" in m:
        p = case.profile
        return (f"Household of {p.household_size} · income {money(p.annual_income)} · {m['fpl_pct']}% of the poverty "
                f"line. Policy: {_tier_range(policy_id, Decimal(m['fpl_pct']))}.")
    if f.kind == Kind.F1 and "cap" in m:
        return f"The policy caps what you pay at {money(Decimal(m['cap']))} a year."
    if f.kind == Kind.F2:
        dates = {k: v for k, v in m.items() if k.startswith(("policy:", "federal:"))}  # math also carries notes (why_unsure)
        winner = max(dates, key=lambda k: dates[k])
        base = {"policy: one year after service": "1 year after your care",
                "policy: 12 months after last insurance payment": "12 months after your insurance paid",
                "policy: days after first bill": "the first bill after discharge",
                "federal: 240 days after first bill": "240 days from the first bill after discharge"}.get(winner, winner)
        first = case.doc(ev[0]).statement_date if ev else None
        if "first bill" in base:
            when = f" ({first:%b %d, %Y})" if first else ""
            return f"{base[0].upper()}{base[1:]}{when}."
        tail = f" First bill after discharge: {first:%b %d, %Y}." if first else ""
        federal = dates.get("federal: 240 days after first bill")
        if federal and not winner.startswith("federal") and f.deadline:  # one date, said once: the later one counts
            tail += (f" The federal minimum is {date.fromisoformat(federal):%b %-d, %Y}; the hospital's policy gives you"
                     f" until {f.deadline:%b %-d, %Y}, and the later date counts.")
        return f"{base[0].upper()}{base[1:]}.{tail}"
    if f.kind == Kind.F3 and len(ev) == 2:
        return f"Same stay. {m.get('discount_pct', '')}% off on {_sheet(ev[0])}, missing on {_sheet(ev[1])}."
    if f.kind == Kind.E1 and len(f.evidence) == 2:
        a, b = f.evidence
        return f"Line {a.line_no} on {_sheet(a.doc_id)} vs. patient responsibility on {_sheet(b.doc_id)}."
    if f.kind == Kind.E2 and len(f.evidence) >= 2:
        a, b = f.evidence[0], f.evidence[1]
        return f"Code {m['code']} twice on {_sheet(a.doc_id)} (lines {a.line_no} and {b.line_no}), same day, same price."
    if f.kind == Kind.E3:
        return f.detail if "applied_on" in m else "A credit sits on the account. Ask for it back."
    if f.kind == Kind.E4:
        return (f"Balance {money(Decimal(m['patient_balance']))}, but the coupon asks "
                f"{money(Decimal(m['amount_due_coupon']))}. Ask before you pay.")
    if f.kind == Kind.B1:
        n = m.get("denied_lines", "0")
        denied = (f" {n} line{'s' if n != '1' else ''}" + (f", {money(Decimal(m['denied_total']))}," if m.get("denied_total") else "")
                  + " denied.") if n != "0" else ""
        baby = (f" The baby's bill ({_sheet(m['baby_bill'])}) shows an insurance credit no statement explains."
                if m.get("baby_bill") else "")
        return f"Newborn care ({m['codes']}) is on mom's insurance ID.{denied} Ask to bill it under the baby.{baby}"
    if f.kind == Kind.P1:
        return f"Missing pages: {m['missing'].replace(',', ', ')} of {m['page_count']}. Totals may be on them."
    return f.detail


def card(f: Finding, colors: dict[str, int], case: Case | None = None, policy_id: str = "") -> Card:
    chips, seen = [], set()
    for e in f.evidence:
        if e.doc_id not in seen:
            seen.add(e.doc_id)
            label = f"Sheet {e.doc_id[1:]}" if e.doc_id.startswith("S") else f"File {e.doc_id[1:]}"
            if e.line_no:
                label += f" · line {e.line_no}"
            chips.append(Chip(label, "sheet", colors.get(e.doc_id), e.note))
    pages = []
    for ref in f.policy_refs:
        c = _policy_chip(ref)
        if c.label not in pages:
            pages.append(c.label)
            chips.append(c)
    if f.kind == Kind.F1 and "fpl_pct" in f.math:
        chips.append(Chip("Profile", "profile", title="household size and income, kept on this device"))
    pill = ""
    if f.amount is not None:
        pill = {Kind.F1: f"About {money(f.amount)} off", Kind.F3: f"{money(f.amount)} not applied",
                Kind.E3: f"{money(f.amount)} back to you"}.get(f.kind, money(f.amount))
    detail = short(f, case, policy_id) if case is not None else f.detail
    title = f.title
    if case is not None and f.kind == Kind.F3 and len(f.evidence) == 2:
        has, lacks = (person(case, case.doc(e.doc_id).patient) for e in f.evidence)
        lacks = "the baby" if lacks == "Baby" else lacks
        title = f"Discount on {has}'s bill, not on {lacks}'s"
    return Card(f.kind.value, TONE.get(f.kind, "quiet"), title, detail, chips, pill, f.detail)


def build(case: Case, report: Report, overlays: list[str], replayed: bool = True) -> Screen:
    paper_docs = [d for d in case.documents if d.source == Source.PAPER]
    digital_docs = [d for d in case.documents if d.source != Source.PAPER]
    colors = {d.doc_id: i % SHEET_COLORS for i, d in enumerate(paper_docs)}

    order = {Kind.F1: 0, Kind.F2: 1, Kind.F3: 2, Kind.E1: 3, Kind.E2: 4, Kind.B1: 5, Kind.E4: 6, Kind.E3: 7, Kind.P1: 8}
    cards = [card(f, colors, case, report.policy_id)
             for f in sorted(report.findings, key=lambda f: order.get(f.kind, 9))]
    quiet = [card(f, colors) for f in report.no_money]

    mother = next((p for p in case.profile.people if p.role == Role.MOTHER), None)
    who = person(case, mother.token) if mother else "Your"
    case_title = f"{who}'s case" if who != "Your" else "Your case"
    if any(p.role == Role.NEWBORN for p in case.profile.people):
        case_title = case_title.replace("case", "birth case")

    total = report.total_may_come_off
    # whole dollars, rounded down: "up to" must never promise more than the checks found
    headline = (f"Up to {money(total.to_integral_value(rounding=ROUND_DOWN))} may come off your bills" if total > 0
                else "We checked every sheet")
    bits = [f"Found {len(paper_docs)} paper sheet{'s' if len(paper_docs) != 1 else ''}"]
    if digital_docs:
        bits[0] += f" + {len(digital_docs)} digital file{'s' if len(digital_docs) != 1 else ''}"
    if report.apply_by:
        bits.append(f"apply by {report.apply_by:%b %d, %Y}")

    return Screen(
        case_title=case_title, letterhead=case.letterhead, headline=headline, subline=" · ".join(bits),
        cards=cards, quiet=quiet,
        paper=[DocRow(d.doc_id, d.doc_id[1:], colors[d.doc_id], doc_label(case, d)) for d in paper_docs],
        digital=[DocRow(d.doc_id, d.doc_id[1:], None, doc_label(case, d)) for d in digital_docs],
        overlays=overlays, replayed=replayed,
    )


# ---- who did what: model labels per step and per finding (provider from the run, never hard-wired) ----

# role -> label, per provider. A run says which provider answered in problems.json ("provider") and may
# override single labels ("models": {"read": "..."}). Roles: find (sheets on the photo), read (all sheets into
# one case), review (read-only second look at findings and letters).
MODEL_LABELS = {
    "nebius": {"find": "Nemotron 3.5 Super VL", "read": "Nemotron 3.5 Super VL", "review": "Nemotron 3 Ultra"},
}


@dataclass
class ModelTag:
    label: str
    role: str  # find | read | review | code | device
    replayed: bool = False

    @property
    def title(self) -> str:
        what = {"find": "found the sheets on the photo", "read": "read the sheets",
                "review": "reviewed the findings, read-only", "code": "computed in code from the sheets and the policy",
                "device": "done on this device"}[self.role]
        return f"{self.label}: {what}" + (" (replayed answers, not a live call)" if self.replayed else "")


def labels_for(run_info: dict) -> dict[str, str]:
    provider = run_info.get("provider", "nebius")
    base = dict(MODEL_LABELS.get(provider, {}))
    base.update(run_info.get("models") or {})
    return base


def _reviewed(run_info: dict) -> bool:
    """Only when the review model really answered (its notes are in the checker's issues or flagged)."""
    checker = run_info.get("checker") or {}
    names = ("Nemotron",)  # the review note starts with the name of the model that ran (verifier.checker_name)
    return bool(checker.get("reviewed")) or any(n in i.get("what", "") for n in names for i in checker.get("issues", []))


def models_for(f: Finding, run_info: dict) -> list[ModelTag]:
    """The models that really took part in this finding: who found its sheets, who read them, who reviewed it."""
    labels, replayed = labels_for(run_info), run_info.get("replayed", True)
    ran = run_info.get("timings") or {}  # a case read without photos (or not by a model) never ran these steps
    docs = {e.doc_id for e in f.evidence}
    tags = []
    if any(d.startswith("S") for d in docs) and "find" in labels and "sheets" in ran:
        tags.append(ModelTag(labels["find"], "find", replayed))
    if docs and "read" in labels and "read" in ran:
        if tags and tags[-1].label == labels["read"]:  # one model found and read the sheets: one chip that says so
            tags[-1] = ModelTag(f"{labels['read']} · found & read", "find", replayed)
        elif labels.get("find") == labels["read"]:  # same model, only the reading here (a PDF): say which step
            tags.append(ModelTag(f"{labels['read']} · read", "read", replayed))
        else:
            tags.append(ModelTag(labels["read"], "read", replayed))
    if _reviewed(run_info) and "review" in labels:
        tags.append(ModelTag(labels["review"], "review", replayed))
    return tags


@dataclass
class Step:
    n: int
    title: str
    tag: ModelTag
    detail: str


def steps(case: Case, run_info: dict, sheets: int) -> list[Step]:
    """How this case was made, step by step, with who did each step."""
    labels, replayed = labels_for(run_info), run_info.get("replayed", True)
    t = run_info.get("timings") or {}
    digital = sum(1 for d in case.documents if d.source != Source.PAPER)
    if "sheets" not in t and "read" not in t:  # no photos and no model reading in this run
        return [Step(1, "Papers already read", ModelTag("Sample data", "code"),
                     f"{len(case.documents)} documents, typed in for this example"),
                Step(2, "Checked every quote and amount", ModelTag("Code checker", "code"),
                     f"{(run_info.get('checker') or {}).get('checked', 0)} items re-done from the papers")]
    out = [
        Step(1, "Found every sheet", ModelTag(labels.get("find", "—"), "find", replayed),
             f"{sheets} sheet{'s' if sheets != 1 else ''} on the photos" + (f" · {t['sheets']:.1f} s" if t.get("sheets") else "")),
        Step(2, "Hid names", ModelTag("On this device", "device"),
             "Names and account numbers covered before upload" if run_info.get("redacted")
             else "Not used here: these are fictional sample papers"),
        Step(3, "Read and connected all sheets", ModelTag(labels.get("read", "—"), "read", replayed),
             f"{len(case.documents)} documents into one case" + (f" ({digital} digital)" if digital else "")),
        Step(4, "Checked every quote and amount", ModelTag("Code checker", "code"),
             f"{(run_info.get('checker') or {}).get('checked', 0)} items re-done from the sheets"),
    ]
    if _reviewed(run_info) and "review" in labels:
        out.append(Step(5, "Second look, read-only", ModelTag(labels["review"], "review", replayed), "Findings and letters"))
    return out


# ---- the case screen in style D: findings as rows, with a level and who found them ----

LEVEL_HIGH, LEVEL_CHECK, LEVEL_OK = "serious", "worth checking", "ok"
CAPTION = {Kind.E1: "Potential overcharge", Kind.E2: "Potential overcharge", Kind.F1: "May come off",
           Kind.F3: "Not applied yet", Kind.E3: "Back to you"}
BORDERLINE_NEXT = {Kind.F1: "Send the application", Kind.F3: "Ask why the discount is missing",
                   Kind.B1: "Ask to bill it under the baby", Kind.P1: "Ask for the missing pages",
                   Kind.I1: "Letter ready: request itemized bill"}
NO_AMOUNT = {Kind.B2: ("Send the baby's insurance", "No statement covers the baby"), Kind.B1: ("May be reprocessed", "Newborn coverage may apply"),
             Kind.E4: ("Ask before you pay", "Two amounts on one sheet"),
             Kind.P1: ("Ask for the pages", "Totals may be on them")}


@dataclass
class Row:
    kind: str
    level: str  # serious | worth checking | ok
    label: str  # CONFIRMED · WORTH CHECKING · CHECKED — NO MONEY HERE
    title: str
    where: str  # "Sheet 10, lines 3–4"
    detail: str
    models: list[ModelTag]
    value: str  # right column, big
    caption: str
    full: str = ""
    laws: list[Chip] = field(default_factory=list)
    refs: list[Chip] = field(default_factory=list)  # the policy pages whose quotes back this finding
    explain: str = ""
    confidence: float | None = None
    unsure: str = ""  # why the checker is not sure (borderline)
    lines: list[str] = field(default_factory=list)  # every line it points to, in plain words

    @property
    def sure(self) -> str:
        """The confidence scale: green >= 0.9, yellow 0.7-0.89, orange below (worth checking)."""
        if self.confidence is None:
            return ""
        return "high" if self.confidence >= 0.9 else ("mid" if self.confidence >= 0.7 else "low")


def _where(f: Finding) -> str:
    by_doc: dict[str, list[int]] = {}
    for e in f.evidence:
        by_doc.setdefault(e.doc_id, [])
        if e.line_no:
            by_doc[e.doc_id].append(e.line_no)
    parts = []
    for doc, lines in by_doc.items():
        name = f"Sheet {doc[1:]}" if doc.startswith("S") else f"File {doc[1:]}"
        lines = sorted(set(lines))
        if len(lines) == 1:
            name += f", line {lines[0]}"
        elif lines:
            name += f", lines {lines[0]}–{lines[-1]}" if lines == list(range(lines[0], lines[-1] + 1)) \
                else ", lines " + ", ".join(map(str, lines))
        parts.append(name)
    return " · ".join(parts)


def _law_chips(f: Finding) -> list[Chip]:
    chips = []
    for ref in getattr(f, "laws", None) or []:
        try:
            chips.append(source_chip(ref))
        except Exception:  # a law id the sources do not know: show the id, never invent a quote
            chips.append(Chip(ref.split("#", 1)[-1], "policy"))
    return chips


def row(f: Finding, case: Case, policy_id: str, run_info: dict) -> Row:
    severity = getattr(f, "severity", None)
    borderline = severity == "borderline"
    if f.kind == Kind.NM:
        level, label = LEVEL_OK, "Checked — no money here"
    elif borderline:
        level, label = LEVEL_CHECK, "Worth checking"
    else:
        level, label = LEVEL_HIGH, "Confirmed"
    if f.kind == Kind.NM:
        value, caption = "No issue found", ""
    elif borderline and f.kind == Kind.F2 and f.deadline:  # a date is not an amount: show it, to be checked
        value, caption = f"{f.deadline:%b %-d, %Y}", "Last day to apply, check it with the hospital"
    elif borderline:  # no amount until it is confirmed; the next step fits the finding
        value, caption = "No amount yet", BORDERLINE_NEXT.get(f.kind, "Ask for the itemized bill")
    elif f.amount is not None:
        value, caption = f"${f.amount:,.2f}", CAPTION.get(f.kind, "")
    elif f.kind == Kind.F2 and f.deadline:
        value, caption = f"{f.deadline:%b %-d, %Y}", "Last day to apply"
    else:
        value, caption = NO_AMOUNT.get(f.kind, ("", ""))
    detail = short(f, case, policy_id) if f.kind != Kind.NM else _nm_detail(f, case)
    title = f.title
    if f.kind == Kind.F3 and len(f.evidence) == 2:
        title = card(f, {}, case, policy_id).title
    refs = []
    for ref in f.policy_refs:
        c = _policy_chip(ref)
        if c.label not in [r.label for r in refs]:
            refs.append(c)
    from billradar.i18n import plain
    title, detail = plain(title, case), plain(detail, case)  # tokens the model saw, in words for the family
    return Row(f.kind.value, level, label, title, _where(f), detail, models_for(f, run_info), value, caption,
               plain(f.detail, case), _law_chips(f), refs, plain(getattr(f, "explain", None) or "", case), getattr(f, "confidence", None),
               _unsure(f.math.get("why_unsure", "")), _plain_lines(f, case))


def _nm_detail(f: Finding, case: Case) -> str:
    from billradar import finding_text
    return finding_text.text(f, case, "en")[1]


def _unsure(why: str) -> str:
    """Why a finding is not confirmed, in words the card can stand behind. The second read's own text can name dates
    and sheets the code has already worked out otherwise (the F2 card once said "an unknown date" next to the code's
    "Mar 21, 2026"): the card says who doubted it and points to the case data, where its words are kept as given."""
    if "Nemotron-3-Ultra-550b-a55b:" in why:
        why = re.sub(r"Nemotron-3-Ultra-550b-a55b:[^;]*", "Nemotron 3 Ultra, the second read, could not confirm it from "
                     "the papers; its notes are in the case data", why)
    return why


def _plain_lines(f: Finding, case: Case) -> list[str]:
    from billradar import explain
    out = []
    for e in f.evidence:
        if not e.line_no:
            continue
        try:
            doc = case.doc(e.doc_id)
        except StopIteration:
            continue
        ln = next((x for x in doc.lines if x.line_no == e.line_no), None)
        if ln is not None:
            text = f"{'Sheet' if e.doc_id.startswith('S') else 'File'} {e.doc_id[1:]}, line {e.line_no}: {explain.line(ln)}"
            if text not in out:
                out.append(text)
    return out


@dataclass
class Board:
    rows: list[Row]
    serious: int
    checking: int
    quiet: int


def board(case: Case, report: Report, run_info: dict) -> Board:
    order = {Kind.E2: 0, Kind.E1: 1, Kind.B1: 2, Kind.F1: 3, Kind.F3: 4, Kind.E3: 5, Kind.E4: 6, Kind.P1: 7, Kind.F2: 8}
    found = sorted(report.findings, key=lambda f: (getattr(f, "severity", None) == "borderline", order.get(f.kind, 9)))
    rows = [row(f, case, report.policy_id, run_info) for f in found]
    # "Worth checking": borderline items without an amount, kept apart from confirmed ones
    asking = [f.model_copy(update={"severity": "borderline"}) for f in getattr(report, "worth_asking", None) or []]
    rows += [row(f, case, report.policy_id, run_info) for f in asking]
    rows += [row(f, case, report.policy_id, run_info) for f in report.no_money]
    return Board(rows, sum(r.level == LEVEL_HIGH for r in rows), sum(r.level == LEVEL_CHECK for r in rows),
                 sum(r.level == LEVEL_OK for r in rows))


def service_span(case: Case) -> str:
    docs = [d for d in case.bills() if d.service_from] or [d for d in case.documents if d.service_from]
    starts = [d.service_from for d in docs]
    ends = [d.service_to or d.service_from for d in docs]
    if not starts:
        return ""
    a, b = min(starts), max(ends)
    if a == b:
        return f"{a:%b %-d, %Y}"
    if a.year == b.year and a.month == b.month:
        return f"{a:%b %-d}–{b:%-d, %Y}"
    return f"{a:%b %-d, %Y} – {b:%b %-d, %Y}"


# ---- static pages: which model does what, and how far along it is (keep in step with the code) ----

READY, REPLAY, PLANNED = "in the app", "in the app · replayed until our API credits arrive", "planned"


@dataclass
class ModelStatus:
    model: str
    does: str
    status: str


NEMOTRON_STATUS = [
    ModelStatus("Nemotron 3.5 Super VL", "Finds every sheet on the photo of the table, then reads all sheets and files "
                "(up to 8 images per request) and connects them into one case.", READY),
    ModelStatus("Nemotron 3 Ultra", "Takes a second, read-only look at the findings and letters; a doubt lowers a "
                "finding's confidence, the code check decides what is held back.", READY),
    ModelStatus("Nemotron 3 Super", "Answers questions about the case, only with a quote from the papers or the policy.",
                READY),
    ModelStatus("Nemotron 3.5 Lightning", "Translates letters into Russian and Spanish; every number and quote is checked.",
                READY),
    ModelStatus("Voice notes", "Typed after the call: there is no speech model on Nebius Token Factory yet.", PLANNED),
]


def model_status() -> list[ModelStatus]:
    return NEMOTRON_STATUS


@dataclass
class HomeStep:
    title: str
    model: str


def steps_static() -> list[HomeStep]:
    from billradar import providers
    m = providers.labels()
    return [HomeStep("Find every sheet", m["find"]), HomeStep("Hide names", "On this device"),
            HomeStep("Read and connect", m["read"]), HomeStep("Check quotes and amounts", "Code checker"),
            HomeStep("Letters and forms", "You sign")]


def source_chip(ref: str) -> Chip:
    """A small label for one source: the hospital policy page, the plan document page, or the rule's name."""
    from billradar import plans, writer
    src = writer.source_of(ref)
    if plans.is_ref(ref):
        return Chip(f"Plan document p.{src['page']}", "policy", title=src["quote"])
    if not writer.paged(ref) or ref.startswith("federal"):
        return Chip(src["title"].split(",")[0], "policy", title=src["quote"])
    return Chip(f"Policy p.{src['page']}", "policy", title=src["quote"])


@dataclass
class AppealPanel:
    plan: str
    denial_date: str
    deadline: str
    days: int | None


def appeal_panel(case: Case, letter) -> AppealPanel | None:
    """The right-hand panel on an appeal letter: when the plan denied it and the last day to appeal."""
    from billradar import appeal, plans
    if not letter.kind.startswith("appeal-"):
        return None
    for d in appeal.denials(case):
        if appeal.letter_kind(d) == letter.kind:
            dl = appeal.deadline(plans.load(d.plan_id), d)
            return AppealPanel(plans.load(d.plan_id).title, f"{d.denial_date:%m/%d/%Y}" if d.denial_date else "",
                               f"{dl.day:%m/%d/%Y}" if dl else "", (dl.day - d.denial_date).days if dl and d.denial_date else None)
    return None


LETTER_NAMES = {"itemized": "Request for itemized bill", "apply-discount": "Apply the discount to the baby's bill",
                "application": "Financial assistance application", "dispute": "Fix an amount on the bill"}


def letter_name(kind: str) -> str:
    return "Insurance appeal" if kind.startswith("appeal-") else LETTER_NAMES.get(kind, kind.replace("-", " ").capitalize())


def mark_quotes(text: str):
    """Quoted words (“…”) highlighted, everything else escaped: the quote is what the source says."""
    import re

    from markupsafe import Markup, escape
    parts = re.split(r"(“[^”]+”)", text)
    return Markup("".join(f'<span class="quoted">{escape(p)}</span>' if p.startswith("“") else str(escape(p))
                          for p in parts))


@dataclass
class ReceiptRow:
    step: str
    model: str
    calls: int
    tokens: int
    usd: float
    seconds: float | None  # None for a replay: the recording keeps the cost, not the time it took


def receipt_rows(receipt: dict | None) -> list[ReceiptRow]:
    """The case receipt grouped by step and model, in the order the steps ran."""
    if not receipt:
        return []
    out: dict[tuple[str, str], ReceiptRow] = {}
    for r in receipt["rows"]:
        k = (r["step"], r["model"])
        row = out.setdefault(k, ReceiptRow(r["step"], _model_name(r["model"]), 0, 0, 0.0, 0.0 if r["live"] else None))
        row.calls += 1
        row.tokens += r["input_tokens"] + r["output_tokens"]
        row.usd += r["usd"]
        if row.seconds is not None:
            row.seconds += r["seconds"]
    return list(out.values())


def _model_name(model_id: str) -> str:
    from billradar import providers
    return providers._short(model_id)


STORY_CARDS = {"E2": "Billed twice", "B1": "Baby on mom's insurance", "F1": "Financial help you qualify for",
               "E1": "More than insurance says", "E3": "A credit left on the account", "F3": "Discount on mom, not on baby"}


STORY_STAGE = 1.44, 44, 8  # episode 1: stage width/height, photo card width and top in % of the stage


def story_found(found: dict, sheet_url) -> dict:
    """Episode 1 and 2 from scripts/story_assets.py: the photo card with the boxes Super VL drew, each sheet starting
    where its box is, and the request text the second read got, with its tokens marked."""
    import re as _re
    ar, card_w, card_top = STORY_STAGE
    w, h = found["photo"]["width"], found["photo"]["height"]
    card_h = card_w * ar * h / w
    sheets = [{"sheet": b["sheet"], "src": sheet_url(b["sheet"]), "n": i,
               "x0": round(card_w * b["box"][0] / w, 2), "y0": round(card_top + card_h * b["box"][1] / h, 2),
               "w0": round(card_w * (b["box"][2] - b["box"][0]) / w, 2), "box": b["box"]}
              for i, b in enumerate(found["boxes"], start=1)]
    parts = [(t, bool(_re.fullmatch(r"\[?[A-Z]+_\d+\]?", t)))
             for t in _re.split(r"(\[[A-Z]+_\d+\]|\b(?:PATIENT|MEMBER)_\d+\b)", found["sent"]["excerpt"]) if t]
    return {"w": w, "h": h, "card_w": card_w, "card_top": card_top, "card_h": round(card_h, 2), "ar": ar,
            "sheets": sheets, "find_model": found["find_model"], "find_recording": found["find_recording"],
            "sent_model": found["sent"]["model"], "sent_recording": found["sent"]["recording"], "sent_parts": parts}


def story(case: Case, report, letter, sheet_urls: list[str]) -> dict:
    """What the scroll story on the home page shows: the example case's own names, numbers and letter."""
    import re as _re
    tokens = case.profile.local.tokens
    mother = next((p.name for p in case.profile.people if p.role.value == "mother" and p.name), "")
    clean = lambda s: _re.sub(r"\s+", " ", _re.sub(r"\[[A-Z_0-9]+\]", "", s or "")).strip()
    lines = []
    for doc in case.bills():
        rows = [(clean(ln.description).title(), ln.charges) for ln in doc.lines if ln.charges and clean(ln.description)]
        if len(rows) >= 4:
            lines = [(d[:34], money(c)) for d, c in rows[:5]]
            break
    present = {f.kind.value for f in report.findings}
    icons = {"B1": "baby", "F1": "coin", "F3": "baby"}
    kinds = [{"label": STORY_CARDS[k], "icon": icons.get(k, "docs")}
             for k in ("E2", "B1", "F1", "E1", "E3", "F3") if k in present]  # the frames' order first
    body = " ".join(p.text for p in letter.paragraphs[:2]) if letter else ""
    head, sep, rest = body.partition(": ")
    if sep and "; " in rest:  # a long list of accounts: the two the story shows (mother's and baby's bill), as lines
        items, _, tail = rest.partition(". ")
        items = items.split("; ")
        shown = [i for i in items if any(v and v in i for v in (tokens.get("ACCT_1"), tokens.get("ACCT_2")))] or [items[0], items[-1]]
        more = len(items) - len(shown)
        body = head + ":\n· " + "\n· ".join(shown) + (f"\n· and {more} more account{'s' if more != 1 else ''}" if more else "") + ("\n" + tail if tail else "")
    return {"hospital": case.letterhead, "patient": mother, "member": tokens.get("MEMBER_1", ""),
            "account": tokens.get("ACCT_1", ""), "address": tokens.get("ADDR_1", ""), "lines": lines,
            "amount": int(report.total_may_come_off), "cards": kinds[:3], "sheets": sheet_urls[:5],
            "letter_to": letter.to if letter else "", "letter_subject": letter.subject if letter else "",
            "letter_body": body, "signer": mother}
