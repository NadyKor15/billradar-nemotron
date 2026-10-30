"""Build the SAMPLE plan document PDF from its text source and check every quote against it.

python scripts/make_sample_plan.py [plan_id]
"""

from __future__ import annotations

import sys

from billradar import plans


def main() -> int:
    plan_id = sys.argv[1] if len(sys.argv) > 1 else "sample-health-plan-2026"
    plans.pdf_path(plan_id).write_bytes(plans.render_pdf(plans.source_pages(plan_id)))
    plans.page_texts.cache_clear()
    bad = [(f.fragment_id, why) for f in plans.load(plan_id).fragments.values()
           for ok, why in [plans.verify(f)] if not ok]
    for fid, why in bad:
        print(f"NOT VERIFIED {fid}: {why}")
    print(f"{plans.pdf_path(plan_id).name}: {len(plans.load(plan_id).fragments) - len(bad)} of "
          f"{len(plans.load(plan_id).fragments)} quotes on their pages")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
