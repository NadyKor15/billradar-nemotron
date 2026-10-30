"""Cover names, IDs, addresses and account numbers on the device, before any image goes to a model.

Local OCR (RapidOCR, ONNX models shipped inside the package, no network) finds the text lines.
A value is covered when it follows a label ("Account:", "Member ID", "Patient", ...), matches a
pattern (SSN, phone, a printed mailing or scan line), or is something the family told us on this
device (names, street with its apartment number, account numbers). Each value gets one token, the
same on every sheet ("[ACCT_1]"), drawn over it, so the model can still link a bill to its insurance
statement by token. Printed machine codes (the postal IMb under an address, DataMatrix, QR, 1D bars)
carry the same numbers for a scanner, so they are covered whole ("[CODE]"). Dates, service codes and
amounts stay readable: the checks need them.

`leaks()` runs OCR and the code finder again on what is about to be sent and reports any covered
value, apartment number, mailing line or machine code that can still be read. The pipeline refuses
to send an image with a leak.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

LABELS: list[tuple[str, list[str]]] = [  # longest first inside each kind; order of kinds matters for "name"
    # a personal code to see or pay the bill online: "your personal statement code - PxvEVrdx"
    ("ACCESS", ["statement code", "access code", "activation code", "online code", "web code", "user id", "login id"]),
    ("SSN", ["social security number", "social security", "ssn"]),
    ("STMT", ["statement number", "statement no", "statement #", "stmt #", "stmt"]),
    ("DOB", ["date of birth", "birth date", "dob"]),
    ("MEMBER", ["member id", "member #", "member number", "subscriber id", "subscriber #", "policy number",
                "policy #", "group number", "group #", "medical record number", "medical record", "mrn", "mr number",
                "mr #", "id #", "id number"]),
    ("ACCT", ["account number", "account no", "account #", "acct #", "acct no", "guarantor number", "guarantor no",
              "guarantor id", "guarantor #", "account", "acct"]),
    ("GROUP", ["group name", "employer name", "employer"]),  # before PATIENT: "Group Name: X" is not a person
    ("CLAIM", ["claim number", "claim #", "claim no", "claim id", "claim"]),
    ("PATIENT", ["patient name", "patient", "guarantor", "subscriber", "member name", "member", "name"]),
    ("ADDR", ["address", "street"]),
    ("PHONE", ["telephone", "phone", "tel"]),
]
PATTERNS = [
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("PHONE", re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}-\d{4}\b")),
]
# Printed mailing and scan lines carry no label but identify the letter and often the account:
#   "H2PX375M007494" (letters and digits in five runs or more), "449325560190000000DANIELA..." (a payment
#   scan line), "HLNX-H2PX375M-007494-001OF001" (four groups or more, 8 digits or more); see _mail_spans.
#   A service code with its words ("99222INITHOSPCARE") or a PO box has two runs and stays.
MAIL_WORD = re.compile(r"(?<![\w/-])[A-Z0-9]{10,}(?![\w/-])")
MAIL_GROUPS = re.compile(r"(?<![\w/#-])[A-Z0-9#]+(?:\s?-\s?[A-Z0-9#]+){2,}(?![\w/#-])")
# A residential line with its unit, for addresses the family did not type in: "9349 SEAGRAPE LN # 717", "12 Oak St Apt 4B".
# "Suite" is left out: that is how offices print theirs.
STREET_UNIT = re.compile(r"(?i)\b\d{1,6}\s*[a-z][a-z .]*?(?:ln|lane|st|street|ave|avenue|rd|road|dr|drive|way|blvd|"
                         r"ct|court|pl|place|cir|circle|ter|terrace|pkwy|hwy|trl)\.?\s*(?:apt|apartment|unit|#)\.?\s*#?\s*"
                         r"[a-z0-9-]{1,6}\b")
ZIP4 = re.compile(r"^\s?-\s?\d{4}(?!\d)")
HEADER_KINDS = ("ACCT", "MEMBER", "CLAIM", "ACCESS")
ID_ALONE = re.compile(r"^\W*([A-Za-z0-9][A-Za-z0-9-]{3,})\W*$")
UNIT_ALONE = re.compile(r"(?i)^\W*(?:(?:apt|apartment|unit)\.?\s*)?#?\s*([a-z]?\d{1,5}[a-z]?)\W*$")  # "STE 400" is an office
SEP = re.compile(r"^[\s:#.\-]*")
WORD_KINDS = {"PATIENT", "ADDR", "GROUP"}
# One of our own tokens drawn on the image, as OCR reads it back: "[PATIENT_1]", "L[PATIENT_1]", "[CIAIM81"
TOKEN_RX = re.compile(r"\[\s*[A-Z1Il]{3,8}\s*_?\s*\d+")
# The id itself at the start of a labelled value: "3270189, Provider: ..." -> "3270189"
ID_RX = re.compile(r"[\w/-]+(?:[ -][\w/-]*\d[\w/-]*)*")


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


GLUED_TOKEN = re.compile(r"\[\s*(?:CODE|MAIL|ADDR|ACCT|PATIENT|MEMBER|CLAIM|PHONE|GROUP|ACCESS|STMT)\s*(?:_\s*\d+)?\s*\]?")
TOKEN_KINDS = ["PATIENT", "MEMBER", "ACCESS", "CLAIM", "GROUP", "PHONE", "ACCT", "ADDR", "MAIL", "CODE", "STMT"]
_LOOKALIKE = str.maketrans("1L|!0", "IIIIO")


def _token_readback(value: str) -> bool:
    """A value that is one of our tokens as OCR misread it: a kind's name, one letter off, and a digit."""
    if not re.search(r"\d", value):
        return False
    v = re.sub(r"[^A-Z]", "", value.upper().translate(_LOOKALIKE))
    for kind in TOKEN_KINDS:
        k = kind.translate(_LOOKALIKE)
        for i in range(len(v) - len(k) + 1):
            if sum(a != b for a, b in zip(v[i:i + len(k)], k)) <= 1:
                return True
    return False


@cache
def _engine():
    import cv2
    from rapidocr_onnxruntime import RapidOCR
    # opencv 5.0's multithreaded resize (KleidiCV) crashed once with EXC_BAD_ACCESS on a loaded machine
    # (27.10.2026, flaky). 0 = run opencv sequentially; on macOS (GCD backend) any value above 0 is ignored.
    cv2.setNumThreads(0)
    threads = int(os.environ.get("BILLRADAR_OCR_THREADS", "0") or 0)  # 0: let onnxruntime decide
    return RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1) if threads > 0 else RapidOCR()


@dataclass
class Line:
    box: np.ndarray  # 4x2 corners
    text: str
    score: float


def ocr(img: Image.Image) -> list[Line]:
    res, _ = _engine()(np.asarray(img.convert("RGB")))
    return [Line(np.array(b, dtype=np.float32), t, float(s)) for b, t, s in (res or [])]


def ocr_turned(img: Image.Image) -> list[Line]:
    """ocr() plus the lines printed up a margin ("HLNX - H2PX375M - 007494 - 001 OF 001" along a statement's edge):
    the left and right margins read turned a quarter, kept only where they stand upright on the page, boxes in page
    coordinates. One turn reads both directions: OCR turns upside-down lines itself."""
    lines = ocr(img)
    seen = {compact(line.text) for line in lines}
    h, band = img.height, max(1, int(0.15 * img.width))
    for x0 in sorted({0, img.width - band}):  # the two margins only: that is where such lines are printed
        strip = img.crop((x0, 0, x0 + band, h))
        for line in ocr(strip.rotate(-90, expand=True)):  # clockwise: strip (x, y) -> (h - 1 - y, x)
            box = np.stack([line.box[:, 1] + x0, h - 1 - line.box[:, 0]], axis=1).astype(np.float32)
            w_, h_ = np.ptp(box, axis=0)
            if h_ > 1.5 * w_ and len(compact(line.text)) >= 4 and compact(line.text) not in seen:
                lines.append(Line(box, line.text, line.score))
                seen.add(compact(line.text))
    return lines


@dataclass
class Secret:
    kind: str
    value: str
    token: str


@dataclass
class Redaction:
    image: Image.Image
    secrets: list[Secret] = field(default_factory=list)  # every value covered on this image
    covered: int = 0

    @property
    def tokens(self) -> dict[str, str]:
        return {s.token: s.value for s in self.secrets}


STATE_NAMES = dict(pair.split(":") for pair in (
    "AL:Alabama|AK:Alaska|AZ:Arizona|AR:Arkansas|CA:California|CO:Colorado|CT:Connecticut|DE:Delaware|"
    "DC:District of Columbia|FL:Florida|GA:Georgia|HI:Hawaii|ID:Idaho|IL:Illinois|IN:Indiana|IA:Iowa|KS:Kansas|"
    "KY:Kentucky|LA:Louisiana|ME:Maine|MD:Maryland|MA:Massachusetts|MI:Michigan|MN:Minnesota|MS:Mississippi|"
    "MO:Missouri|MT:Montana|NE:Nebraska|NV:Nevada|NH:New Hampshire|NJ:New Jersey|NM:New Mexico|NY:New York|"
    "NC:North Carolina|ND:North Dakota|OH:Ohio|OK:Oklahoma|OR:Oregon|PA:Pennsylvania|RI:Rhode Island|"
    "SC:South Carolina|SD:South Dakota|TN:Tennessee|TX:Texas|UT:Utah|VT:Vermont|VA:Virginia|WA:Washington|"
    "WV:West Virginia|WI:Wisconsin|WY:Wyoming").split("|"))


def city_line(city: str, state: str, zip_code: str) -> list[str]:
    """'Bayside', 'FL', '33067' -> ['Bayside FL 33067', 'Bayside Florida 33067']: the city line as papers print it.
    City and ZIP together only: a bare ZIP would also match digits inside an amount."""
    if not (city.strip() and zip_code.strip()):
        return []
    st = state.strip()
    names = [st] + ([STATE_NAMES[st.upper()]] if st.upper() in STATE_NAMES else [])
    return [f"{city.strip()} {n} {zip_code.strip()}".replace("  ", " ") for n in names]


def name_variants(name: str) -> list[str]:
    """How papers print a person: 'Adrian James Castillo' -> 'Castillo Adrian James', 'Castillo Adrian',
    'Adrian Castillo'. Commas and case do not matter (values are compared letters and digits only)."""
    words = re.findall(r"[A-Za-z'-]+", name)
    if len(words) < 2:
        return []
    first, last, middle = words[0], words[-1], words[1:-1]
    out = [" ".join([last, first, *middle]), f"{last} {first}"]
    if middle:
        out.append(f"{first} {last}")
    return [v for v in dict.fromkeys(out) if compact(v) != compact(name)]


UNIT_RX = re.compile(r"[\s,]+(?:apt|apartment|unit|suite|ste|#)\.?\s*#?\s*(\w+)\s*$", re.I)
UNIT_WORDS = ["#", "Apt", "Apartment", "Unit", "Ste", "Suite"]


def _street_only(address: str) -> str:
    """'4089 Coral Reef Way Apt 400' -> '4089 Coral Reef Way': papers print the unit as 'Apt', '#' or 'Unit'."""
    return UNIT_RX.sub("", address).strip()


def _with_units(address: str) -> list[str]:
    """'9349 Seagrape Ln Apt 717' -> the same street with the unit as papers print it: '... Ln # 717', '... Ln Unit 717'.
    The street alone is covered too; without these the apartment number would stay readable next to its token."""
    m = UNIT_RX.search(address)
    if not m:
        return []
    street = address[:m.start()].strip()
    return [f"{street} {w} {m.group(1)}" for w in UNIT_WORDS]


def _mail_spans(text: str) -> list[tuple[int, int]]:
    out = []
    for m in MAIL_GROUPS.finditer(text):  # four groups and 8 digits, or three and 12: "292006-143-69524025"
        groups, digits = len(re.split(r"\s?-\s?", m.group())), len(re.sub(r"\D", "", m.group()))
        if (groups >= 4 and digits >= 8) or digits >= 12:
            out.append((m.start(), m.end()))
    for m in MAIL_WORD.finditer(text):
        v = m.group()
        runs, letters = re.findall(r"[A-Z]+|\d+", v), re.findall(r"[A-Z]+", v)
        digits, n_letters = len(re.sub(r"\D", "", v)), sum(map(len, letters))
        # "H2PX375M007494": five runs, letter runs short (a drug name in a merged service line is long);
        # a scan line: 20 digits, or 14 with a name between them. A date run into a revenue and a service code
        # ("20250302030850524HC", 17 digits) is a bill line the checks need.
        name_inside = n_letters >= 5 and re.search(r"\d{6}[A-Z]{5,}\d{6}", v)  # digits, a name, digits again
        if (len(runs) >= 5 and all(len(x) <= 4 for x in letters)) or digits >= 20 or (digits >= 14 and name_inside):
            out.append((m.start(), m.end()))
    return out


class Vault:
    """Value -> token, shared across all images of one case so the same account gets the same token."""

    def __init__(self, known: list[tuple[str, str]] | None = None, seed: dict[tuple[str, str], str] | None = None):
        """known: values to look for anywhere (names, street, account numbers the family typed in).
        seed: values whose token is fixed already, e.g. the mother's name -> "[PATIENT_1]"."""
        self.by_value: dict[tuple[str, str], Secret] = {}
        self.counts: dict[str, int] = {}
        seed = dict(seed or {})
        given = [value for kind, value in seed if kind == "PATIENT"]  # as the family typed them, first name first
        for (kind, value), token in list(seed.items()):
            if kind == "PATIENT":  # "Castillo, Daniela" is the same person as "Daniela Castillo"
                for v in name_variants(value):
                    seed.setdefault((kind, v), token)
        for (kind, value), token in seed.items():
            self.by_value[(kind, compact(value))] = Secret(kind, value, token)
            n = int(re.sub(r"\D", "", token) or 0)
            self.counts[kind] = max(self.counts.get(kind, 0), n)
        self.seeded = dict(self.by_value)
        # a first or a last name printed alone ("Daniela's visit on February 11", "Castillo , [PATIENT_1]"), as a
        # whole word, also as OCR spells a double letter once ("Castilo")
        self.name_words: dict[str, Secret] = {}
        for value in given:
            words = re.findall(r"[A-Za-z'-]{3,}", value)
            if len(words) >= 2:
                for w in (words[0], words[-1]):
                    for form in {w.lower(), re.sub(r"(.)\1", r"\1", w.lower())}:
                        if len(form) >= 3:
                            self.name_words.setdefault(form, self.by_value[("PATIENT", compact(value))])
        known = (known or []) + list(seed.keys())
        homes = [v for k, v in known if k == "ADDR"]
        known += [("ADDR", street) for v in homes if (street := _street_only(v)) != v]
        known += [("ADDR", v) for home in homes for v in _with_units(home)]
        self.known = [(k, v) for k, v in dict.fromkeys(known) if len(compact(v)) >= 3]
        # the family's apartment numbers: a box that holds only "# 717" next to a covered street is covered too
        self.units = {compact(m.group(1)) for v in homes if (m := UNIT_RX.search(v))}

    def token_for_label(self, kind: str, value: str) -> Secret:
        """A labelled value that contains a name we already know ("Anna Sample" vs profile "Anna")
        keeps that person's token, so the model still sees whose bill it is."""
        if kind == "PATIENT":
            v = compact(value)
            for (k, known), secret in self.seeded.items():
                if k == kind and len(known) >= 3 and (known in v or v in known):
                    return secret
        return self.token(kind, value)

    def token(self, kind: str, value: str) -> Secret:
        key = (kind, compact(value))
        if key not in self.by_value:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            self.by_value[key] = Secret(kind, value.strip(), f"[{kind}_{self.counts[kind]}]")
        return self.by_value[key]

    @property
    def secrets(self) -> list[Secret]:
        return list(self.by_value.values())


def _spans(text: str, vault: Vault, learn: bool = True) -> list[tuple[int, int, Secret]]:
    """Character spans of `text` to cover, with the secret each one stands for. With learn=False (a second pass
    over an image already covered) a labelled value counts only if it is known already: what OCR reads over our
    own tokens ("[PATIENT_]", "rCI AIMR1") is not a new value to hide."""
    out: list[tuple[int, int, Secret]] = []
    low = text.lower()
    for kind, labels in LABELS:
        for label in labels:
            # a two-word label may sit inside words OCR ran together ("yourpersonalstatementcode-PxvEVrdx")
            before = "" if " " in label else r"(?<![a-z])"
            m = re.search(before + re.escape(label).replace(r"\ ", r"\s*") + r"(?![a-z])", low)
            if not m:
                continue
            rest = text[m.end():]
            sep = SEP.match(rest)
            start = m.end() + (sep.end() if sep else 0)
            value = text[start:].strip()
            if kind not in WORD_KINDS:
                m_id = ID_RX.match(value)
                value = m_id.group() if m_id else value
            if TOKEN_RX.search(value) or re.search(r"[\[\]_]", value) or _token_readback(value[:12]):
                break  # our own token read back by OCR ("[PATIENT_]", "rCI AIMR1"): not a value to hide
            if len(compact(value)) < 3:
                continue
            if kind in WORD_KINDS:
                # "Patient: Anna" yes; "Patient responsibility $2,575.10" no: words need a colon after the label
                if ":" not in (sep.group() if sep else ""):
                    continue
                if kind == "PATIENT" and not re.search(r"[A-Za-z]{2}", value):
                    continue
            elif kind == "ACCESS":  # letters only is fine ("PxvEVrdx"), a plain word is not ("below")
                if len(value) < 5 or not (re.search(r"\d", value) or (re.search("[a-z]", value) and re.search("[A-Z]", value))):
                    continue
            elif not re.search(r"\d", value):  # "Account Summary", "Claim Totals": no number, nothing to hide
                continue
            end = start + len(value) if kind not in WORD_KINDS else len(text)
            if not learn and (kind, compact(value)) not in vault.by_value:
                break
            out.append((start, end, vault.token_for_label(kind, value)))
            break
        if out:
            break
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            out.append((m.start(), m.end(), vault.token(kind, m.group())))
    for m in GLUED_TOKEN.finditer(text):  # a value whose label one of our covers clipped: "[CODE" + "TMT237851G7"
        tail = re.match(r"[A-Za-z]{0,6}\W{0,2}([A-Za-z0-9-]*\d[A-Za-z0-9-]*)(?![./,\d])", text[m.end():])
        if tail and len(re.sub(r"\D", "", tail.group(1))) >= 6:
            out.append((m.end() + tail.start(1), m.end() + tail.end(1), vault.token("MAIL", tail.group(1))))
    for word, secret in vault.name_words.items():
        for m in _name_word_rx(word).finditer(text):
            out.append((m.start(), m.end(), secret))
    for start, end in _mail_spans(text):
        out.append((start, end, vault.token("MAIL", text[start:end])))
    for m in STREET_UNIT.finditer(text):
        out.append((m.start(), m.end(), vault.token("ADDR", m.group())))
    for kind, value, secret in _everything_known(vault):
        # OCR may drop spaces: match on letters and digits only, map back to the original indexes
        idx = [i for i, ch in enumerate(text) if ch.isalnum()]
        hay = "".join(text[i] for i in idx).lower()
        needle = compact(value)
        pos = hay.find(needle)
        if pos < 0 and (part := _partial(needle, hay)):  # a number half under a cover reads one digit short
            pos, needle = part
        if pos >= 0:
            end = idx[pos + len(needle) - 1] + 1
            if kind == "ADDR" and re.search(r"\d{5}$", value) and (z4 := ZIP4.match(text[end:])):
                end += z4.end()  # "BAYSIDE FL 33067-4632": the +4 narrows it to a few houses
            out.append((idx[pos], end, secret or vault.token(kind, value)))
    return out


@cache
def _name_word_rx(word: str) -> re.Pattern:
    # OCR runs words together ("Daniela'svisit"): the name ends at a non-letter or where a capital starts the next word
    w = re.escape(word)
    return re.compile(r"(?<![A-Za-z])(?i:" + w + r")(?:['’]?s)?(?![A-Za-z])|(?<![A-Za-z])" + re.escape(word.capitalize())
                      + r"(?:['’]?s)?(?=[A-Z])")


def _partial(needle: str, hay: str) -> tuple[int, str] | None:
    """An all-digit value of 6 digits or more with its first or last digit missing, as a run of digits of its own."""
    if not needle.isdigit() or len(needle) < 6:
        return None
    for part in (needle[:-1], needle[1:]):
        for m in re.finditer(re.escape(part), hay):
            before = hay[m.start() - 1] if m.start() else ""
            after = hay[m.end()] if m.end() < len(hay) else ""
            if not before.isdigit() and not after.isdigit():
                return m.start(), part
    return None


def _everything_known(vault: Vault) -> list[tuple[str, str, Secret | None]]:
    """What the family typed in, plus every value already found next to a label on any sheet:
    an account number labelled on one sheet is covered on the next sheet too, label or not."""
    seen, out = set(), []
    for kind, value in vault.known:
        out.append((kind, value, None))
        seen.add(compact(value))
    for s in vault.secrets:
        v = compact(s.value)
        if len(v) >= 3 and v not in seen and not TOKEN_RX.search(s.value):
            out.append((s.kind, s.value, s))
            seen.add(v)
    return out


def _alnum_map(text: str) -> tuple[list[int], str]:
    idx = [i for i, ch in enumerate(text) if ch.isalnum()]
    return idx, "".join(text[i] for i in idx).lower()


def _below(a: Line, b: Line) -> bool:
    """b is the next line of the same block: just under a, starting about where a starts."""
    ax0, ay0 = a.box.min(axis=0)
    _, ay1 = a.box.max(axis=0)
    bx0, by0 = b.box.min(axis=0)
    h = max(1.0, ay1 - ay0)
    return 0 <= by0 - ay0 <= 2.6 * h and abs(bx0 - ax0) <= 1.5 * h


def _split_spans(lines: list[Line], vault: Vault) -> list[tuple[Line, int, int, Secret]]:
    """Values printed over two lines ("4089 Coral Reef Way" / "Apt 400"), or read as two boxes on one line
    ("Castillo ," / "Daniela"): no single box holds them."""
    out = []
    known = [(kind, compact(value), value, secret) for kind, value, secret in _everything_known(vault)]
    maps = [_alnum_map(line.text) for line in lines]
    for a, (ia, ha) in zip(lines, maps):
        for b, (ib, hb) in zip(lines, maps):
            if b is a or not ib or not ia or not (_below(a, b) or _same_row_right(a, b)):
                continue
            for kind, needle, value, secret in known:
                if needle in ha or needle in hb:
                    continue  # one line has it whole; the line pass covers it
                pos = (ha + hb).find(needle)
                if pos < 0 or pos >= len(ha) or pos + len(needle) <= len(ha):
                    continue
                secret = secret or vault.token(kind, value)
                out.append((a, ia[pos], ia[-1] + 1, secret))
                out.append((b, ib[0], ib[pos + len(needle) - len(ha) - 1] + 1, secret))
    return out


@cache
def _font(size: int):
    for path in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cover(draw: ImageDraw.ImageDraw, line: Line, start: int, end: int, token: str, others: list[Line] = ()) -> None:
    """Draw the token over characters start..end of the line. `others`: the sheet's other lines; the cover stops
    halfway to the line printed above or below, so a tightly set "$275.00" between a covered account number and a
    covered name stays readable."""
    n = max(1, len(line.text))
    x0, y0 = line.box.min(axis=0)
    x1, y1 = line.box.max(axis=0)
    w, h = x1 - x0, y1 - y0
    if h > 1.5 * w:  # a line up the margin: covered whole, its token written across
        pad = max(4.0, 0.15 * w)
        draw.rectangle((int(max(0, x0 - pad)), int(max(0, y0 - pad)), int(x1 + pad), int(y1 + pad)), fill=(250, 250, 248))
        draw.text((int(max(0, x0 - pad)), int(y0 + 4)), token, fill=(40, 80, 160), font=_font(max(10, int(w * 0.6))))
        return
    pad_x = max(6.0, 0.04 * w)
    left = x0 + w * start / n - pad_x
    right = x0 + w * end / n + pad_x
    top, bottom = y0 - 0.12 * h, y1 + 0.12 * h
    mid = (y0 + y1) / 2
    for b in others:
        if b is line:
            continue
        bx0, by0 = b.box.min(axis=0)
        bx1, by1 = b.box.max(axis=0)
        if min(bx1, right) <= max(bx0, left):
            continue
        bmid = (by0 + by1) / 2
        if bmid < mid - 0.5 * h:
            top = max(top, (bmid + mid) / 2)
        elif bmid > mid + 0.5 * h:
            bottom = min(bottom, (bmid + mid) / 2)
    rect = (int(max(0, left)), int(max(0, top)), int(right), int(bottom))
    draw.rectangle(rect, fill=(250, 250, 248))
    size = max(8, int(min(h * 0.6, (rect[3] - rect[1]) * 0.9)))
    while size > 8 and draw.textlength(token, font=_font(size)) > rect[2] - rect[0] - 6:  # stays inside its cover
        size -= 1
    draw.text((rect[0] + 4, rect[1] + max(1, (rect[3] - rect[1] - size) // 2)), token, fill=(40, 80, 160), font=_font(size))


def _text_height(lines: list[Line], img_h: int) -> float:
    hs = [float(np.ptp(line.box[:, 1])) for line in lines if line.score >= 0.6 and len(compact(line.text)) >= 2]
    hs = [h for h in hs if h > 0]
    return float(np.median(hs)) if hs else max(8.0, 0.012 * img_h)


def _ink(img: Image.Image, h: float) -> np.ndarray:
    import cv2
    gray = np.asarray(img.convert("L"))
    block = max(9, int(0.6 * h) | 1)  # about a letter wide: inside a dense code the modules still come apart
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block, 15)


def _in_text(box: tuple[int, int, int, int], lines: list[Line]) -> bool:
    """Most of the box sits inside a line OCR read as text: a letter, not a code."""
    x0, y0, x1, y1 = box
    area = max(1, (x1 - x0) * (y1 - y0))
    for line in lines:
        if line.score < 0.6 or len(compact(line.text)) < 2:
            continue
        lx0, ly0 = line.box.min(axis=0)
        lx1, ly1 = line.box.max(axis=0)
        w, hh = min(x1, lx1) - max(x0, lx0), min(y1, ly1) - max(y0, ly0)
        if w > 0 and hh > 0 and w * hh >= 0.4 * area:
            return True
    return False


def _bar_runs(ink: np.ndarray, h: float) -> list[tuple[int, int, int, int]]:
    """Rows of thin bars set close together: the postal IMb (four heights), Code 128 and the like.
    Twenty elements or more, five of them tall; a dotted leader line ("Total .....") has no tall ones."""
    import cv2
    n, _, st, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    elems = sorted((int(x), int(y), int(w), int(hh)) for x, y, w, hh, a in st[1:]
                   if w <= max(2.0, 0.3 * h) and 0.12 * h <= hh <= 5 * h and a >= 2)
    runs: list[list[tuple[int, int, int, int]]] = []
    for e in elems:  # each element joins the run it continues; text just above or below keeps its own runs
        for run in runs:
            px, py, pw, ph = run[-1]
            gap = e[0] - (px + pw)
            if -1 <= gap <= max(3.0, 0.45 * h) and min(py + ph, e[1] + e[3]) - max(py, e[1]) >= 1:
                run.append(e)
                break
        else:
            runs.append([e])
    out = []
    for r in runs:
        if len(r) >= 20 and sum(e[3] >= 2 * e[2] and e[3] >= 0.4 * h for e in r) >= 5:
            out.append((min(e[0] for e in r), min(e[1] for e in r), max(e[0] + e[2] for e in r), max(e[1] + e[3] for e in r)))
    return out


def _squares(ink: np.ndarray, h: float, lines: list[Line], rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Dense square patches outside the text: DataMatrix and QR. Ink crosses each row and each column of
    the patch many times and is black; a letter, a ring or icon, a coloured logo or a box around a form field
    is not like that."""
    import cv2
    k = max(2, int(round(0.12 * h)))
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)  # a code inside a printed box too
    out = []
    for c in contours:
        x, y, w, hh = cv2.boundingRect(c)
        square = 0.7 * h <= w <= 10 * h and 0.75 <= w / max(1, hh) <= 1.33
        block = 0.6 * h <= hh <= 3 * h and w >= 1.8 * hh  # 1D bars printed so fine they run together
        if not (square or block):
            continue
        patch = ink[y:y + hh, x:x + w] > 0
        fill = patch.mean()
        across = np.abs(np.diff(patch.astype(np.int8), axis=1)).sum(axis=1).mean()
        down = np.abs(np.diff(patch.astype(np.int8), axis=0)).sum(axis=0).mean()
        crossings = min(across, down)  # big modules cross fewer times per pixel, but many times in all
        if square and not (0.3 <= fill <= 0.85 and (crossings >= 10 or crossings >= max(3.0, 0.16 * max(w, hh)))):
            continue
        # bars run the full height of the code; letters, and words joined by an underline, do not
        if not square and not (0.3 <= fill <= 0.9 and across >= max(8.0, 0.2 * w) and down <= 3
                               and (patch.mean(axis=0) >= 0.85).mean() >= 0.3):
            continue
        px = rgb[y:y + hh, x:x + w][patch].astype(np.int16)
        colour = float((px.max(axis=1) - px.min(axis=1)).mean()) if len(px) else 0.0
        if colour <= 40 and not _in_text((x, y, x + w, y + hh), lines):
            out.append((x, y, x + w, y + hh))
    return out


def _qr(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """QR codes by their three finder squares (opencv), also when a label printed under the code ("SCAN TO PAY")
    joins it into one blob that is no longer square."""
    import cv2
    try:
        ok, pts = cv2.QRCodeDetector().detectMulti(np.asarray(img.convert("L")))
    except cv2.error:
        return []
    if not ok or pts is None:
        return []
    return [(int(q[:, 0].min()), int(q[:, 1].min()), int(q[:, 0].max()) + 1, int(q[:, 1].max()) + 1) for q in pts]


def codes(img: Image.Image, lines: list[Line] | None = None) -> list[tuple[int, int, int, int]]:
    """Printed machine codes on the image, as boxes (x0, y0, x1, y1)."""
    lines = ocr(img) if lines is None else lines
    h = _text_height(lines, img.height)
    ink = _ink(img, h)
    return _bar_runs(ink, h) + _squares(ink, h, lines, np.asarray(img.convert("RGB"))) + _qr(img)


def _cover_box(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], h: float, token: str = "[CODE]") -> None:
    x0, y0, x1, y1 = box
    pad = max(4, int(0.3 * h))
    rect = (max(0, x0 - pad), max(0, y0 - pad), x1 + pad, y1 + pad)
    draw.rectangle(rect, fill=(250, 250, 248))
    size = max(10, int(min(h * 0.6, (rect[3] - rect[1]) * 0.8)))
    draw.text((rect[0] + 2, rect[1] + max(1, (rect[3] - rect[1] - size) // 2)), token, fill=(40, 80, 160), font=_font(size))


def _same_row(a: Line, b: Line) -> bool:
    _, ay0 = a.box.min(axis=0)
    _, ay1 = a.box.max(axis=0)
    _, by0 = b.box.min(axis=0)
    _, by1 = b.box.max(axis=0)
    return min(ay1, by1) - max(ay0, by0) >= 0.5 * min(ay1 - ay0, by1 - by0)


def _same_row_right(a: Line, b: Line) -> bool:
    """b starts just right of a on the same printed line ("[ADDR_1]" then "# 717")."""
    ax0, ay0 = a.box.min(axis=0)
    ax1, ay1 = a.box.max(axis=0)
    bx0, by0 = b.box.min(axis=0)
    _, by1 = b.box.max(axis=0)
    h = max(1.0, ay1 - ay0)
    overlap = min(ay1, by1) - max(ay0, by0)
    return overlap >= 0.5 * min(h, by1 - by0) and -0.5 * h <= bx0 - ax1 <= 4 * h


def _header_values(lines: list[Line], vault: Vault, learn: bool) -> list[tuple[Line, Secret]]:
    """A column header with its value in the cell under it: "Patient Account Num" / "4788416", or one column of a
    header row ("Statement Date  Due Date  Guarantor #  Amount Due" / "... 847673833 ..."): the value must sit
    under the label's own words."""
    out = []
    for a in lines:
        idx, flat = _alnum_map(a.text)  # OCR runs header words together: "PatientAccount Num"
        hit = next(((k, flat.find(compact(lb)), len(compact(lb))) for k, labels in LABELS if k in HEADER_KINDS
                    for lb in labels if len(compact(lb)) >= 4 and compact(lb) in flat), None)
        if not hit or len(a.text.split()) > 12:
            continue
        kind, pos, n = hit
        ax0, _ = a.box.min(axis=0)
        ax1, ay1 = a.box.max(axis=0)
        h = max(1.0, float(np.ptp(a.box[:, 1])))
        per_char = (ax1 - ax0) / max(1, len(a.text))
        lx0, lx1 = ax0 + idx[pos] * per_char, ax0 + (idx[pos + n - 1] + 1) * per_char
        if re.search(r"\d", a.text[idx[pos]:]):  # the label has its value on its own line: the line pass covers it
            continue
        for b in lines:
            bx0, by0 = b.box.min(axis=0)
            bx1, _ = b.box.max(axis=0)
            # "Account Number:" with its value further along the same row, in a box of its own
            if b is not a and _same_row(a, b) and 0 <= bx0 - ax1 <= 20 * h and a.text.rstrip().endswith((":", "#")):
                digits = len(re.sub(r"\D", "", b.text))
                if digits >= 5 and not re.search(r"[$/]", b.text) and ID_RX.fullmatch(b.text.strip()) and (
                        learn or (kind, compact(b.text)) in vault.by_value):
                    out.append((b, 0, len(b.text), vault.token(kind, b.text.strip())))
                continue
            if b is a or not (0 <= by0 - ay1 <= 1.5 * h) or min(lx1, bx1) - max(lx0, bx0) <= -3 * per_char:
                continue
            b_char = (bx1 - bx0) / max(1, len(b.text))
            for m in re.finditer(r"\S+", b.text):  # the cell under the label: a whole row may read as one line
                cx0, cx1 = bx0 + m.start() * b_char, bx0 + m.end() * b_char
                value = m.group().strip(".,;:()")
                if min(lx1, cx1) - max(lx0, cx0) <= -2 * per_char or re.search(r"[$/]", value):
                    continue
                if len(re.sub(r"\D", "", value)) < 5 or not ID_ALONE.match(value):
                    continue
                if not learn and (kind, compact(value)) not in vault.by_value:
                    continue
                out.append((b, m.start(), m.end(), vault.token(kind, value)))
    return out


# "77953", "480004 - 440", "4744 500976": digits in one to three groups, five at least, no $, no decimals
MAIL_NUMBER = re.compile(r"^\W*(?=(?:\D*\d){5})\d{2,12}(?:\s?[-\s]\s?\d{2,12}){0,2}\W*$")  # OCR may drop the space


def _near(box: tuple, line: Line, h: float) -> bool:
    x0, y0 = line.box.min(axis=0)
    x1, y1 = line.box.max(axis=0)
    dx = max(box[0] - x1, x0 - box[2], 0)
    dy = max(box[1] - y1, y0 - box[3], 0)
    return dx <= 4 * h and dy <= 1.5 * h


def _mail_numbers(lines: list[Line], anchors: list[tuple], h: float) -> list[Line]:
    """A bare number printed by a barcode or in the address block ("77953" by the IMb, "480004 - 440" over the
    addressee): the mail room's piece and sort numbers, which point at this letter. Never a number in a row of a
    table (a service or revenue code, a charge): its row holds an amount, a date or words."""
    out = []
    money = re.compile(r"\$|\d[.,]\d{2}\b")
    in_tables = {t for ln in lines if money.search(ln.text) for o in lines if _same_row(ln, o)
                 for t in re.findall(r"\d{4,6}", o.text)}  # codes on rows that carry an amount: service codes
    for ln in lines:
        if not MAIL_NUMBER.match(ln.text) or not any(_near(a, ln, h) for a in anchors):
            continue
        if re.sub(r"\D", "", ln.text) in in_tables:  # "76815" in the legend is the code billed on a claim line
            continue
        row = [o for o in lines if o is not ln and _same_row(ln, o)]
        if any(re.search(r"\$|\d[.,]\d{2}\b|\d{1,2}/\d{1,2}/\d{2,4}", o.text) for o in row):
            continue  # a charge or a date in its row: a line of the bill, not of the mail room
        x1 = float(ln.box[:, 0].max())
        if any(re.search(r"[A-Za-z]{3}", o.text) and not TOKEN_RX.search(o.text) and _box(o) not in anchors
               and 0 <= float(o.box[:, 0].min()) - x1 <= 3 * h for o in row):
            continue  # its description follows it ("76815 OB US, LIMITED"); a logo to its left does not count
        out.append(ln)
    return out


def _box(line: Line) -> tuple:
    x0, y0 = line.box.min(axis=0)
    x1, y1 = line.box.max(axis=0)
    return (x0, y0, x1, y1)


UNIT_ANYWHERE = re.compile(r"(?i)(?:apt|apartment|unit|#)\.?\s*#?\s*([a-z]?\d{1,5}[a-z]?)\b")


def _ink_left(img: Image.Image, box: tuple, h: float, lines: list[Line] = ()) -> tuple[int, int] | None:
    """Ink printed just left of an address line, in its band, that OCR may not read at all (a faint sort line
    "4744 500976" in 6-point type): the columns [x0, x1) it spans, or None. Our blue tokens are not ink."""
    x0, y0, x1, y1 = (int(v) for v in box)
    left = max(0, int(x0 - 8 * h))
    if x0 - left < 4:
        return None
    a = np.asarray(img.convert("RGB").crop((left, max(0, y0), x0, y1)), dtype=np.int16)
    if a.size == 0:
        return None
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    ink = ((r + g + b) / 3 < 170) & ~((b - r) > 40)
    for ln in lines:  # what OCR read is checked by the other rules; only ink it could not read counts here
        lx0, ly0, lx1, ly1 = (int(v) for v in _box(ln))
        if (lx0, ly0, lx1, ly1) == (x0, y0, x1, y1) or GLUED_TOKEN.search(ln.text):
            continue  # our own tokens are no text to trust: a box "[CODE] [ADDR_1]" may sit over a faint number
        ink[max(0, ly0 - max(0, y0)):max(0, ly1 - max(0, y0)), max(0, lx0 - left):max(0, lx1 - left)] = False
    cols = np.where(ink.sum(axis=0) >= 2)[0]
    if not len(cols):
        return None
    gap, start = int(1.5 * h), None  # the run of ink that reaches the address cover, gaps under 1.5 lines
    edge = x0 - left
    for c in cols[::-1]:
        if edge - c > gap:
            break
        start, edge = c, c
    return (left + int(start), x0) if start is not None else None


def _units_left(lines: list[Line], units: set[str], anywhere: bool = False) -> list[Line]:
    """Lines that still print one of the family's apartment numbers: alone ("# 717"), or with anywhere=True also
    after a word or token ("[ADDR_1] # 717", "Apt 717")."""
    out = []
    for line in lines:
        m = UNIT_ALONE.match(line.text)
        hits = [m.group(1)] if m else []
        if anywhere:
            hits += [x.group(1) for x in UNIT_ANYWHERE.finditer(line.text)]
        if any(compact(x) in units for x in hits):
            out.append(line)
    return out


def redact(img: Image.Image, vault: Vault, learn: bool = True) -> Redaction:
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    covered = 0
    used: dict[str, Secret] = {}
    lines = ocr_turned(out)
    found = codes(out, lines)  # on the image as photographed, before any cover is drawn
    h = _text_height(lines, out.height)
    if learn:  # every value on the sheet first, so a number labelled low on the page is covered higher up too
        for line in lines:
            _spans(line.text, vault)
        _header_values(lines, vault, learn)
    for line in lines:
        if re.match(r"\W*\[\s*ADDR", line.text) and (ink := _ink_left(img, _box(line), h, lines)):
            bx0, by0, bx1, by1 = _box(line)  # a second pass: ink left beside an address already covered
            draw.rectangle((ink[0] - 2, int(by0), int(bx0) + 2, int(by1)), fill=(250, 250, 248))
        for start, end, secret in _spans(line.text, vault, learn):
            if secret.kind == "ADDR" and start > 1 and re.fullmatch(r"\W*\d[\d\s-]{3,}\W*", line.text[:start]):
                start = 0  # OCR ran the sort number into the address: "4744500976BAYSIDEFL33067"
            if secret.kind == "ADDR" and start <= 1 and (ink := _ink_left(img, _box(line), h, lines)):
                bx0, by0, bx1, by1 = _box(line)  # a faint number printed right before the address line
                draw.rectangle((ink[0] - 2, int(by0), int(bx0) + 2, int(by1)), fill=(250, 250, 248))
            _cover(draw, line, start, end, secret.token, lines)
            used[secret.token] = secret
            covered += 1
            if secret.kind == "ADDR" and end >= len(line.text.rstrip()):  # the unit or the +4 printed apart
                right = [x for x in lines if x is not line and _same_row_right(line, x)]
                apart = _units_left(right, vault.units)
                if re.search(r"\d{5}$", secret.value):
                    apart += [x for x in right if ZIP4.match(x.text.strip()) and len(x.text.strip()) <= 6]
                for b in apart:
                    _cover(draw, b, 0, len(b.text), secret.token, lines)
                    covered += 1
    for line, start, end, secret in _header_values(lines, vault, learn):
        _cover(draw, line, start, end, secret.token, lines)
        used[secret.token] = secret
        covered += 1
    for line, start, end, secret in _split_spans(lines, vault):
        _cover(draw, line, start, end, secret.token, lines)
        used[secret.token] = secret
        covered += 1
    address = [_box(ln) for ln in lines for _, _, sec in _spans(ln.text, vault, False) if sec.kind == "ADDR"]
    address += [_box(ln) for ln in lines if re.search(r"\[\s*(?:CODE|ADDR)", ln.text)]  # a second pass: our own covers
    for ln in _mail_numbers(lines, list(found) + address, h):  # covered where it stands, never learned:
        _cover(draw, ln, 0, len(ln.text), "[MAIL]", lines)  # a short number known everywhere would eat codes and sums
        covered += 1
    for box in found:
        _cover_box(draw, box, h)
        covered += 1
    return Redaction(out, list(used.values()), covered)


def cover_codes(img: Image.Image) -> Image.Image:
    """Cover the machine codes found on this image as it is now. Covering text shifts the text height the code finder
    sizes by, so a small code can show only after the first covers are drawn; the gate calls this then."""
    out = img.convert("RGB").copy()
    lines = ocr_turned(out)
    found = codes(out, lines)
    if found:
        draw, h = ImageDraw.Draw(out), _text_height(lines, out.height)
        for box in found:
            _cover_box(draw, box, h)
    return out


def learn(img: Image.Image, vault: Vault) -> int:
    """Register every labelled value on this image (no drawing). The gate runs it on each cut sheet first, so a
    value the photo pass could not read is still known when the sheet is checked. Returns how many there were."""
    lines = ocr_turned(img)
    before = len(vault.by_value)
    for line in lines:
        _spans(line.text, vault)
    _header_values(lines, vault, True)
    return len(vault.by_value) - before


def leaks(img: Image.Image, secrets: list[Secret], units: set[str] | frozenset = frozenset(),
          names: dict[str, Secret] | None = None) -> list[Secret]:
    """What can still be read on this image: covered values, the family's apartment number on its own, mailing
    and scan lines, residential lines with a unit, machine codes. Must be empty before sending."""
    lines = ocr_turned(img)
    text = " ".join(compact(line.text) for line in lines)
    found = []
    for s in secrets:
        v = compact(s.value)
        if not v:
            continue
        digits = re.sub(r"\D", "", v)
        if v in text or (len(digits) >= 6 and digits in re.sub(r"\D", "", text)) or _partial(v, text):
            found.append(s)
    for line in lines:
        found += [Secret("PATIENT", w, "[PATIENT]") for w in (names or {}) if _name_word_rx(w).search(line.text)]
        if TOKEN_RX.fullmatch(line.text.strip().rstrip("]")):
            continue
        for start, end in _mail_spans(line.text):
            found.append(Secret("MAIL", line.text[start:end], "[MAIL]"))
        found += [Secret("ADDR", m.group(), "[ADDR]") for m in STREET_UNIT.finditer(line.text)]
    found += [Secret("ADDR", line.text, "[ADDR]") for line in _units_left(lines, set(units), anywhere=True)]
    found += [Secret("CODE", f"code at {x0},{y0}", "[CODE]") for x0, y0, _, _ in codes(img, lines)]
    h = _text_height(lines, img.height)
    found += [Secret("ADDR", f"ink left of the address at {int(ln.box[0][1])}", "[ADDR]") for ln in lines
              if re.match(r"\W*\[\s*ADDR", ln.text) and _ink_left(img, _box(ln), h, lines)]  # pixels, not OCR
    anchors = [_box(ln) for ln in lines if re.search(r"\[\s*(?:CODE|ADDR)", ln.text)]
    found += [Secret("MAIL", ln.text, "[MAIL]") for ln in _mail_numbers(lines, anchors, _text_height(lines, img.height))]
    return found


def send_copy(img: Image.Image, max_side: int = 900, secrets: list[Secret] | None = None,
             tries: int = 4) -> Image.Image:
    """What the sheet finder gets: small and soft, enough to see the sheets' edges, not to read them.
    With `secrets`, the copy is checked by OCR and blurred more until none of them can be read."""
    small = img.convert("RGB").copy()
    small.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    radius = max_side / 150
    for _ in range(tries):
        soft = small.filter(ImageFilter.GaussianBlur(radius=radius))
        if not secrets or not leaks(soft, secrets):
            return soft
        radius *= 1.6
    raise RuntimeError("could not make the photo unreadable for the sheet finder; not sending it")

