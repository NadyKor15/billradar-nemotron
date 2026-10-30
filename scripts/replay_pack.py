"""Run one pack through the whole pipeline with SYNTHETIC model answers taken from the pack's generator.

The generator knows what is printed on every sheet. It writes answers/reading.json (what a perfect
reader would return, in billradar's reading schema) and answers/sheets.json (the corners of every sheet
on every photo). The sheet finder and the reader are then answered from those files, so this checks that photos, sheet
numbering, reading, checks, letters and the screen fit together. It is not a measurement of the models.

python scripts/replay_pack.py eval/packs/work/brs1-birth-all-errors [--private]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from billradar import evaluation as ev  # noqa: E402
from billradar import pipeline  # noqa: E402
from tests.fakes import PackAnswers, run_pack  # noqa: E402,F401  (tests load them from here)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pack", type=Path)
    ap.add_argument("--private", action="store_true")
    args = ap.parse_args(argv)
    pack = args.pack
    run = run_pack(pack, private=args.private)
    out = ROOT / "runs" / pack.name
    pipeline.save(run, out)
    key = ev.Key.model_validate_json((pack / "key.json").read_text())
    score = ev.score(key, ev.from_report(run.report))
    print(f"{pack.name}: {len(run.sheets)} sheets, total ${run.report.total_may_come_off}, problems: {len(run.problems)}")
    for p in run.problems:
        print("  !", p)
    print("REPLAYED (synthetic answers, not a measurement)\n" + ev.table({"BillRadar": score}))
    print(f"saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
