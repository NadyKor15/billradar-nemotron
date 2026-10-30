"""The hospital's own published prices (CMS Hospital Price Transparency, 45 CFR Part 180).

Every hospital publishes a machine-readable file of standard charges, listed in `cms-hpt.txt` at the root
of its website. BillRadar uses two numbers from it per service: the gross charge (what the bill starts
from) and the discounted cash price (what the hospital says it accepts from people paying themselves).

The files are large (one of them is 1.49 GB of CSV), so they are never in this repository.
`scripts/fetch_prices.py` downloads them into `.cache/mrf/`, and `build_index` walks a file once
(streamed, about 20 MB of memory) and keeps only codes with both numbers, plus the row where they were
found. Every finding names the file, its row and its publication date: prices are as the hospital
published them on that date.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path

from billradar.policies import source

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "mrf"
CODE_TYPES = ("HCPCS", "CPT", "CDM")  # one code -> one item; a revenue code (RC) covers dozens
RATE_TYPES = CODE_TYPES + ("MS-DRG",)  # plan rates: also the case rate of a whole stay (a birth: DRG 807)
FIRM = ("case rate", "fee schedule", "per diem")  # a dollar the plan pays; "percent of billed" is the hospital's own estimate
# Plans whose published rates are kept in the index (the demo compares with a real plan).
PLANS = [("Aetna", "Qualified Health Plan")]


@dataclass(frozen=True)
class PriceSource:
    policy_id: str
    hospital: str  # from SOURCES (the policy's hospital); the file's own hospital_name wins once indexed
    hpt_txt: str  # where the file is listed
    url: str  # the machine-readable file itself


SOURCES = {pid: PriceSource(pid, source.SOURCES[pid].hospital, hpt, url) for pid, (hpt, url) in source.PRICE_FILES.items()}


@dataclass(frozen=True)
class Price:
    code_type: str
    code: str
    description: str
    setting: str
    gross: Decimal
    cash: Decimal
    row: int  # data row in the file, 1 = first row after the header


@dataclass(frozen=True)
class Rate:
    code_type: str
    code: str
    description: str
    setting: str
    dollar: Decimal
    methodology: str
    row: int


def plan_key(payer: str, plan: str) -> str:
    return f"{payer.strip().lower()}|{plan.strip().lower()}"


@dataclass(frozen=True)
class Index:
    policy_id: str
    hospital: str
    url: str
    last_updated_on: str
    version: str
    prices: dict[str, list[Price]]  # "HCPCS:85610" -> distinct (setting, gross, cash) rows
    rates: dict[str, dict[str, list[Rate]]]  # plan_key -> "MS-DRG:807" -> distinct published rates

    def candidates(self, code: str) -> list[Price]:
        c = code.strip().upper()
        return [p for t in CODE_TYPES for p in self.prices.get(f"{t}:{c}", [])]

    def plan_rates(self, payer: str, plan: str, code_type: str, code: str) -> list[Rate]:
        return self.rates.get(plan_key(payer, plan), {}).get(f"{code_type}:{code.strip().upper()}", [])


def raw_path(policy_id: str) -> Path:
    name = SOURCES[policy_id].url.split("?")[0].rsplit("/", 1)[-1]
    return CACHE / f"{policy_id}{'.zip' if name.endswith('.zip') else '.csv'}"


def index_path(policy_id: str) -> Path:
    return CACHE / f"{policy_id}.index.json"


def _open(path: Path):
    if path.suffix == ".zip":
        z = zipfile.ZipFile(path)
        member = next(i for i in z.infolist() if i.filename.lower().endswith(".csv"))
        return io.TextIOWrapper(z.open(member), encoding="utf-8-sig", errors="replace", newline="")
    return path.open(encoding="utf-8-sig", errors="replace", newline="")


def _money(text: str) -> Decimal | None:
    try:
        return Decimal(text.strip()).quantize(Decimal("0.01")) if text.strip() else None
    except InvalidOperation:
        return None


def build_index(path: Path, policy_id: str, hospital: str = "", url: str = "",
                plans: list[tuple[str, str]] | None = None) -> dict:
    """One streamed pass over a CMS 3.0 'tall' CSV. Returns the index as plain data (JSON-ready).
    Keeps gross and cash for every code, and the negotiated dollar of the chosen `plans` only."""
    csv.field_size_limit(10**9)
    reader = csv.reader(_open(path))
    info = dict(zip(next(reader), next(reader)))
    header = [h.strip().lower() for h in next(reader)]
    col = {name: i for i, name in enumerate(header)}
    need = ("description", "setting", "standard_charge|gross", "standard_charge|discounted_cash")
    missing = [n for n in need if n not in col]
    if missing:
        raise ValueError(f"not a CMS 'tall' standard charges file: no column {missing}")
    code_cols = [(col[f"code|{k}"], col[f"code|{k}|type"]) for k in range(1, 10)
                 if f"code|{k}" in col and f"code|{k}|type" in col]
    seen: dict[str, dict[tuple, list]] = {}
    wanted = {plan_key(p, q) for p, q in (plans if plans is not None else PLANS)}
    rates: dict[str, dict[str, dict[tuple, list]]] = {k: {} for k in wanted}
    pc, qc, nd, mc = (col.get(k) for k in ("payer_name", "plan_name", "standard_charge|negotiated_dollar",
                                           "standard_charge|methodology"))
    for n, row in enumerate(reader, start=1):
        if len(row) < len(header):
            continue
        if None not in (pc, qc, nd) and (pk := plan_key(row[pc], row[qc])) in wanted:
            dollar = _money(row[nd])
            if dollar is not None:
                for ci, ti in code_cols:
                    code, ctype = row[ci].strip().upper(), row[ti].strip().upper()
                    if code and ctype in RATE_TYPES:
                        item = (row[col["setting"]].strip(), str(dollar), row[mc].strip().lower() if mc is not None else "")
                        rates[pk].setdefault(f"{ctype}:{code}", {}).setdefault(
                            item, [row[col["description"]].strip()[:80], n])
        gross, cash = _money(row[col["standard_charge|gross"]]), _money(row[col["standard_charge|discounted_cash"]])
        if gross is None or cash is None:
            continue
        for ci, ti in code_cols:
            code, ctype = row[ci].strip().upper(), row[ti].strip().upper()
            if code and ctype in CODE_TYPES:
                key = f"{ctype}:{code}"
                item = (row[col["setting"]].strip(), str(gross), str(cash))
                seen.setdefault(key, {}).setdefault(item, [row[col["description"]].strip()[:80], n])
    return {"policy_id": policy_id, "hospital": hospital or info.get("hospital_name", ""), "url": url,
            "last_updated_on": info.get("last_updated_on", ""), "version": info.get("version", ""),
            "prices": {k: [[d, s, g, c, r] for (s, g, c), (d, r) in v.items()] for k, v in seen.items()},
            "rates": {pk: {k: [[d, s, dl, m, r] for (s, dl, m), (d, r) in v.items()] for k, v in codes.items()}
                      for pk, codes in rates.items()}}


def write_index(policy_id: str) -> Path:
    src = SOURCES[policy_id]
    data = build_index(raw_path(policy_id), policy_id, "", src.url)  # the file names its hospital
    out = index_path(policy_id)
    out.write_text(json.dumps(data, separators=(",", ":")))
    return out


@cache
def load(policy_id: str) -> Index | None:
    """The index for a hospital, or None when it was never built on this machine (prices then stay unchecked)."""
    p = index_path(policy_id)
    if not p.exists():
        return None
    raw = json.loads(p.read_text())
    prices = {}
    for key, rows in raw["prices"].items():
        ctype, code = key.split(":", 1)
        prices[key] = [Price(ctype, code, d, s, Decimal(g), Decimal(c), r) for d, s, g, c, r in rows]
    rates = {pk: {key: [Rate(*key.split(":", 1), d, s, Decimal(dl), m, r) for d, s, dl, m, r in rows]
                  for key, rows in codes.items()} for pk, codes in raw.get("rates", {}).items()}
    return Index(raw["policy_id"], raw["hospital"], raw["url"], raw["last_updated_on"], raw["version"], prices, rates)


def match(index: Index, code: str, charge: Decimal) -> tuple[Price, int] | None | str:
    """The one published price a printed charge comes from: charge = units x gross, to the cent.
    Returns (price, units); "ambiguous" when the code is listed but the charge fits no single price; None
    when the code is not in the file."""
    cands = index.candidates(code)
    if not cands:
        return None
    fits = {}
    for p in cands:
        if p.gross <= 0:
            continue
        units = (charge / p.gross).to_integral_value()
        if 1 <= units <= 99 and abs(charge - units * p.gross) <= Decimal("0.01"):
            fits.setdefault((p.gross, p.cash), (p, int(units)))
    return next(iter(fits.values())) if len(fits) == 1 else "ambiguous"
