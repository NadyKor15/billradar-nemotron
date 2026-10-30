"""billradar check <case.json>        run the checks on an already-read case
billradar run <pack-dir> [--out DIR]  photos + PDFs + profile.json -> case, findings, overlays
billradar cover <dir> --out DIR       private mode's covering alone, on this device, no model call: the covered
                                      photos and PDF pages are written to DIR to look at; exit 1 if any would be held back

A pack directory holds table photos (*.jpg, *.jpeg, *.png), digital files (*.pdf) and
profile.json: {"policy_id", "letterhead", "today", "profile": {...}}.
Without BILLRADAR_LIVE=1 and MODEL_API_KEY every model call is replayed from recordings.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from billradar import checks, pipeline, providers
from billradar.case import Case, Profile


def _print_report(r: checks.Report) -> None:
    print(f"{r.case_id}: income {r.fpl_pct}% of the poverty guideline, policy {r.policy_id}")
    for f in r.findings:
        money = f" ${f.amount:,.2f}" if f.amount is not None else ""
        print(f"  [{f.kind}] {f.title}{money}")
        print(f"        {f.detail}")
    for f in r.no_money:
        print(f"  [no money] {f.title}")
    for f in r.held_back:
        print(f"  [held back] {f.title}")
    print(f"  total that may come off: ${r.total_may_come_off:,.2f}; apply by: {r.apply_by or '-'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="billradar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("case_json", type=Path)
    r = sub.add_parser("run")
    r.add_argument("pack", type=Path)
    r.add_argument("--out", type=Path, default=None)
    v = sub.add_parser("cover")
    v.add_argument("pack", type=Path)
    v.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    if args.cmd == "cover":
        return _cover(args.pack, args.out)

    if args.cmd == "check":
        case = Case.model_validate_json(args.case_json.read_text())
        _print_report(checks.run(case))
        return 0

    info = json.loads((args.pack / "profile.json").read_text())
    photos = [p.read_bytes() for p in sorted(args.pack.iterdir()) if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    files = [(p.name, p.read_bytes()) for p in sorted(args.pack.iterdir()) if p.suffix.lower() == ".pdf"]
    client = providers.make_client()
    print(f"mode: {'LIVE' if client.live else 'replay'}; {len(photos)} photo(s), {len(files)} file(s)")
    result = pipeline.run(client, photos, files, Profile.model_validate(info["profile"]), info["policy_id"],
                          info.get("letterhead", ""), date.fromisoformat(info["today"]) if info.get("today") else None,
                          case_id=args.pack.name, fictional=bool(info.get("fictional")))
    out = args.out or Path("runs") / args.pack.name
    pipeline.save(result, out)
    for p in result.problems:
        print(f"  ! {p}")
    _print_report(result.report)
    print(f"saved to {out}")
    return 0


def _cover(pack: Path, out: Path) -> int:
    """Names of what was written and whether it would be sent; never a covered value."""
    info = json.loads((pack / "profile.json").read_text())
    photos = [(p.name, p.read_bytes()) for p in sorted(pack.iterdir()) if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    files = [(p.name, p.read_bytes()) for p in sorted(pack.iterdir()) if p.suffix.lower() == ".pdf"]
    out.mkdir(parents=True, exist_ok=True)
    held = 0
    for n, c in enumerate(pipeline.cover_only(photos, files, Profile.model_validate(info["profile"])), start=1):
        if c.image is not None:
            c.image.save(out / f"{n:02d}-covered.jpg", quality=90)
        held += bool(c.blocked)
        print(f"{n:02d}-covered.jpg  {c.name}: {'HELD BACK, still readable: ' + c.blocked if c.blocked else 'ok to send'}")
    print(f"{held} of {n} would be held back; look at every page in {out} before trusting it")
    return 1 if held else 0


if __name__ == "__main__":
    sys.exit(main())
