"""Download each hospital's public standard charges file into .cache/mrf/ (not committed) and index it.

Usage: python scripts/fetch_prices.py [policy_id ...]
Files are listed in the hospital's cms-hpt.txt (see billradar/prices.py). Downloads stop at MAX_BYTES.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from billradar import prices  # noqa: E402

MAX_BYTES = 400_000_000
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) BillRadar price fetch"}


def fetch(policy_id: str) -> None:
    src = prices.SOURCES[policy_id]
    out = prices.raw_path(policy_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(src.url, headers=HEADERS, stream=True, timeout=120) as r:
        r.raise_for_status()
        size = 0
        with out.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise RuntimeError(f"{policy_id}: file is over {MAX_BYTES} bytes; stopped")
                f.write(chunk)
    t = time.monotonic()
    idx = prices.write_index(policy_id)
    print(f"ok   {policy_id}: {size} bytes, index {idx.stat().st_size} bytes in {time.monotonic() - t:.1f} s")


def main(ids: list[str]) -> int:
    failed = 0
    for pid in ids or list(prices.SOURCES):
        try:
            fetch(pid)
        except Exception as exc:  # noqa: BLE001 - report every hospital, then fail
            print(f"FAIL {pid}: {exc}")
            failed += 1
    print(f"errors: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
