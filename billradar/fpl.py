"""HHS poverty guidelines, 48 contiguous states + DC.

2026: 91 FR 1797 (published 2026-01-15, effective 2026-01-13).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

GUIDELINES = {
    2026: {"base": [15960, 21640, 27320, 33000, 38680, 44360, 50040, 55720], "each_extra": 5680},
}


SOURCE = {2026: "HHS poverty guidelines 2026, 48 contiguous states and DC, 91 FR 1797 (January 15, 2026)"}


def latest_year() -> int:
    return max(GUIDELINES)


def guideline(household_size: int, year: int = 2026) -> Decimal:
    if household_size < 1:
        raise ValueError("household size must be at least 1")
    g = GUIDELINES[year]
    if household_size <= len(g["base"]):
        return Decimal(g["base"][household_size - 1])
    return Decimal(g["base"][-1] + g["each_extra"] * (household_size - len(g["base"])))


def percent_of_guideline(annual_income: Decimal, household_size: int, year: int = 2026) -> Decimal:
    """Income as a percent of the guideline, one decimal place (193.9, not 193.93...)."""
    pct = Decimal(annual_income) * 100 / guideline(household_size, year)
    return pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
