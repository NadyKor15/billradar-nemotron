"""Hospital financial assistance policies as data, every rule tied to a quote.

A rule without a verified quote is not used. The quote check runs against the
policy's own text layer (see policies/source.py), so a typo in our JSON or a new
revision of the hospital's PDF shows up as an unverified fragment, not as a
silent wrong finding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from pathlib import Path

from billradar.policies import source

POLICY_DIR = Path(__file__).parent / "policies"


@dataclass(frozen=True)
class Fragment:
    policy_id: str
    fragment_id: str
    page: int
    quote: str

    @property
    def ref(self) -> str:
        return f"{self.policy_id}#{self.fragment_id}"


@dataclass(frozen=True)
class Tier:
    max_fpl_pct: int
    discount_pct: int
    fragment: str


@dataclass
class Policy:
    policy_id: str
    hospital: str
    title: str
    url: str
    fragments: dict[str, Fragment]
    covers_insured_balances: bool = True
    min_request_total: Decimal | None = None
    liquid_assets_limit_for_free_care: Decimal | None = None
    income_cap_pct: int | None = None
    fpg_year: int | None = None  # the poverty-guideline year the policy's tiers are written against
    tiers: list[Tier] = field(default_factory=list)
    window: dict = field(default_factory=dict)

    def tier_for(self, fpl_pct: Decimal) -> Tier | None:
        """Tier for a household at `fpl_pct` percent of the poverty guideline.

        Policies print tiers as whole percents ("201% - 250%"), so 250.4% still
        belongs to the 250 tier: we compare against the ceiling rounded down.
        """
        whole = int(fpl_pct)
        for tier in self.tiers:
            if whole <= tier.max_fpl_pct:
                return tier
        return None

    def fragment(self, fragment_id: str) -> Fragment:
        return self.fragments[fragment_id]


@cache
def load(policy_id: str) -> Policy:
    raw = json.loads((POLICY_DIR / f"{policy_id}.json").read_text())
    frags = {fid: Fragment(policy_id, fid, f["page"], f["quote"]) for fid, f in raw["fragments"].items()}
    money = lambda v: Decimal(v) if v is not None else None  # noqa: E731
    return Policy(
        policy_id=raw["policy_id"],
        hospital=raw["hospital"],
        title=raw["title"],
        url=raw["url"],
        fragments=frags,
        covers_insured_balances=raw.get("covers_insured_balances", True),
        min_request_total=money(raw.get("min_request_total")),
        liquid_assets_limit_for_free_care=money(raw.get("liquid_assets_limit_for_free_care")),
        income_cap_pct=raw.get("income_cap_pct"),
        fpg_year=raw.get("fpg_year"),
        tiers=[Tier(**t) for t in raw.get("tiers", [])],
        window=raw.get("window", {}),
    )


def available() -> list[str]:
    return sorted(p.stem for p in POLICY_DIR.glob("*.json"))


@dataclass(frozen=True)
class QuoteCheck:
    ref: str
    found: bool
    page: int
    found_on_page: int | None
    detail: str


def verify_fragment(frag: Fragment) -> QuoteCheck:
    """Is the quote really on the page we say? Needs the cached policy text."""
    if not source.is_cached(frag.policy_id):
        return QuoteCheck(frag.ref, False, frag.page, None, "policy text not downloaded")
    pages = [source.compact(t) for t in source.page_texts(frag.policy_id)]
    needle = source.compact(frag.quote)
    if 0 < frag.page <= len(pages) and needle in pages[frag.page - 1]:
        return QuoteCheck(frag.ref, True, frag.page, frag.page, "ok")
    elsewhere = next((i + 1 for i, p in enumerate(pages) if needle in p), None)
    if elsewhere:
        return QuoteCheck(frag.ref, False, frag.page, elsewhere, f"quote is on page {elsewhere}, not {frag.page}")
    return QuoteCheck(frag.ref, False, frag.page, None, "quote not found in policy text")


@cache
def verified(ref: str) -> bool:
    policy_id, fragment_id = ref.split("#", 1)
    return verify_fragment(load(policy_id).fragment(fragment_id)).found
