"""Which Nemotron should read the case a second time? Same live readings, same findings, one model per run.

BILLRADAR_LIVE=1 python scripts/eval_review.py runs/live-priv2 \
    nvidia/Nemotron-3_5-Lightning nvidia/nemotron-3-super-120b-a12b nvidia/Nemotron-3-Ultra-550b-a55b

Input: the case.json of live runs (one folder per pack under the runs folder, pack name = folder name). The
findings are recomputed by code from that reading, before anything is held back, and each one is marked right or
wrong against the pack's key: right = a key item of the same class whose amount is within $1 (when the key has one)
and whose deadline is the same (when the key has one); a second finding matching an already matched key item is a
duplicate and counts as wrong. The model then reads the case (verifier.model_review) and we count how many wrong
findings it calls "problem" (good) and how many right ones it calls "problem" (bad).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from billradar import checks, verifier, writer  # noqa: E402
from billradar import evaluation as ev  # noqa: E402
from billradar import nemotron_client as nc  # noqa: E402
from billradar.case import Case  # noqa: E402

PACKS = ROOT / "eval" / "packs"


def truth(report: checks.Report, key: ev.Key) -> dict[str, bool]:
    """Subject ("F1 #2") -> is this finding right by the key."""
    used: set[int] = set()
    out = {}
    names = verifier.subjects(report)
    for f in report.findings:
        ok = False
        for i, k in enumerate(key.items):
            if i in used or k.cls != f.kind.value:
                continue
            if k.amount is not None and (f.amount is None or abs(Decimal(f.amount) - k.amount) > 1):
                continue
            if k.deadline is not None and f.deadline != k.deadline:
                continue
            used.add(i)
            ok = True
            break
        out[names[id(f)]] = ok
    return out


def main(argv: list[str]) -> int:
    runs, models = Path(argv[0]), argv[1:]
    cases = []
    for d in sorted(p for p in runs.iterdir() if (p / "case.json").exists()):
        key_file = next(PACKS.glob(f"*/{d.name}/key.json"))
        case = Case.model_validate_json((d / "case.json").read_text())
        report = checks.run(case)
        cases.append((d.name, case, report, writer.all_letters(case, report),
                      truth(report, ev.Key.model_validate_json(key_file.read_text()))))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    rows, detail = [], []
    for model in models:
        nc.MODEL_FOR["checker"] = model  # this run sets the second-read role explicitly
        client = nc.NemotronClient()
        wrong_flagged = wrong = right_flagged = right = errors = 0
        for name, case, report, letters, t in cases:
            try:
                v = verifier.model_review(client, case, report, letters, verifier.Verdict())
            except nc.NemotronError as exc:
                errors += 1
                detail.append({"model": model, "pack": name, "error": str(exc)[:300]})
                continue
            said = {it.get("subject"): it.get("verdict") for it in v.model_notes}
            for subj, ok in t.items():
                flagged = said.get(subj) == "problem"
                if ok:
                    right, right_flagged = right + 1, right_flagged + flagged
                else:
                    wrong, wrong_flagged = wrong + 1, wrong_flagged + flagged
                detail.append({"model": model, "pack": name, "subject": subj, "right_by_key": ok,
                               "model_said": said.get(subj, "(not answered)")})
        usd = sum(c["usd"] for c in client.calls)
        secs = sum(c["seconds"] for c in client.calls) / max(1, len(client.calls))
        rows.append((model, wrong_flagged, wrong, right_flagged, right, errors, usd, secs, client.live))
    out = ROOT / "eval" / "results" / f"{stamp}-review"
    out.mkdir(parents=True, exist_ok=True)
    (out / "verdicts.json").write_text(json.dumps(detail, indent=1))
    lines = [f"# Second read of the case: which Nemotron ({runs.name}, {stamp}, "
             f"{'live' if all(r[8] for r in rows) else 'REPLAYED (not a measurement)'})", "",
             "| Model | Wrong findings it doubts | Right findings it doubts | Failed calls | Cost | Seconds per case |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {m} | {wf} of {w} | {rf} of {r} | {e} | ${usd:.4f} | {s:.1f} |"
              for m, wf, w, rf, r, e, usd, s, _ in rows]
    md = "\n".join(lines) + "\n"
    (out / "review.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
