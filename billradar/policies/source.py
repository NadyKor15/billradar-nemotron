"""Public financial assistance policy PDFs: where they live and how we read their text.

We never ship the hospitals' PDFs in this repository. `scripts/fetch_policies.py`
downloads them from the hospitals' own websites into `.cache/policies/`, and every
quote in our policy files is checked against that text layer.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache" / "policies"


@dataclass(frozen=True)
class PolicySource:
    policy_id: str
    hospital: str
    url: str
    kind: str = "pdf"  # "pdf", "ecfr" (eCFR versioner XML) or "html" (a statute page); xml/html are one "page"


SOURCES = {
    "memorial-2026-05": PolicySource(
        "memorial-2026-05",
        "Memorial Healthcare System (Memorial Regional Hospital, Hollywood FL)",
        "https://www.mhs.net/-/media/mhs/files/patients-and-visitors/financial-assistance/en/full-policy.pdf",
    ),
    "baptist-2026-09": PolicySource(
        "baptist-2026-09",
        "Baptist Health South Florida (Baptist Hospital of Miami)",
        "https://baptisthealth.net/-/media/FAB30007578640E2B1F902CBF6256B95.ashx",
    ),
    "federal-501r": PolicySource(
        "federal-501r",
        "26 CFR 1.501(r)-1 (federal rules for nonprofit hospitals)",
        "https://www.ecfr.gov/api/versioner/v1/full/2026-10-01/title-26.xml?part=1&section=1.501(r)-1",
        kind="ecfr",
    ),
    "federal-501r-4": PolicySource(
        "federal-501r-4",
        "26 CFR 1.501(r)-4 (financial assistance policy)",
        "https://www.ecfr.gov/api/versioner/v1/full/2026-10-01/title-26.xml?part=1&section=1.501(r)-4",
        kind="ecfr",
    ),
    "federal-501r-5": PolicySource(
        "federal-501r-5",
        "26 CFR 1.501(r)-5 (limit on amounts charged, AGB)",
        "https://www.ecfr.gov/api/versioner/v1/full/2026-10-01/title-26.xml?part=1&section=1.501(r)-5",
        kind="ecfr",
    ),
    "federal-501r-6": PolicySource(
        "federal-501r-6",
        "26 CFR 1.501(r)-6 (billing and collection)",
        "https://www.ecfr.gov/api/versioner/v1/full/2026-10-01/title-26.xml?part=1&section=1.501(r)-6",
        kind="ecfr",
    ),
    "nsa-149-410": PolicySource(
        "nsa-149-410",
        "45 CFR 149.410 (No Surprises Act: emergency services)",
        "https://www.ecfr.gov/api/versioner/v1/full/2026-10-01/title-45.xml?part=149&section=149.410",
        kind="ecfr",
    ),
    "nsa-149-420": PolicySource(
        "nsa-149-420",
        "45 CFR 149.420 (No Surprises Act: out-of-network care at an in-network facility)",
        "https://www.ecfr.gov/api/versioner/v1/full/2026-10-01/title-45.xml?part=149&section=149.420",
        kind="ecfr",
    ),
    "nsa-149-610": PolicySource(
        "nsa-149-610",
        "45 CFR 149.610 (good faith estimate for uninsured or self-pay)",
        "https://www.ecfr.gov/api/versioner/v1/full/2026-10-01/title-45.xml?part=149&section=149.610",
        kind="ecfr",
    ),
    "nsa-149-620": PolicySource(
        "nsa-149-620",
        "45 CFR 149.620 (patient-provider dispute resolution)",
        "https://www.ecfr.gov/api/versioner/v1/full/2026-10-01/title-45.xml?part=149&section=149.620",
        kind="ecfr",
    ),
    "erisa-2560-503-1": PolicySource(
        "erisa-2560-503-1",
        "29 CFR 2560.503-1 (claims procedure: appeals of denials)",
        "https://www.ecfr.gov/api/versioner/v1/full/2026-10-01/title-29.xml?part=2560&section=2560.503-1",
        kind="ecfr",
    ),
    "fl-95-11": PolicySource(
        "fl-95-11",
        "Florida Statutes 95.11 (limitations: medical debt)",
        "https://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&URL=0000-0099/0095/Sections/0095.11.html",
        kind="html",
    ),
    "fl-395-301": PolicySource(
        "fl-395-301",
        "Florida Statutes 395.301 (itemized statement, billing disputes)",
        "https://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&URL=0300-0399/0395/Sections/0395.301.html",
        kind="html",
    ),
}

# Each hospital's public standard charges file (CMS Hospital Price Transparency), as listed in the
# cms-hpt.txt at the root of its website. Used by billradar/prices.py.
PRICE_FILES = {
    "memorial-2026-05": (
        "https://www.mhs.net/cms-hpt.txt",
        "https://www.mhs.net/-/media/Files/Standard%20charges/596014973memorialregionalhospitalstandardchargescsv.zip",
    ),
    "baptist-2026-09": (
        "https://baptisthealth.net/cms-hpt.txt",
        "https://baptisthealth.net/-/media/Documents/Patient-Resources/Patient-Pricing/Apr-2026/"
        "590910342_baptist-hospital-of-miami_standardcharges.zip",
    ),
}

FORMS = {
    "memorial-fef": (
        "Memorial Healthcare System Financial Evaluation Form 2310-10040 (5-19)",
        "https://www.mhs.net/-/media/mhs/files/patients-and-visitors/financial-assistance/en/financial-evaluation-form-english.pdf",
    ),
}


def form_path(form_id: str) -> Path:
    return CACHE.parent / "forms" / f"{form_id}.pdf"


def pdf_path(policy_id: str) -> Path:
    ext = {"ecfr": "xml", "html": "html"}.get(SOURCES[policy_id].kind, "pdf")
    return CACHE / f"{policy_id}.{ext}"


def is_cached(policy_id: str) -> bool:
    return pdf_path(policy_id).exists()


def compact(text: str) -> str:
    """Quote-matching key: unify quotes/dashes, lowercase, drop all whitespace.

    pdf text layers split words at random ("balan ces"), so spaces cannot be trusted.
    """
    return re.sub(r"\s+", "", normalize(text))


def normalize(text: str) -> str:
    """Collapse whitespace and unify quotes/dashes so a quote matches across line breaks."""
    text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"')
                .replace("–", "-").replace("—", "-").replace(" ", " "))
    text = re.sub(r"-\s*\n\s*", "-", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def page_texts(policy_id: str) -> list[str]:
    """Text of every page, 1-based page N is index N-1."""
    path = pdf_path(policy_id)
    if SOURCES[policy_id].kind in ("ecfr", "html"):
        markup = path.read_text(encoding="utf-8", errors="replace")
        return [html.unescape(re.sub(r"<[^>]+>", " ", markup))]
    reader = PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


def sha256(policy_id: str) -> str:
    return hashlib.sha256(pdf_path(policy_id).read_bytes()).hexdigest()
