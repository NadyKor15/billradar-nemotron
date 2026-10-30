"""Which models answer: NVIDIA Nemotron on Nebius Token Factory.

Nemotron 3.5 Super VL reads and finds sheets, Nemotron 3 Ultra does the second read, the text steps go to the
model their measurement chose (nemotron_client). BILLRADAR_LIVE=1 plus NEBIUS_API_KEY calls the live API;
otherwise recorded answers are replayed, and a request with no recording fails loudly.
"""

from __future__ import annotations

from billradar import sheets_vl
from billradar.nemotron_client import NemotronClient

PROVIDER = "nebius"


def name() -> str:
    return PROVIDER


def make_client():
    return NemotronClient()


def sheet_finder(client):
    """Super VL finds each sheet's box; local code finds its corners."""
    return sheets_vl.find_sheets


def models(client) -> dict[str, str]:
    """Which model did which step, for screens and logs."""
    from billradar import nemotron_client as nc
    return {"sheets": nc.READER_MODEL, "read": nc.READER_MODEL, "text": nc.TEXT_MODEL, "ask": nc.QA_MODEL,
            "check": nc.CHECKER_MODEL}


def _short(model_id: str) -> str:
    """nvidia/Nemotron-3.5-Super-VL -> Nemotron 3.5 Super VL."""
    named = {"nvidia/nemotron-3-super-120b-a12b": "Nemotron 3 Super", "nvidia/Nemotron-3-Ultra-550b-a55b": "Nemotron 3 Ultra",
             "nvidia/Nemotron-3.5-Super-VL": "Nemotron 3.5 Super VL", "nvidia/Nemotron-3_5-Lightning": "Nemotron 3.5 Lightning"}
    return named.get(model_id, model_id.split("/")[-1].replace("-", " "))


def labels(client=None) -> dict[str, str]:
    """Screen names of the models for every role. Roles without a model on Token Factory (voice, the portal
    agent) are empty strings, and the screens say so instead."""
    from billradar import nemotron_client as nc
    return {"provider": "nebius", "platform": "NVIDIA Nemotron on Nebius Token Factory",
            "find": _short(nc.READER_MODEL), "read": _short(nc.READER_MODEL), "ask": _short(nc.QA_MODEL),
            "text": _short(nc.TEXT_MODEL), "review": _short(nc.CHECKER_MODEL), "agent": _short(nc.AGENT_MODEL),
            "voice": "", "portal": ""}
