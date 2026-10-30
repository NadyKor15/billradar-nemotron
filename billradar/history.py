"""Past cases on this device, without accounts.

Every run lives in runs/<case_id>/. The browser keeps its own list of the cases it made
(localStorage); the server lists every run only when it is not a public deploy.
Deleting needs the token handed out with the case (a cookie on that browser) on a public
deploy; the example cases can never be deleted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from billradar.example import READ_ONLY

CASE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
OWNER = "owner.json"


def public() -> bool:
    """A public deploy: no server-wide list, delete only with the case's own token."""
    return os.environ.get("BILLRADAR_PUBLIC") == "1"


def cookie_name(case_id: str) -> str:
    return f"br_{case_id}"


@dataclass
class HistoryRow:
    case_id: str
    title: str
    letterhead: str
    created: str  # UTC, ISO
    total: str
    findings: int
    sheets: int
    demo: bool
    example: bool

    def to_dict(self) -> dict:
        return asdict(self)


def run_dir(runs: Path, case_id: str) -> Path | None:
    if not CASE_ID.fullmatch(case_id):
        return None
    d = runs / case_id
    return d if (d / "case.json").exists() else None


def _created(d: Path) -> str:
    owner = d / OWNER
    if owner.exists():
        return json.loads(owner.read_text())["created"]
    return datetime.fromtimestamp((d / "case.json").stat().st_mtime, UTC).isoformat(timespec="seconds")


def summary(d: Path) -> HistoryRow:
    case = json.loads((d / "case.json").read_text())
    report = json.loads((d / "report.json").read_text())
    people = case["profile"].get("people", [])
    mom = next((p for p in people if p.get("role") == "mother"), None)
    name = (mom or {}).get("name", "").split(" ")[0]
    birth = any(p.get("role") == "newborn" for p in people)
    title = f"{name}'s {'birth ' if birth else ''}case" if name else f"{'Birth' if birth else 'Your'} case"
    return HistoryRow(
        case_id=d.name, title=title, letterhead=case.get("letterhead", ""), created=_created(d),
        total=report.get("total_may_come_off", "0"), findings=len(report.get("findings", [])),
        sheets=sum(1 for doc in case.get("documents", []) if doc.get("source") == "paper"),
        demo=bool(case.get("demo")), example=d.name in READ_ONLY)


def list_runs(runs: Path) -> list[HistoryRow]:
    if not runs.exists():
        return []
    rows = [summary(d) for d in runs.iterdir() if d.is_dir() and CASE_ID.fullmatch(d.name)
            and (d / "case.json").exists() and (d / "report.json").exists()]
    return sorted(rows, key=lambda r: r.created, reverse=True)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(d: Path, now: datetime | None = None) -> str:
    """Written with a new case; the browser that made the case gets the token, the server keeps a hash."""
    token = secrets.token_urlsafe(24)
    created = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    (d / OWNER).write_text(json.dumps({"token_sha256": _hash(token), "created": created}))
    return token


def may_delete(d: Path, token: str | None) -> bool:
    if d.name in READ_ONLY:
        return False
    owner = d / OWNER
    if owner.exists() and token:
        if secrets.compare_digest(json.loads(owner.read_text())["token_sha256"], _hash(token)):
            return True
    return not public()  # on this device (not a public deploy) the owner is whoever runs it


def delete(runs: Path, case_id: str, token: str | None) -> bool:
    """Remove the case and everything made for it. False if not allowed; raises nothing for odd ids."""
    d = run_dir(runs, case_id)
    if d is None or not may_delete(d, token):
        return False
    target = d.resolve()
    if target.parent != runs.resolve():  # never outside runs/
        return False
    shutil.rmtree(target)
    return True
