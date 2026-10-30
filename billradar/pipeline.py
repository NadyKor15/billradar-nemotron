"""Photo(s) of the table + digital files -> sheets (Nemotron 3.5 Super VL + local corners) -> case (Super VL)
-> findings (code) -> second read (Nemotron 3 Ultra, BILLRADAR_SECOND_READ=1)."""

from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from billradar import checks, pdfpages, people, providers, reader, receipt, redact, verifier, writer
from billradar import policy_finder
from billradar import sheets as sheetlib
from billradar.case import Case, DocKind, Profile
from billradar.model_api import ModelError
from billradar.nemotron_client import NemotronClient


@dataclass
class CaseRun:
    case: Case
    report: checks.Report
    sheets: list[sheetlib.Sheet]
    overlays: list[bytes]  # one numbered overlay per table photo, JPEG
    sent_views: list[bytes] = field(default_factory=list)  # private mode: the covered photo, as the models got it
    problems: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    replayed: bool = True
    verdict: verifier.Verdict | None = None
    redacted: bool = False
    receipt: dict | None = None  # Nemotron runs: model, seconds and cost per step (billradar.receipt)
    provider: str = providers.PROVIDER  # which models answered (providers.py)
    file_previews: dict[str, bytes] = field(default_factory=dict)  # D1 -> page 1 of that PDF as JPEG, shown on this device
    originals: dict = field(default_factory=dict)  # private mode: S1 -> the sheet cut from the photo as taken, for the screen
    found_policy: dict | None = None  # BILLRADAR_POLICY_FINDER=1: the hospital's own policy, via Tavily (policy_finder)


def _jpeg(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


class RealDataGate(RuntimeError):
    pass


def run(client: NemotronClient, photos: list[bytes], files: list[tuple[str, bytes]], profile: Profile,
        policy_id: str, letterhead: str = "", today: date | None = None, case_id: str = "case",
        fictional: bool = False, private: bool = False) -> CaseRun:
    # Gate: live model calls only on packs marked fictional. Real bills need local redaction of the
    # images first, a test that proves it, and the owner's explicit yes. None of that exists yet.
    if client.live and not fictional:
        raise RealDataGate("live model calls are allowed only on fictional packs (mark the pack fictional)")
    problems: list[str] = []
    timings: dict[str, float] = {}
    found_sheets: list[sheetlib.Sheet] = []
    overlays: list[bytes] = []
    replayed = True
    vault = _vault(profile) if private else None
    sent_views: list[bytes] = []
    find_sheets = providers.sheet_finder(client)  # Super VL boxes + local corners
    first_call = len(getattr(client, "calls", []))  # this case's calls start here (receipt)
    previews = _previews(files)  # before private mode takes the files out of the read
    pdfs: list = []
    pending: list = []  # private mode: sheets cut from each covered photo, checked once all of them are learned
    originals: dict = {}
    if vault is not None:  # the files' own text first: values found there are covered on every photo and page
        for i, (name, raw) in enumerate(files, start=1):
            try:
                pdfpages.learn(vault, raw)
                pdfs.append((i, name, raw))
            except pdfpages.RenderError as exc:
                problems.append(f"private mode: {name} not sent: {exc}")
        files = []
    for photo in photos:
        if vault is not None:
            covered = redact.redact(sheetlib.load_photo(photo), vault)
            to_model = redact.send_copy(covered.image, secrets=vault.secrets)
            found = find_sheets(client, photo, send=to_model, crop_from=covered.image)
            for s in found.sheets:  # every cut sheet is learned before any is checked: a number labelled on
                redact.learn(s.image, vault)  # sheet 10 is covered on sheet 9 as well
            pending.append((found, to_model))
        else:
            found = find_sheets(client, photo)
        offset = len(found_sheets)
        as_taken = sheetlib.load_photo(photo) if vault is not None else None
        for s in found.sheets:  # number sheets across all photos: S1..Sn
            s.doc_id = f"S{offset + int(s.doc_id[1:])}"
            if as_taken is not None:  # the models got the covered cut; the family's screen shows the sheet as photographed
                originals[s.doc_id] = _cut(as_taken, s)
        found_sheets += found.sheets
        overlays.append(_jpeg(sheetlib.overlay(photo, found)))
        problems += [f"sheets: {d}" for d in found.dropped]
        timings["sheets"] = timings.get("sheets", 0) + found.seconds
        replayed &= found.replayed

    for found, to_model in pending:
        for s in found.sheets:
            s.image = _no_leaks(s.image, vault, s.doc_id)
        sent_views.append(_jpeg(_what_was_sent(to_model, found.sheets, providers.models(client))))

    pages: list = []
    for i, name, raw in pdfs:
        try:
            pages += pdfpages.cover(raw, f"D{i}", vault, lambda img, pid: _no_leaks(img, vault, pid))
        except pdfpages.RenderError as exc:
            problems.append(f"private mode: {name} not sent: {exc}")
    if pages:
        sent_views.append(_jpeg(pdfpages.sent_view(pages, _short(providers.models(client)["read"]))))
    digital = [(f"D{i}", name, raw) for i, (name, raw) in enumerate(files, start=1)]
    read = reader.read_case(client, [(s.doc_id, _jpeg(s.image)) for s in found_sheets]
                            + [(pid, _jpeg(img)) for pid, img in pages], digital)
    problems += [f"reader: {p}" for p in read.problems]
    read.documents = _office_from_coupon(_second_look(client, read.documents, found_sheets, problems), found_sheets, letterhead,
                                         problems)
    timings["read"] = read.seconds
    replayed &= read.replayed

    if vault is not None:  # the tokens stay on the device; letters put the real values back locally
        profile = profile.model_copy(deep=True)
        for secret in vault.secrets:
            profile.local.tokens.setdefault(secret.token.strip("[]"), secret.value)
    case = Case(case_id=case_id, letterhead=letterhead, policy_id=policy_id, today=today or date.today(),
                profile=profile, documents=_visit_dates(checks.one_office_per_account(
                    people.link(pdfpages.merge_pages(read.documents), profile), letterhead), found_sheets, problems))
    report = checks.run(case)
    report, verdict = check_and_hold(case, report, client)
    return CaseRun(case, report, found_sheets, overlays, sent_views, problems, timings, replayed, verdict,
                   redacted=vault is not None, receipt=receipt.build(getattr(client, "calls", [])[first_call:]),
                   provider=getattr(client, "provider", providers.PROVIDER), file_previews=previews,
                   originals=originals, found_policy=policy_finder.for_case(case, client))


@dataclass
class Covered:
    name: str  # "table-1.jpg", "01-statement.pdf page 2"
    image: object  # PIL image, covered
    blocked: str = ""  # why it would not be sent: the kinds of what is still readable, never the values


def cover_only(photos: list[tuple[str, bytes]], files: list[tuple[str, bytes]], profile: Profile) -> list[Covered]:
    """Private mode's covering alone, on this device, with no model call: every photo whole and every PDF page,
    covered and run through the same leak gate the sheets pass before they are sent. For a person to look at
    their own papers covered before trusting the app with them (`billradar cover`)."""
    vault = _vault(profile)
    out: list[Covered] = []
    for name, raw in files:  # the files' own text first, as in run(): their values are covered on the photos too
        try:
            pdfpages.learn(vault, raw)
        except pdfpages.RenderError as exc:
            out.append(Covered(name, None, f"not read: {exc}"))
    images = [(name, sheetlib.load_photo(raw)) for name, raw in photos]
    for name, raw in files:
        try:
            images += [(f"{name} page {k}", page) for k, page in enumerate(pdfpages.render(raw), start=1)]
        except pdfpages.RenderError:
            continue  # already reported above
    for _, img in images:  # every value on every paper first, as run() learns every cut sheet before checking one
        redact.learn(img, vault)
    out += [_gated(name, redact.redact(img, vault).image, vault) for name, img in images]
    return out


def _gated(name: str, img, vault: redact.Vault) -> Covered:
    try:
        return Covered(name, _no_leaks(img, vault, name))
    except RealDataGate:
        left = redact.leaks(img, vault.secrets, vault.units, vault.name_words)
        return Covered(name, img, ", ".join(sorted({s.kind for s in left})) or "still readable")


SERVICE_CODE = re.compile(r"(?<![\d.,$])(\d{5}|0\d{3})(?![\d.,])")
MONEY_ON_ROW = re.compile(r"\$\s?\d")
BALANCE_WORDS = re.compile(r"(?i)balance|amount due|you owe|total due|please pay")
SECOND_LOOKS = 4  # at most this many sheets are read again alone, per case


def _rows(lines) -> list[str]:
    """OCR boxes joined into printed rows, left to right ("01960" + "$4,944.00" + "$196.82")."""
    rows: list[list] = []
    for line in sorted(lines, key=lambda l: (float(l.box[:, 1].min()), float(l.box[:, 0].min()))):
        row = next((r for r in rows if redact._same_row(r[0], line)), None)
        (row.append(line) if row is not None else rows.append([line]))
    return [" ".join(l.text for l in sorted(r, key=lambda l: float(l.box[:, 0].min()))) for r in rows]


def _missed(doc, lines) -> str:
    """What the device sees on a sheet that its reading left out, in words; "" when nothing."""
    from collections import Counter
    rows = _rows(lines)
    seen = Counter(c for row in rows if MONEY_ON_ROW.search(row) for c in set(SERVICE_CODE.findall(row)))
    read = Counter(ln.code.strip() for ln in doc.lines if ln.code)
    short = [f"{code} {n} times, read {read.get(code, 0)}" for code, n in seen.items() if n > read.get(code, 0)
             and (read.get(code, 0) or n >= 2)]
    if short:
        return "the sheet prints " + "; ".join(short)
    if doc.kind == DocKind.EOB:  # the share is printed on the code's row and the reading left it out on every line
        shared = {c for row in rows if len(MONEY_ON_ROW.findall(row)) >= 2 for c in set(SERVICE_CODE.findall(row))}
        lost = sorted(c for c in shared if read.get(c) and all(
            ln.patient_responsibility is None for ln in doc.lines if ln.code.strip() == c))
        if lost:
            return "the sheet prints your share for " + ", ".join(lost) + ", the reading has none"
    if doc.patient_balance is None and doc.amount_due_coupon is None and any(
            BALANCE_WORDS.search(row) and MONEY_ON_ROW.search(row) for row in rows):
        return "the sheet prints a balance, the reading has none"
    return ""


PAYABLE_TO = re.compile(r"(?i)make\s*checks?\s*payable\s*to\s*:?\s*(.*)")


def _payee(lines) -> str:
    """'MAKE CHECK PAYABLE TO : 300_Women First OB/GYN Care, LLC' -> the office the bill is from, read on this
    device; "" when the payee is printed apart from the words or not at all."""
    rows = _rows(lines)
    for i, row in enumerate(rows):
        m = PAYABLE_TO.search(row)
        if not m or re.search(r"(?i)mail\s*to", m.group(0)):
            continue
        raw = m.group(1)
        if not re.search(r"[A-Za-z]{3}", raw) and i + 1 < len(rows):  # the name printed on the line under the heading
            raw = redact.GLUED_TOKEN.sub(" ", rows[i + 1])
        if re.search(r"[A-Za-z]{3}", raw):
            name = re.sub(r"^[0-9O]{2,4}[_\s]+", "", raw.strip(" :"))  # "300_" is the office's billing code
            name = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)  # OCR ran the words together
            return re.sub(r"\s*,\s*", ", ", name)
    return ""


OFFICE_WORDS = re.compile(r"(?i)\b(?:LLC|P\.?A|M\.?D|Inc|Group|Pediatrics|Physicians?|Associates|Clinic)\b")


def _office_name(lines) -> str:
    """A bill the reader left without a name: the office printed on it ("Little Harbor Pediatrics - PA"), the first
    line with an office's word and no digits."""
    for row in _rows(lines):
        name = redact.GLUED_TOKEN.sub(" ", row).strip(" -:")
        words = re.findall(r"[A-Za-z]{2,}", name)
        if OFFICE_WORDS.search(name) and not re.search(r"\d", name) and 2 <= len(words) <= 6:
            return re.sub(r"\s*-\s*", " - ", name)
    return ""


def _office_from_coupon(docs: list, sheets: list, letterhead: str, problems: list[str]) -> list:
    """A doctor's statement that names the hospital as the place of service ("Seaside Regional Hospital (IP)")
    while its coupon says whom to pay: the bill is from the payee."""
    from billradar.checks import _name_words
    if not letterhead:
        return docs
    home = _name_words(letterhead)
    images = {s.doc_id: s.image for s in sheets}
    out = []
    for d in docs:
        if d.kind != DocKind.EOB and d.doc_id in images and (not d.provider or home & _name_words(d.provider)):
            lines = redact.ocr(images[d.doc_id])
            payee = _payee(lines) or (_office_name(lines) if not d.provider else "")
            if payee and _name_words(payee) and not (home & _name_words(payee)):
                problems.append(f"{d.doc_id}: billed by {payee} (the coupon's payee, read on this device), not "
                                f"{d.provider or 'an unnamed office'}")
                d = d.model_copy(update={"provider": payee})
        out.append(d)
    return out


def _second_look(client, docs: list, sheets: list, problems: list[str]) -> list:
    """The reader's answer checked against the sheet on this device (OCR of the very image it got): a line read
    once that is printed twice, or a balance it left out. Such a sheet is read again on its own and the new
    reading is kept when it no longer misses it. Never more than SECOND_LOOKS sheets."""
    by_id = {d.doc_id: i for i, d in enumerate(docs)}
    looks = 0
    for s in sheets:
        if s.doc_id not in by_id or looks >= SECOND_LOOKS:
            continue
        lines = redact.ocr(s.image)
        why = _missed(docs[by_id[s.doc_id]], lines)
        if not why:
            continue
        looks += 1
        again = reader.read_case(client, [(s.doc_id, _jpeg(s.image))], [])
        new = next((d for d in again.documents if d.doc_id == s.doc_id), None)
        if new is not None and not _missed(new, lines):
            docs[by_id[s.doc_id]] = new
            problems.append(f"{s.doc_id}: read again on its own: {why}")
        else:
            problems.append(f"{s.doc_id}: {why}; reading it again on its own did not fix it")
    return docs


VISIT_RX = re.compile(r"(?i)(?:visit|date of service|service date)\s*(?:on|:)?\s*([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}|\d{1,2}/\d{1,2}/\d{4})")


VISIT_WORDS = re.compile(r"(?i)visit\s*on|date of service|service date")


def _visit_date_in(text: str):
    """'[PATIENT_1]'svisitonFebruary11,2025' -> 2025-02-11 (OCR runs the words together)."""
    from datetime import datetime
    t = re.sub(r",\s*", ", ", re.sub(r"([A-Za-z])(\d)", r"\1 \2", text))
    out = set()
    for m in VISIT_RX.finditer(t):
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%b. %d, %Y", "%m/%d/%Y"):
            try:
                out.add(datetime.strptime(m.group(1), fmt).date())
                break
            except ValueError:
                continue
    return out


def _visit_dates(docs: list, sheets: list, problems: list[str]) -> list:
    """A bill that prints one visit in its heading ("Daniela's visit on February 11, 2025") and lines without a
    date of their own: the lines get that date. Read on this device by OCR from the very image the reader got,
    never guessed; with two visit dates on the sheet nothing is filled."""
    images = {s.doc_id: s.image for s in sheets}
    out = []
    for d in docs:
        undated = [ln for ln in d.lines if not ln.service_date and ln.code]
        if not undated or d.doc_id not in images or any(ln.service_date for ln in d.lines) or d.service_from:
            out.append(d)
            continue
        found, said = set(), 0
        for line in redact.ocr(images[d.doc_id]):
            found |= _visit_date_in(line.text)
            said += len(VISIT_WORDS.findall(line.text))
        if said != 1 or len(found) != 1:  # one visit printed and read whole, or no date at all
            out.append(d)
            continue
        day = found.pop()
        problems.append(f"{d.doc_id}: date of service {day.isoformat()} from the sheet's heading, read on this device")
        out.append(d.model_copy(update={"lines": [ln.model_copy(update={"service_date": day}) if ln in undated else ln
                                                  for ln in d.lines]}))
    return out


def _cut(photo, sheet):
    """The same sheet, same corners, from another copy of the photo (the covering does not move pixels)."""
    import numpy as np
    from PIL import Image
    if sheet.corners:
        return Image.fromarray(sheetlib.straighten(np.array(photo), np.array(sheet.corners, dtype=np.float32)))
    return photo.crop(tuple(int(v) for v in sheet.box))


def _previews(files: list[tuple[str, bytes]]) -> dict[str, bytes]:
    """Page 1 of each PDF, small, for the documents column. Rendered here and never sent; a file that does not
    render just gets no picture."""
    out = {}
    for i, (_, raw) in enumerate(files, start=1):
        if not raw.startswith(b"%PDF"):
            continue
        try:
            page = pdfpages.render(raw, dpi=50)[0]
        except (pdfpages.RenderError, IndexError):
            continue
        page.thumbnail((420, 560))
        out[f"D{i}"] = _jpeg(page.convert("RGB"))
    return out


def _what_was_sent(to_model, cut: list[sheetlib.Sheet], models: dict[str, str], per_row: int = 3):
    """Exactly the images that left the device: the blurred photo the sheet finder got, then every sheet as the
    reader got it."""
    from PIL import Image, ImageDraw

    tiles = [(f"to {_short(models['sheets'])}, blurred", to_model)] + [
        (f"to {_short(models['read'])}: sheet {s.doc_id[1:]}", s.image) for s in cut]
    cell_w, cell_h, gap, label_h = 640, 520, 28, 40
    rows = (len(tiles) + per_row - 1) // per_row
    canvas = Image.new("RGB", (per_row * cell_w + (per_row + 1) * gap, rows * (cell_h + label_h + gap) + gap),
                       pdfpages.CREAM)
    draw = ImageDraw.Draw(canvas)
    font = redact._font(26)
    for i, (label, img) in enumerate(tiles):
        r, c = divmod(i, per_row)
        x, y = gap + c * (cell_w + gap), gap + r * (cell_h + label_h + gap)
        t = (img if i == 0 else pdfpages.to_paper(img)).copy()  # the blurred photo stays whole; a sheet loses the table
        t.thumbnail((cell_w, cell_h))
        draw.text((x, y), label, fill=(30, 42, 46), font=font)
        pdfpages.paste_paper(canvas, t, x, y + label_h)
    return canvas


def _short(model: str) -> str:
    """Screen label of a model id: nvidia/Nemotron-3.5-Super-VL -> Nemotron-3.5-Super-VL."""
    return model.split("/")[-1]


def _vault(profile: Profile) -> redact.Vault:
    seed = {("PATIENT", p.name): f"[{p.token}]" for p in profile.people if p.name}
    loc = profile.local
    known = [("ADDR", loc.street), ("PHONE", loc.phone)]
    known += [("ADDR", line) for line in redact.city_line(loc.city, loc.state, loc.zip)]
    known += [("ACCT" if t.startswith("ACCT") else t.split("_")[0], v) for t, v in loc.tokens.items()]
    return redact.Vault(known=[(k, v) for k, v in known if v], seed=seed)


def _no_leaks(img, vault: redact.Vault, doc_id: str):
    """Every sheet is read back by OCR before it may leave; a second pass covers what the photo pass missed."""
    redact.learn(img, vault)  # what is labelled on this sheet, even if the photo pass could not read it
    if not redact.leaks(img, vault.secrets, vault.units, vault.name_words):
        return img
    img = redact.redact(img, vault).image  # learns what the photo pass missed; our tokens read back are not values
    left = redact.leaks(img, vault.secrets, vault.units, vault.name_words)
    for _ in range(2):  # only codes left: one that showed after the covers changed the text height (S8, clean pack)
        if not left or any(s.kind != "CODE" for s in left):
            break
        img = redact.cover_codes(img)
        left = redact.leaks(img, vault.secrets, vault.units, vault.name_words)
    if left:
        kinds = ", ".join(sorted({s.kind for s in left}))
        raise RealDataGate(f"sheet {doc_id}: {len(left)} private value(s) still readable after covering ({kinds}); not sending")
    return img


SECOND_READ = "BILLRADAR_SECOND_READ"  # "1": Nemotron 3 Ultra reads the case and the findings too


def check_and_hold(case: Case, report: checks.Report, client: NemotronClient | None = None
                   ) -> tuple[checks.Report, verifier.Verdict]:
    """Run the read-only checker; a finding it could not confirm is held back, not shown.
    With BILLRADAR_SECOND_READ=1 and a client, the second read's notes lower the confidence of what it doubts."""
    letters = writer.all_letters(case, report)
    verdict = verifier.verify(case, report, letters)
    if client is not None and os.environ.get(SECOND_READ) == "1":
        try:
            verdict = verifier.model_review(client, case, report, letters, verdict)
        except ModelError as exc:  # the second reading is optional: keep the code's result, say it did not run
            verdict.issues.append(verifier.Issue("model review", "note",
                                                 f"{verifier.checker_name(client)} review did not run: {exc}"))
    names = verifier.subjects(report)  # numbering of the full list, before anything is held back
    shown, held, counts = [], list(report.held_back), {}
    for f in report.findings:
        counts[f.kind.value] = counts.get(f.kind.value, 0) + 1
        errors = verdict.errors_for(verifier.subject(f, counts[f.kind.value]))
        if errors:
            held.append(f.model_copy(update={"detail": f.detail + " [held back by the checker: "
                                             + "; ".join(e.what for e in errors) + "]"}))
        else:
            shown.append(f)
    report = report.model_copy(update={"findings": shown, "held_back": held})
    return verifier.grade(case, report, verdict, names), verdict


def save(run_: CaseRun, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "case.json").write_text(run_.case.model_dump_json(indent=2))
    (out / "report.json").write_text(run_.report.model_dump_json(indent=2))
    v = run_.verdict
    (out / "problems.json").write_text(json.dumps({
        "problems": run_.problems, "timings": run_.timings, "replayed": run_.replayed, "redacted": run_.redacted,
        "provider": run_.provider,
        "checker": {"checked": v.checked, "ok": v.ok, "issues": [i.__dict__ for i in v.issues]} if v else None,
    }, indent=2))
    if run_.receipt:
        (out / "receipt.json").write_text(json.dumps(run_.receipt, indent=2))
    if run_.found_policy:
        (out / "policy-finder.json").write_text(json.dumps(run_.found_policy, indent=2))
    for i, ov in enumerate(run_.overlays, start=1):
        (out / f"overlay-{i}.jpg").write_bytes(ov)
    for i, sv in enumerate(run_.sent_views, start=1):
        (out / f"sent-{i}.jpg").write_bytes(sv)
    if run_.file_previews:
        (out / "files").mkdir(exist_ok=True)
        for doc_id, jpg in run_.file_previews.items():
            (out / "files" / f"{doc_id}.jpg").write_bytes(jpg)
    sheets_dir = out / "sheets"
    sheets_dir.mkdir(exist_ok=True)
    for s in run_.sheets:  # on screen a sheet is the paper, without the table around it
        pdfpages.to_paper(run_.originals.get(s.doc_id, s.image)).save(sheets_dir / f"{s.doc_id}.jpg", quality=88)
    if run_.originals:  # private mode: the covered cut each model got, next to the sheet as photographed
        (out / "sent-sheets").mkdir(exist_ok=True)
        for s in run_.sheets:
            pdfpages.to_paper(s.image).save(out / "sent-sheets" / f"{s.doc_id}.jpg", quality=88)
