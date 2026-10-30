"""Which model answered each step, from the request journal: the proof that a run went through Nemotron.

python scripts/nemotron_proof.py                 # runs/requests.jsonl, every Nebius entry
python scripts/nemotron_proof.py --live-only     # only calls that reached Token Factory (not replays)
python scripts/nemotron_proof.py --since 2026-10-28T00:00:00

Each row is one request: step label, the model id we asked for, the model id Token Factory answered
with, its response id and x-request-id (both can be matched against the Token Factory console),
tokens and cost. Media is never in the journal.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rows(journal: Path, live_only: bool, since: str) -> list[dict]:
    out = []
    for line in journal.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("provider") != "nebius" or (live_only and not e.get("live")) or e.get("at", "") < since:
            continue
        out.append(e)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", type=Path, default=ROOT / "runs" / "requests.jsonl")
    ap.add_argument("--live-only", action="store_true")
    ap.add_argument("--since", default="")
    a = ap.parse_args(argv)
    if not a.journal.exists():
        print(f"no journal at {a.journal}", file=sys.stderr)
        return 1
    found = rows(a.journal, a.live_only, a.since)
    print(f"{'at (UTC)':20} {'step':14} {'live':5} {'answered by':36} {'in':>7} {'out':>6} {'reason':>6} {'usd':>8}  "
          "response id / x-request-id")
    for e in found:
        u = e.get("usage", {})
        print(f"{e['at'][:19]:20} {e.get('label', '')[:14]:14} {str(e.get('live')):5} "
              f"{(e.get('answered_by') or '(no model in answer)')[:36]:36} {u.get('input_tokens', 0):>7} "
              f"{u.get('output_tokens', 0):>6} {u.get('output_tokens_details', {}).get('reasoning_tokens', 0):>6} "
              f"{e.get('usd', 0):>8.4f}  {e.get('response_id', '')} / {e.get('request_id', '')}")
    live = [e for e in found if e.get("live")]
    print(f"\n{len(found)} Nebius request(s), {len(live)} live, ${sum(e.get('usd', 0) for e in live):.4f} live spend; "
          f"models: {sorted({e.get('answered_by') or e.get('model') for e in found})}")
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
