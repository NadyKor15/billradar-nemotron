"""Download the public financial assistance policies into .cache/policies/ (not committed).

Usage: python scripts/fetch_policies.py
"""

from __future__ import annotations

import sys

import requests

from billradar.policies.source import CACHE, FORMS, SOURCES, form_path, page_texts, pdf_path, sha256

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) BillRadar policy fetch"}


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    failed = 0
    for src in SOURCES.values():
        r = requests.get(src.url, headers=HEADERS, timeout=60)  # requests sends gzip, eCFR needs it
        magic = {"ecfr": b"<?xml", "html": b"<"}.get(src.kind, b"%PDF")
        if r.status_code != 200 or not r.content.lstrip().startswith(magic):
            print(f"FAIL {src.policy_id}: HTTP {r.status_code}, {r.headers.get('content-type')}")
            failed += 1
            continue
        pdf_path(src.policy_id).write_bytes(r.content)
        pages = page_texts(src.policy_id)
        print(f"ok   {src.policy_id}: {len(r.content)} bytes, {len(pages)} pages, sha256 {sha256(src.policy_id)[:16]}")
    for form_id, (title, url) in FORMS.items():
        r = requests.get(url, headers=HEADERS, timeout=60)
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            print(f"FAIL {form_id}: HTTP {r.status_code}")
            failed += 1
            continue
        form_path(form_id).parent.mkdir(parents=True, exist_ok=True)
        form_path(form_id).write_bytes(r.content)
        print(f"ok   {form_id}: {len(r.content)} bytes ({title})")
    print(f"errors: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
