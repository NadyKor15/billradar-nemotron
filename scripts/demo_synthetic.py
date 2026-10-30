"""Build runs/demo-synthetic: a drawn table of SAMPLE sheets + SYNTHETIC model answers -> case screen.

For working on the screen before the API credits arrive. Nothing here is a real bill,
and no model was called (see billradar/example.py). The screen shows a "replay" badge.

python scripts/demo_synthetic.py && uvicorn billradar.app:app  ->  /cases/demo-synthetic
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from billradar import example, pipeline  # noqa: E402


def main() -> None:
    run = example.run_demo_synthetic()
    out = ROOT / "runs" / "demo-synthetic"
    pipeline.save(run, out)
    print(f"saved {out}: {len(run.sheets)} sheets, total ${run.report.total_may_come_off}, problems: {run.problems}")


if __name__ == "__main__":
    main()
