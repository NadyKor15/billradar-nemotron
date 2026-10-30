"""What every model call in BillRadar shares: the request key, the answer, the errors.

The modules that build requests (reader, verifier, ...) write Responses-style bodies; the client
(nemotron_client) translates them for the provider. A recorded answer is found by request_key(body):
the canonical JSON of the body with every data URL replaced by its hash, so a recording never holds
the photos or files that were sent.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ModelError(RuntimeError):
    pass


class ReplayMissing(ModelError):
    pass


def data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def _media_digest(url: str) -> str:
    return "sha256:" + hashlib.sha256(url.encode()).hexdigest()[:16]


def _strip_media(obj):
    """Same body with every data URL replaced by its hash: the replay key and the journal entry."""
    if isinstance(obj, dict):
        return {k: _strip_media(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_media(v) for v in obj]
    if isinstance(obj, str) and obj.startswith("data:"):
        return _media_digest(obj)
    return obj


def request_key(body: dict) -> str:
    canon = json.dumps(_strip_media(body), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:20]


@dataclass
class Result:
    text: str
    events: list[dict]
    usage: dict = field(default_factory=dict)
    response_id: str = ""
    replayed: bool = False
    seconds: float = 0.0
