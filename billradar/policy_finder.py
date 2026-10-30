"""Find the financial assistance policy of a hospital that is not in BillRadar's base, on the hospital's own site.

Tavily searches and extracts, Nemotron 3 Super copies the income tiers with exact quotes, code checks every quote
against the extracted text. A tier whose quote is not in the text, or whose numbers are not in its quote, is not
shown; the family gets the link and "we found the policy, check it yourself".

The hospital's site comes in three steps, never from whatever ranks first (a probe on 2026-10-29: "Memorial
Regional Hospital Hollywood Florida" ranked memorialregionalhealth.com, a different hospital in Colorado, first):
  1. the bill prints a site ("Pay online at ...", a portal): search only that domain;
  2. no site: search the name with the city or ZIP from the bill's address, and take a site only if its page shows
     the name and the city or ZIP, the domain looks like the hospital's (not a directory or a guide);
  3. otherwise, or in doubt: ask the family "Is this your hospital? [name, city, site]"; one tap confirms.

What goes to Tavily: the hospital's name, its city or ZIP, its state and its site's domain. Nothing from the bill
itself (tavily_client.py). BILLRADAR_POLICY_FINDER=1 turns the step on inside the pipeline; it is off by default.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from billradar.nemotron_client import NemotronClient, NemotronError, SpendCapReached
from billradar.policies import source
from billradar.tavily_client import CreditCapReached, TavilyClient, TavilyError

FLAG = "BILLRADAR_POLICY_FINDER"
MODEL = "nvidia/nemotron-3-super-120b-a12b"
NOTE = "Policies change; check the hospital's own site before you apply. This is not a promise of a discount."
MAX_TEXT = 40_000  # characters of the policy the model reads; quotes are checked against the whole text

# Sites that list hospitals but are not one: directories, guides, reviews, government lookups.
NOT_A_HOSPITAL = {
    "careroute.ai", "healthgrades.com", "usnews.com", "yelp.com", "wikipedia.org", "facebook.com", "linkedin.com",
    "webmd.com", "vitals.com", "zocdoc.com", "mapquest.com", "yellowpages.com", "medicare.gov", "cms.gov", "hhs.gov",
    "propublica.org", "glassdoor.com", "indeed.com", "bbb.org", "doximity.com", "leapfroggroup.org", "newsweek.com",
    "beckershospitalreview.com", "hospitalsafetygrade.org", "npiregistry.cms.hhs.gov", "dollarfor.org",
    "myflorida.com", "google.com", "bing.com", "youtube.com", "instagram.com", "x.com", "twitter.com", "reddit.com",
}
GENERIC = {"the", "of", "and", "at", "hospital", "hospitals", "medical", "center", "centre", "health", "healthcare",
           "system", "systems", "regional", "inc", "llc", "general", "clinic", "community", "university", "a"}
POLICY_WORDS = ("financial assistance policy", "charity care policy", "plain language summary",
                "financial assistance program", "charity care", "financial assistance")
NOT_POLICY_WORDS = ("community health needs assessment", "chna", "community benefit", "implementation strategy",
                    "community service plan", "annual report", "form 990", "schedule h",
                    "return of organization exempt")
# Another language: a language folder (/ko/, /es/) or the language's name in the path (full-fap-korean.pdf).
LANGUAGE_FOLDER = re.compile(r"/(ko|es|zh|vi|ru|ht|ar|tl|hy|fa|ja|pl|pt|fr|de|it|ht|so)(?:-[a-z]{2})?/", re.I)
LANGUAGE_NAME = re.compile(r"(?:^|[/_.\-])(korean|spanish|espanol|español|chinese|mandarin|cantonese|vietnamese|russian|"
                           r"creole|haitian|arabic|tagalog|armenian|farsi|persian|japanese|polish|portuguese|french|"
                           r"german|italian|somali|hindi|bengali|urdu|punjabi|gujarati|hmong|khmer|lao|thai|ukrainian|"
                           r"yiddish|greek|albanian)(?=[/_.\-]|$)", re.I)
# Hosts many sites share: a domain there is not one hospital's.
SHARED_HOSTS = {"googleapis.com", "google.com", "googleusercontent.com", "amazonaws.com", "cloudfront.net",
                "windows.net", "azurewebsites.net", "sharepoint.com", "wixsite.com", "squarespace.com", "wordpress.com",
                "github.io", "box.com", "dropbox.com", "hubspot.com", "hs-sites.com", "blogspot.com", "weebly.com",
                "godaddysites.com", "netlify.app", "vercel.app", "pages.dev", "herokuapp.com", "issuu.com", "scribd.com"}
TWO_LEVEL = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "org.au", "co.nz", "com.br", "co.jp", "co.in", "k12.ca.us"}
US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "DC": "district of columbia", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky",
    "LA": "louisiana", "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new hampshire",
    "NJ": "new jersey", "NM": "new mexico", "NY": "new york", "NC": "north carolina", "ND": "north dakota",
    "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania", "PR": "puerto rico", "RI": "rhode island",
    "SC": "south carolina", "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont",
    "VA": "virginia", "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming"}
STATE_NAME_RE = re.compile(r"\s*,?\s*(" + "|".join(sorted(US_STATES.values(), key=len, reverse=True)) + r")\b", re.I)
CITY_PREFIXES = {"west", "east", "north", "south", "new", "port", "fort", "lake", "mount", "palm", "san", "santa",
                 "st", "saint", "el", "la", "los", "las", "upper", "lower", "old", "great", "little", "grand"}
NAME_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .,'&()/\-]{1,119}")
CITY_OK = re.compile(r"[A-Za-z][A-Za-z .'\-]{0,59}")
ZIP_OK = re.compile(r"\d{5}(?:-\d{4})?")
MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
DATE_NEAR = re.compile(
    r"(revised|revision(?: date)?|effective(?: date)?|last (?:reviewed|revised|updated)|updated|approved|"
    r"review date|adopted|version date)\W{0,12}((?:" + MONTHS + r")\s+(?:\d{1,2},\s*)?(?:19|20)\d{2}|"
    r"\d{1,2}/\d{1,2}/(?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})", re.I)
YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["title", "date_quote", "tiers"],
    "properties": {
        "title": {"type": "string"},
        "date_quote": {"type": "string"},
        "tiers": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["from_fpl_pct", "up_to_fpl_pct", "benefit", "discount_pct", "quote"],
            "properties": {
                "from_fpl_pct": {"type": "integer"},
                "up_to_fpl_pct": {"type": "integer"},
                "benefit": {"type": "string", "enum": ["free", "discount", "other"]},
                "discount_pct": {"type": "integer"},
                "quote": {"type": "string"},
            }}},
    },
}
INSTRUCTIONS = (
    "You read one hospital's financial assistance (charity care) policy. List every income tier it prints: the "
    "household income range as a percent of the federal poverty level or guidelines (FPL/FPG), and what the patient "
    "gets: free care, or a discount in percent. For each tier copy ONE exact quote from the text, word for word, "
    "that shows the percent of poverty and the benefit. Never compute, round or guess a number; never combine words "
    "from different places into one quote. from_fpl_pct is 0 when the tier starts at zero income; up_to_fpl_pct is "
    "0 when the tier has no upper limit; discount_pct is 100 for free care and 0 when the policy prints no percent. "
    "List every row of a tier table as its own tier, even when two rows give the same discount; never merge rows. "
    "When the policy prints separate tables for several hospitals, use only the table of the hospital named above. "
    "If the text prints no tiers, return an empty list. title: the document's own title. date_quote: the line that "
    "prints its revision or effective date, copied exactly, or an empty string.")


@dataclass
class Hint:
    """What the bill says about the hospital. Only these fields ever leave the device, and only to Tavily."""

    name: str
    city: str = ""
    zip: str = ""
    state: str = ""
    website: str = ""  # as printed on the bill: "Pay online at www.mhs.net/pay", a portal link

    def clean(self) -> "Hint":
        """The same limits on every path (pipeline, case screen, MCP): a name, a city, a ZIP, a state, a site, and
        nothing else can ride along to Tavily. Raises ValueError with what is wrong."""
        name, city, zip_, state, site = (" ".join(str(x or "").split()) for x in
                                         (self.name, self.city, self.zip, self.state, self.website))
        if not NAME_OK.fullmatch(name):
            raise ValueError("hospital name: letters, digits and . , ' & ( ) / - only, up to 120 characters")
        if city and not CITY_OK.fullmatch(city):
            raise ValueError("city: letters, spaces and . ' - only, up to 60 characters")
        if zip_ and not ZIP_OK.fullmatch(zip_):
            raise ValueError("ZIP: five digits")
        state = state.upper()
        if state and state not in US_STATES:
            raise ValueError("state: two letters, e.g. TX")
        if len(site) > 200:
            raise ValueError("site: up to 200 characters")
        return Hint(name=name, city=city, zip=zip_, state=state, website=site)


@dataclass
class Found:
    status: str  # verified | partial | undated | link_only | ask_user | not_found | in_base | error
    hospital: str
    city: str = ""
    zip: str = ""
    state: str = ""
    domain: str = ""
    domain_step: int = 0  # 1 site on the bill, 2 name + city/ZIP matched, 3 the family confirmed
    domain_note: str = ""
    candidate: dict | None = None  # for ask_user: {name, city, site}
    url: str = ""
    title: str = ""
    doc_date: str = ""
    date_quote: str = ""
    date_note: str = ""
    tiers: list[dict] = field(default_factory=list)  # only tiers whose quote and numbers were found in the text
    missing: list[str] = field(default_factory=list)  # poverty ranges the policy prints that no confirmed tier covers
    state_laws: dict | None = None  # state_laws.for_state for the hospital's state (not Florida), when it has a site
    dropped: list[dict] = field(default_factory=list)  # tiers the check refused, with why
    rejected: list[dict] = field(default_factory=list)  # pages not used, with why (other site, other language, ...)
    tavily_calls: list[dict] = field(default_factory=list)
    nemotron_calls: list[dict] = field(default_factory=list)
    credits: int = 0
    nemotron_usd: float = 0.0
    checked_at: str = ""
    note: str = NOTE
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --- the hospital's site ------------------------------------------------------------------------

HOST = re.compile(r"(?:https?://)?((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})", re.I)


def domain_of(text: str) -> str:
    """'Pay online at www.MHS.net/pay' -> 'mhs.net'; 'https://my.orlandohealth.com/x' -> 'orlandohealth.com';
    'x.nhs.co.uk' -> 'nhs.co.uk'. A shared host (storage.googleapis.com, a wixsite) gives '' : not one hospital's."""
    m = HOST.search(text or "")
    if not m:
        return ""
    labels = m.group(1).lower().strip(".").split(".")
    if len(labels) < 2:
        return ""
    keep = 3 if ".".join(labels[-2:]) in TWO_LEVEL and len(labels) >= 3 else 2
    domain = ".".join(labels[-keep:])
    return "" if domain in SHARED_HOSTS or domain in TWO_LEVEL else domain


def on_domain(url: str, domain: str) -> bool:
    """An http(s) address on that domain or a subdomain of it: never javascript:, data: or a look-alike."""
    u = urlparse(url or "")
    host = (u.hostname or "").lower()
    return bool(domain) and u.scheme in ("http", "https") and (host == domain or host.endswith("." + domain))


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", source.normalize(text or ""))


def distinctive(name: str) -> list[str]:
    """The words of a hospital's name that tell it apart: 'Memorial Regional Hospital' -> ['memorial']."""
    words = [w for w in _words(name) if w not in GENERIC]
    return words or _words(name)


def names_match(name: str, text: str) -> bool:
    have = set(_words(text))
    return all(w in have for w in distinctive(name))


def place_match(hint: Hint, text: str) -> bool:
    """The page shows the hospital's ZIP, or its city on its own: not 'West Hollywood', not 'Hollywood, CA' for a
    Florida hospital."""
    zip5 = hint.zip[:5]
    if zip5 and re.search(rf"(?<!\d){zip5}(?!\d)", text or ""):
        return True
    if not hint.city:
        return False
    city = r"\s+".join(re.escape(w) for w in hint.city.split())
    for m in re.finditer(rf"(?<![A-Za-z]){city}(?![A-Za-z])", text or "", re.I):
        before = re.findall(r"[A-Za-z]+", text[max(0, m.start() - 12):m.start()])
        if before and before[-1].lower() in CITY_PREFIXES:
            continue
        after = text[m.end():m.end() + 30]
        abbr = re.match(r"\s*,?\s*([A-Z]{2})\b", after)
        if abbr and abbr.group(1) in US_STATES and hint.state and abbr.group(1) != hint.state:
            continue
        named = STATE_NAME_RE.match(after)
        if named and hint.state and named.group(1).lower() != US_STATES[hint.state]:
            continue
        return True
    return False


def looks_like_hospital_site(name: str, domain: str) -> bool:
    """The domain spells a distinctive word of the name, or the name's initials: houstonmethodist.org, mountsinai.org."""
    if not domain or domain in NOT_A_HOSPITAL or domain.endswith(".gov"):
        return False
    label = domain.split(".")[0]
    words = _words(name)
    initials = "".join(w[0] for w in words if w not in {"the", "of", "and", "at"})
    return any(w in label for w in distinctive(name) if len(w) >= 4) or (len(initials) >= 3 and label.startswith(initials))


def known_policy(name: str, state: str = "") -> str | None:
    """A policy already in BillRadar's base for this hospital: its distinctive words, and its state when known
    (a "Baptist Hospital" in another state is not Baptist Health South Florida)."""
    st = state.strip().upper()
    for pid, src in source.SOURCES.items():
        if src.kind != "pdf" or not names_match(name, src.hospital):
            continue
        if st and not (re.search(rf"\b{st}\b", src.hospital) or US_STATES.get(st, "~") in src.hospital.lower()):
            continue
        return pid
    return None


def site_from_search(hint: Hint, tavily: TavilyClient, found: Found) -> None:
    """Step 2. Fills found.domain (accepted) or found.candidate (to ask), and found.rejected."""
    place = hint.city or hint.zip
    body = {"query": " ".join(x for x in (hint.name, place, hint.state) if x), "search_depth": "basic",
            "max_results": 5, "country": "united states"}
    res = tavily.search(body, label="policy:site")
    doubtful: dict[str, list[str]] = {}  # site -> pages that show the name and place, but the site is not named so
    for r in res.get("results") or []:
        url, dom = r.get("url", ""), domain_of(r.get("url", ""))
        seen = f"{r.get('title', '')} {r.get('content', '')}"
        if not dom or dom in NOT_A_HOSPITAL or dom.endswith(".gov") or not on_domain(url, dom):
            found.rejected.append({"url": url, "why": "a directory, a guide or a shared host, not the hospital's site"})
            continue
        if not names_match(hint.name, seen):
            found.rejected.append({"url": url, "why": "the page does not show the hospital's name"})
            continue
        if not place_match(hint, seen):
            found.rejected.append({"url": url, "why": f"the page does not show {place or 'the city or ZIP'}"})
            continue
        if looks_like_hospital_site(hint.name, dom):
            found.domain, found.domain_step = dom, 2
            found.domain_note = f"{dom}: the page shows the name and {place}"
            return
        doubtful.setdefault(dom, []).append(url)
        found.rejected.append({"url": url, "why": "name and place match, but the site's name does not; ask"})
    if doubtful:  # a site named like the hospital first, then the one most matching pages come from (mhs.net: two)
        dom, urls = max(doubtful.items(), key=lambda kv: (looks_like_hospital_site(hint.name, kv[0]), len(kv[1])))
        found.candidate = {"name": hint.name, "city": hint.city or hint.zip, "site": dom, "url": urls[0]}


# --- the policy on that site ---------------------------------------------------------------------

def _key(text: str) -> str:
    """Quote-matching key: source.compact, and the markdown marks a page's text may carry dropped. Table pipes stay:
    a quote may not run from one table cell into the next."""
    return re.sub(r"[*_#\\`>\[\]]", "", source.compact(text or ""))


def _url_words(url: str) -> str:
    return re.sub(r"[-_/.]+", " ", unquote(url))


def kind_of(title: str, url: str, text: str) -> str:
    """'policy', 'other' (CHNA, community benefit, annual report ...) or 'unknown'."""
    head = source.normalize(f"{title} {_url_words(url)}")
    if any(w in head for w in POLICY_WORDS):  # /community-benefit/financial-assistance-policy.pdf is the policy
        return "policy"
    if any(w in head for w in NOT_POLICY_WORDS):
        return "other"
    body = source.normalize(f"{title} {_url_words(url)} {text[:4000]}")
    return "policy" if any(w in body for w in POLICY_WORDS) else "unknown"


def english(url: str, text: str) -> bool:
    path = unquote(urlparse(url).path or "")
    if LANGUAGE_FOLDER.search(path) or LANGUAGE_NAME.search(path):
        return False
    letters = [c for c in text[:20000] if c.isalpha()]
    if not letters:
        return True
    return sum(c.isascii() for c in letters) / len(letters) >= 0.9


def date_of(text: str, url: str) -> tuple[str, int]:
    """(the printed revision/effective date, its year); else ('', year from the URL or 0)."""
    best, year = "", 0
    for m in DATE_NEAR.finditer(text[:60000]):
        y = int(YEAR.search(m.group(2)).group(0))
        if y > year:
            best, year = m.group(0).strip(), y
    if not best:
        years = [int(y) for y in YEAR.findall(urlparse(url).path or "")]
        year = max(years) if years else 0
    return best, year


def excerpt(text: str, limit: int = MAX_TEXT) -> str:
    """Whole text when short; else the passages around 'poverty', 'FPL', 'FPG' (merged), up to the limit."""
    if len(text) <= limit:
        return text
    spans: list[list[int]] = []
    for m in re.finditer(r"poverty|\bFPL\b|\bFPG\b|discount|charity", text, re.I):
        a, b = max(0, m.start() - 1500), min(len(text), m.end() + 1500)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    out, total = [], 0
    for a, b in spans:
        if total + (b - a) > limit:
            b = a + (limit - total)
        out.append(text[a:b])
        total += b - a
        if total >= limit:
            break
    return "\n...\n".join(out) if out else text[:limit]


def trim_quote(quote: str) -> str:
    """'"…301% - 350% of FPG 83% Discount ..."' -> '301% - 350% of FPG 83% Discount': the ends, never the middle."""
    return re.sub(r"^[\s\"'“”‘’.…]+|[\s\"'“”‘’.…]+$", "", quote or "")


PCT = r"(\d{2,3})\s?(?:%|percent\b)"
RANGE = re.compile(r"(\d{2,3})\s?(?:%|percent\b)?\s*(?:-|–|to|and|through|but not more than|but less than|"
                   r"but not exceeding)\s*" + PCT, re.I)
POVERTY = re.compile(r"poverty|\bfpl\b|\bfpg\b", re.I)
DISCOUNT_AFTER = re.compile(r"^\W{0,3}(?:discount|off\b|reduction)", re.I)
DISCOUNT_BEFORE = re.compile(r"(?:discount|reduction|reduced)\s+(?:of|by)\s*$", re.I)
FREE = re.compile(r"\bfree\b|no charge|no cost|at no cost|100\s?%\s*(?:discount|charity|financial|write|of)|"
                  r"full (?:charity|discount|write)|in (?:its|their) entirety|fully (?:covered|written)|written off in full",
                  re.I)


def quote_numbers(quote: str) -> tuple[set[int], set[int], list[tuple[int, int]]]:
    """(percents of the poverty level, discount percents, poverty ranges) as the quote itself prints them.
    '201% - 250% of FPG 90% Discount' -> ({201, 250}, {90}, [(201, 250)])."""
    q = " ".join((quote or "").split())
    spans = [(m.start(), m.end(), int(m.group(1))) for m in re.finditer(PCT, q, re.I)]
    fpl, disc, ranges = set(), set(), []
    for m in RANGE.finditer(q):
        nxt = next((a for a, _, _ in spans if a >= m.end()), len(q))
        if POVERTY.search(q[m.end():min(nxt, m.end() + 70)]):
            lo, hi = int(m.group(1)), int(m.group(2))
            fpl |= {lo, hi}
            ranges.append((lo, hi))
    for i, (a, b, v) in enumerate(spans):
        nxt = spans[i + 1][0] if i + 1 < len(spans) else len(q)
        gap = q[b:min(nxt, b + 70)]
        if POVERTY.search(gap) and not DISCOUNT_AFTER.search(gap):
            fpl.add(v)
        elif DISCOUNT_AFTER.search(q[b:b + 25]) or DISCOUNT_BEFORE.search(q[max(0, a - 25):a]):
            disc.add(v)
    return fpl, disc, ranges


def check_tier(tier: dict, text_key: str, below: int | None = None) -> str:
    """'' when the tier stands; otherwise why it is dropped. The quote must be in the text, and the quote itself must
    print the tier's numbers in their roles: its percents of the poverty level, its discount, "free" for free care.
    `below`: the verified ceiling of the tier just below; a quote that prints only "up to 600%" may start there."""
    quote = trim_quote(tier.get("quote", ""))
    if len(_key(quote)) < 12:
        return "quote too short to check"
    if "||" in _key(quote):  # the end of one table row and the start of the next
        return "the quote runs across table rows"
    if _key(quote) not in text_key:
        return "quote not found in the policy text"
    fpl, disc, ranges = quote_numbers(quote)
    lo, hi = int(tier.get("from_fpl_pct") or 0), int(tier.get("up_to_fpl_pct") or 0)
    if not (lo or hi):
        return "no percent of the poverty level"
    if hi and hi not in fpl:
        return f"up to {hi}% of the poverty level is not what the quote prints"
    if lo:
        if lo not in fpl and not (below is not None and len(fpl) == 1 and lo in (below, below + 1)):
            return f"from {lo}% of the poverty level is not what the quote prints"
    elif any(r_lo > 0 for r_lo, r_hi in ranges if not hi or r_hi == hi):
        return "the quote prints a range that does not start at zero"
    benefit, pct = tier.get("benefit"), int(tier.get("discount_pct") or 0)
    if benefit == "other":
        return "not a free-care or discount tier"
    if benefit == "free" or pct == 100:
        if disc - {100}:
            return f"the quote prints a {min(disc - {100})}% discount, not free care"
        words = " ".join(quote.split())
        if not (FREE.search(words) or 100 in disc or
                (re.search(r"\bcharity care\b", words, re.I) and not re.search(r"discount", words, re.I)
                 and not (set(int(v) for v in re.findall(r"(\d{2,3})\s?(?:%|percent)", words, re.I)) - fpl))):
            return "the quote does not say the care is free"
        return ""
    if pct and pct not in disc:
        return f"discount {pct}% is not what the quote prints"
    if not pct and not re.search(r"discount|reduc", quote, re.I):
        return "the quote does not mention a discount"
    return ""


def shows(tier: dict) -> str:
    """What a tier gives, in words, for screens and agents: never '0%' for a discount the policy does not size."""
    pct = int(tier.get("discount_pct") or 0)
    if tier.get("benefit") == "free" or pct == 100:
        return "free care"
    return f"{pct}% discount" if pct else "a discount, amount not stated"


def printed_ranges(text: str) -> list[tuple[int, int]]:
    """Every 'NNN% - NNN% of FPG' the policy text prints (one per distinct range)."""
    flat, out = " ".join((text or "").split()), []
    for m in RANGE.finditer(flat):
        rng = (int(m.group(1)), int(m.group(2)))
        if POVERTY.search(flat[m.end():m.end() + 70]) and rng not in out:
            out.append(rng)
    return out


def uncovered(ranges: list[tuple[int, int]], tiers: list[dict]) -> list[str]:
    """Printed ranges the confirmed tiers do not cover without a gap: '351%–400%' when that row was not confirmed."""
    spans = sorted((int(t.get("from_fpl_pct") or 0), int(t.get("up_to_fpl_pct") or 0) or 10_000) for t in tiers)
    out = []
    for lo, hi in ranges:
        reach = lo
        for a, b in spans:  # each next tier starts at most 1% above the last ceiling
            if a <= reach + 1 and b > reach:
                reach = b
        if lo < hi and reach < hi:
            out.append(f"{lo}%–{hi}%")
    return out


def read_tiers(text: str, hospital: str, nemotron, found: Found) -> None:
    body = {
        "model": MODEL, "instructions": INSTRUCTIONS, "store": False, "max_output_tokens": 4096,
        "reasoning": {"effort": "low"},
        "input": [{"role": "user", "content": [{"type": "input_text", "text": (
            f"Hospital: {hospital}\nPolicy text (from the hospital's own site):\n\n{excerpt(text)}")}]}],
        "text": {"format": {"type": "json_schema", "name": "fap_tiers", "schema": SCHEMA, "strict": True}},
    }
    first = len(getattr(nemotron, "calls", []))
    result = nemotron.respond(body, label="policy:tiers")
    found.nemotron_calls = [{k: c.get(k) for k in ("label", "model", "answered_by", "response_id", "request_id",
                                                    "usage", "usd", "live")}
                            for c in getattr(nemotron, "calls", [])[first:]]
    found.nemotron_usd = round(sum(float(c.get("usd") or 0) for c in found.nemotron_calls), 6)
    answer = json.loads(result.text)
    text_key = _key(text)
    if answer.get("title") and _key(answer["title"]) in text_key:
        found.title = answer["title"].strip()
    dq = (answer.get("date_quote") or "").strip()
    if dq and _key(dq) in text_key and YEAR.search(dq):
        found.date_quote = dq
    tiers = [{**t, "quote": trim_quote(t.get("quote", ""))} for t in answer.get("tiers") or []]
    tiers.sort(key=lambda t: (int(t.get("up_to_fpl_pct") or 0) or 10_000, int(t.get("from_fpl_pct") or 0)))
    below = None  # the verified ceiling of the tier just below, in order from the lowest income up
    for tier in tiers:
        why = check_tier(tier, text_key, below)
        if why:
            found.dropped.append({**tier, "why": why})
        else:
            found.tiers.append({**tier, "shows": shows(tier)})
            below = int(tier.get("up_to_fpl_pct") or 0) or below
    found.missing = uncovered(printed_ranges(excerpt(text)), found.tiers) if found.tiers else []


def find(hint: Hint, tavily: TavilyClient | None = None, nemotron=None, confirmed: bool = False) -> Found:
    """The whole step for one hospital. `confirmed`: the family tapped "yes, this is my hospital" on hint.website."""
    tavily = tavily or TavilyClient()
    found = Found(status="not_found", hospital=hint.name, city=hint.city, zip=hint.zip, state=hint.state,
                  checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        hint = hint.clean()
        found.hospital, found.city, found.zip, found.state = hint.name, hint.city, hint.zip, hint.state
        _find(hint, tavily, nemotron, confirmed, found)
        if found.domain and hint.state:  # the state's own law site; Florida is skipped there
            from billradar import state_laws
            found.state_laws = state_laws.for_state(hint.state, tavily, nemotron or NemotronClient()).to_dict()
            found.nemotron_calls += found.state_laws["nemotron_calls"]  # one receipt for the card
            found.nemotron_usd = round(sum(float(c.get("usd") or 0) for c in found.nemotron_calls), 6)
    except (CreditCapReached, SpendCapReached) as exc:  # stopped before the call: say so, never a half result
        found.status, found.message, found.tiers = "error", f"stopped: {exc}", []
    except (TavilyError, NemotronError, ValueError, KeyError) as exc:  # ValueError: a bad hint or an answer not the JSON
        found.status, found.message = "error", f"{type(exc).__name__}: {exc}"
    finally:
        found.tavily_calls = list(tavily.calls)
        found.credits = sum(int(c.get("credits") or 0) for c in tavily.calls)
    return found


STALE_YEARS = 3


def _policy_pages(hint: Hint, tavily: TavilyClient, found: Found, query: str, label: str = "policy:search") -> list[dict]:
    body = {"query": query, "search_depth": "advanced", "include_domains": [found.domain],
            "include_raw_content": True, "max_results": 5}
    res = tavily.search(body, label=label)
    pages, seen = [], {r["url"] for r in found.rejected}
    for r in res.get("results") or []:
        url, text, title = r.get("url", ""), r.get("raw_content") or "", r.get("title", "")
        if url in seen:
            continue
        if not on_domain(url, found.domain):  # include_domains is Tavily's filter; this is ours
            found.rejected.append({"url": url, "why": f"not on {found.domain}"})
            continue
        if text.strip() and not names_match(hint.name, f"{title} {_url_words(url)} {text[:30000]}"):
            found.rejected.append({"url": url, "why": "the page does not name this hospital"})
            continue
        kind = kind_of(title, url, text)
        if kind == "other":
            found.rejected.append({"url": url, "why": "another document (needs assessment, community benefit, report)"})
            continue
        if not english(url, text):
            found.rejected.append({"url": url, "why": "not the English version"})
            continue
        date_line, year = date_of(text, url)
        pages.append({"url": url, "title": title, "text": text, "kind": kind, "date": date_line, "year": year,
                      "score": float(r.get("score") or 0)})
    return pages


def _find(hint: Hint, tavily: TavilyClient, nemotron, confirmed: bool, found: Found) -> None:
    if hint.website:
        found.domain = domain_of(hint.website)
        found.domain_step = 3 if confirmed else 1
        found.domain_note = (f"{found.domain}: you confirmed it is your hospital" if confirmed
                             else f"{found.domain}: the site printed on your bill")
        if not found.domain:
            found.status, found.message = "ask_user", ("the site on the bill could not be read, or it is a shared host "
                                                       "rather than the hospital's own site; type the hospital's site")
            return
    else:
        site_from_search(hint, tavily, found)
        if not found.domain:
            found.status = "ask_user"
            found.message = ("Is this your hospital?" if found.candidate else
                             "We could not tell the hospital's own site; type the site printed on your bill.")
            return

    pages = _policy_pages(hint, tavily, found, f"{hint.name} financial assistance policy federal poverty level")
    if not pages and any("English" in r["why"] for r in found.rejected):  # e.g. Cedars-Sinai: Korean and Russian first
        pages = _policy_pages(hint, tavily, found, f"{hint.name} financial assistance policy English full policy")
    if not pages:
        found.status, found.message = "not_found", f"no financial assistance policy found on {found.domain}"
        return
    # The policy itself before other pages, pages with text before empty ones, then the newest revision; a dated
    # version more than STALE_YEARS old ranks below an undated page (Mount Sinai's first PDF was from 2018).
    now = datetime.now(timezone.utc).year
    fresh = lambda p: not p["year"] or p["year"] >= now - STALE_YEARS  # noqa: E731
    pages.sort(key=lambda p: (p["kind"] == "policy", bool(p["text"].strip()), fresh(p), p["year"], p["score"]),
               reverse=True)
    best = pages[0]
    found.url, found.title, found.doc_date = best["url"], best["title"], best["date"]
    for p in pages[1:]:
        if p["year"] and (p["year"] < best["year"] or not fresh(p)):
            found.rejected.append({"url": p["url"], "why": f"an older version ({p['year']})"})
    if not best["date"]:  # an undated page may be an old copy (Orlando Health: 225% on the page, 300% in 2026's PDF)
        dated = [p for p in _policy_pages(hint, tavily, found, f"{hint.name} financial assistance policy {now} pdf",
                                          label="policy:dated")
                 if p["kind"] == "policy" and p["date"] and fresh(p) and p["text"].strip()]
        if dated:
            dated.sort(key=lambda p: (p["year"], p["score"]), reverse=True)
            found.rejected.append({"url": best["url"], "why": f"undated; a dated policy ({dated[0]['date']}) is used"})
            best = dated[0]
            found.url, found.title, found.doc_date = best["url"], best["title"], best["date"]
    if not best["text"].strip():
        ex = tavily.extract({"urls": [best["url"]], "extract_depth": "basic"}, label="policy:extract")
        best["text"] = next((x.get("raw_content") or "" for x in ex.get("results") or []
                             if on_domain(x.get("url", ""), found.domain)), "")
        found.doc_date = found.doc_date or date_of(best["text"], best["url"])[0]
    if not best["text"].strip():
        found.status, found.message = "link_only", "we found the page, but could not read its text; check it yourself"
        return
    if not names_match(hint.name, best["text"][:30000]):
        found.status, found.message = "link_only", "the page we found does not name this hospital; check it yourself"
        return
    if nemotron is None:
        nemotron = NemotronClient()
    read_tiers(best["text"], hint.name, nemotron, found)
    found.doc_date = found.doc_date or found.date_quote
    if not found.doc_date:
        found.date_note = "the page prints no revision date; check that it is the current policy"
    elif best["year"] and not fresh(best):
        found.date_note = f"the newest version we found is from {best['year']}; check for a newer one"
    if not found.tiers:
        found.status, found.message = "link_only", "we found the policy, but no tier passed the quote check; check it yourself"
    elif found.missing:
        found.status = "partial"
        found.message = ("the policy prints more tiers than we could confirm word for word ("
                         + ", ".join(found.missing[:4]) + " of the poverty level); check the policy for those")
    elif not found.doc_date:
        found.status = "undated"
        found.message = "the tiers are quoted from the page, but it prints no date and may be out of date"
    else:
        found.status = "verified"


# --- inside the pipeline -------------------------------------------------------------------------

def enabled() -> bool:
    return os.environ.get(FLAG, "").strip() == "1"


def hint_for(case) -> Hint | None:
    """The hospital of the case's bills, by name only: its city, ZIP, state and site the family adds on the case
    screen (the reader does not copy them from the papers yet). Nothing about the family goes in."""
    name = next((d.provider.strip() for d in case.bills() if d.provider.strip()), "")
    return Hint(name=name) if name else None


def for_case(case, client=None) -> dict | None:
    """One call in pipeline.run. None when the flag is off, the bills name no hospital, or it is in the base.
    Never raises: a failure is a visible status, the case itself is never lost to it."""
    if not enabled():
        return None
    try:
        hint = hint_for(case)
        if hint is None:
            return None
        in_base = known_policy(hint.name)
        if in_base:
            return Found(status="in_base", hospital=hint.name, message=f"in BillRadar's base: {in_base}").to_dict()
        if not (hint.website or hint.city or hint.zip):  # nothing to search by yet: ask, spend nothing
            return Found(status="ask_user", hospital=hint.name, message=(
                "Type the hospital's site, or its city or ZIP, as printed on your bill.")).to_dict()
        nemotron = client if getattr(client, "provider", "") == "nebius" else None
        return find(hint, nemotron=nemotron).to_dict()
    except Exception as exc:  # noqa: BLE001 — shown on the case screen as an error, the case is saved as it is
        return Found(status="error", hospital=getattr(case, "case_id", ""), message=f"{type(exc).__name__}: {exc}"
                     ).to_dict()


# --- the recorded live run (the /policy-finder page, the MCP tool's replay) -----------------------

RUN_FILE = Path(__file__).parent / "policy_finder_run.json"


def run_plan() -> dict:
    return json.loads(RUN_FILE.read_text())


def replay_run() -> dict:
    """Every hospital of the recorded run, answered from the recordings only (no network)."""
    from billradar import nemotron_client as nc
    from billradar import tavily_client as tc
    plan = run_plan()
    results = []
    for item in plan["runs"]:
        tavily = TavilyClient(transport=tc.ReplayTransport(), record_to=None, journal=None)
        nemotron = NemotronClient(transport=nc.ReplayTransport(), record_to=None, journal=None)
        found = find(Hint(**item["hint"]), tavily, nemotron, confirmed=item.get("confirmed", False)).to_dict()
        found["checked_at"] = plan.get("recorded", "")  # when the live run happened, not when this replay ran
        results.append(found)
    hospitals = {(r["hospital"], r["state"]) for r in results}
    return {"recorded": plan.get("recorded", ""), "results": results, "hospitals": len(hospitals),
            "states": len({s for _, s in hospitals}), "credits": sum(r["credits"] for r in results),
            "usd": round(sum(r["nemotron_usd"] for r in results), 6)}
