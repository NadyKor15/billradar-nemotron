"""Tavily search and extract, live or replayed, for finding a hospital's own financial assistance policy.

Two transports, as in nemotron_client:
  live    POST https://api.tavily.com/{search,extract}; only when TAVILY_API_KEY is set AND BILLRADAR_LIVE=1.
          Every live answer is recorded to disk.
  replay  answers from recorded files. A request we have no recording for is an error.

What goes to Tavily: the hospital's name, its city or ZIP, its state and its website's domain. Never a bill,
a name from the family, an amount or an account number; policy_finder builds every request from those four.

Credits: before each live call its cost (search basic 1, advanced 2; extract 1 per 5 URLs) is reserved in
runs/tavily-credits.jsonl and the call is refused if that passes TAVILY_CREDIT_CAP (default 60).
Journal: every request goes to runs/requests.jsonl next to the Nemotron calls, with Tavily's request_id,
the credits it reports and its response time.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from billradar.nemotron_client import ROOT

BASE_URL = os.environ.get("TAVILY_BASE_URL", "https://api.tavily.com")
RECORDINGS = ROOT / "tests" / "fixtures" / "recordings-tavily"
JOURNAL = ROOT / "runs" / "requests.jsonl"
CREDITS = ROOT / "runs" / "tavily-credits.jsonl"
ENDPOINTS = ("search", "extract")
RETRY_STATUS = {429, 432, 433, 434, 500, 502, 503}


class TavilyError(RuntimeError):
    pass


class ReplayMissing(TavilyError):
    pass


class CreditCapReached(RuntimeError):
    """Not a TavilyError on purpose: it must stop the run, not be handled as one failed search."""


def request_key(endpoint: str, body: dict) -> str:
    canon = json.dumps({"endpoint": endpoint, "body": body}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:20]


def credits_for(endpoint: str, body: dict) -> int:
    """Worst-case credits of one call, as docs.tavily.com prices them (2026-10-29)."""
    if endpoint == "search":
        return 2 if body.get("search_depth") == "advanced" else 1
    urls = body.get("urls") or []
    per = 2 if body.get("extract_depth") == "advanced" else 1
    return per * max(1, -(-len(urls) // 5))


class CreditLedger:
    """Credits reserved and settled, across processes, in one append-only file."""

    def __init__(self, path: Path = CREDITS, cap: int | None = None):
        self.path = path
        self.cap = cap if cap is not None else int(os.environ.get("TAVILY_CREDIT_CAP", "60"))

    def spent(self) -> int:
        if not self.path.exists():
            return 0
        return sum(int(json.loads(line).get("credits", 0)) for line in self.path.read_text().splitlines() if line.strip())

    def reserve(self, credits: int, note: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            total = sum(int(json.loads(line).get("credits", 0)) for line in f.read().splitlines() if line.strip())
            if total + credits > self.cap:
                raise CreditCapReached(f"Tavily credits {total} + {credits} would pass the cap of {self.cap} "
                                       "(TAVILY_CREDIT_CAP); stopped before the call")
            f.write(json.dumps({"at": _now(), "credits": credits, "note": note}) + "\n")

    def settle(self, reserved: int, actual: int | None, note: str) -> None:
        """Replace the reservation with what Tavily reported (when it reports less)."""
        if actual is None or actual == reserved:
            return
        with self.path.open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps({"at": _now(), "credits": actual - reserved, "note": f"settle {note}"}) + "\n")


@dataclass
class Answer:
    raw: dict
    status: int = 200


class LiveTransport:
    def __init__(self, api_key: str, ledger: CreditLedger | None = None, attempts: int = 3, timeout: float = 90.0):
        self.api_key = api_key
        self.ledger = ledger or CreditLedger()
        self.attempts = attempts
        self.timeout = timeout

    def answer(self, endpoint: str, body: dict) -> Answer:
        cost = credits_for(endpoint, body)
        self.ledger.reserve(cost, endpoint)
        last = ""
        for attempt in range(self.attempts):
            try:
                r = requests.post(f"{BASE_URL}/{endpoint}", json={**body, "include_usage": True}, timeout=self.timeout,
                                  headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            except requests.RequestException as exc:
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(2 ** attempt)
                continue
            if r.status_code in RETRY_STATUS and attempt < self.attempts - 1:
                last = f"HTTP {r.status_code}"
                time.sleep(2 ** attempt)
                continue
            if r.status_code != 200:
                raise TavilyError(f"Tavily {endpoint}: HTTP {r.status_code} {r.text[:200]}")
            raw = r.json()
            self.ledger.settle(cost, (raw.get("usage") or {}).get("credits"), endpoint)
            return Answer(raw)
        raise TavilyError(f"Tavily {endpoint}: no answer after {self.attempts} attempts ({last})")


class ReplayTransport:
    def __init__(self, directory: Path = RECORDINGS):
        self.directory = directory

    def path(self, endpoint: str, body: dict) -> Path:
        return self.directory / f"{endpoint}-{request_key(endpoint, body)}.json"

    def answer(self, endpoint: str, body: dict) -> Answer:
        p = self.path(endpoint, body)
        if not p.exists():
            raise ReplayMissing(f"no Tavily recording {p.name}; run once with BILLRADAR_LIVE=1 and TAVILY_API_KEY")
        return Answer(json.loads(p.read_text())["response"])


class TavilyClient:
    provider = "tavily"

    def __init__(self, transport=None, record_to: Path | None = RECORDINGS, journal: Path | None = JOURNAL):
        self.transport = transport or self._default_transport()
        self.record_to = record_to
        self.journal = journal
        self.calls: list[dict] = []

    @staticmethod
    def _default_transport():
        key = os.environ.get("TAVILY_API_KEY", "")
        if key and os.environ.get("BILLRADAR_LIVE") == "1":
            return LiveTransport(key)
        return ReplayTransport()

    @property
    def live(self) -> bool:
        return isinstance(self.transport, LiveTransport)

    def search(self, body: dict, label: str = "") -> dict:
        return self._call("search", body, label)

    def extract(self, body: dict, label: str = "") -> dict:
        return self._call("extract", body, label)

    def _call(self, endpoint: str, body: dict, label: str) -> dict:
        if endpoint not in ENDPOINTS:
            raise TavilyError(f"unknown Tavily endpoint {endpoint!r}")
        started = time.monotonic()
        ans = self.transport.answer(endpoint, body)
        seconds = round(time.monotonic() - started, 2)
        if self.live and self.record_to:
            self.record_to.mkdir(parents=True, exist_ok=True)
            out = self.record_to / f"{endpoint}-{request_key(endpoint, body)}.json"
            out.write_text(json.dumps({"request": body, "response": ans.raw}, indent=1))
        entry = {
            "at": _now(), "label": label, "provider": self.provider, "endpoint": f"{BASE_URL}/{endpoint}",
            "key": request_key(endpoint, body), "live": self.live, "request_id": ans.raw.get("request_id", ""),
            "credits": (ans.raw.get("usage") or {}).get("credits"), "response_time": ans.raw.get("response_time"),
            "results": len(ans.raw.get("results") or []), "seconds": seconds,
        }
        self.calls.append(entry)
        if self.journal:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        return ans.raw


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
