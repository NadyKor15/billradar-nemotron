"""Questions about a case: quotes typed by the model (ask.answer_typed) against quotes picked by code (qa_cite.answer).

BILLRADAR_PROVIDER=nebius BILLRADAR_LIVE=1 python scripts/qa_eval.py work [model] [typed|picked]

Same packs, same questions and the same strict checker as scripts/eval_routing.py (imported, not copied):
right = not refused, at least one citation that BillRadar verified, and the expected value in the answer.
A second, stricter column, "honest": right, and a citation actually shows the expected value: it is in a cited
quote or on a cited sheet, or (for a computed amount or date) a cited sheet or policy is the evidence of the
finding that holds it. A real but unrelated quote passes "right" and fails "honest".
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_routing import PACKS, case_from_reading, questions, right  # noqa: E402

from billradar import ask, checks, qa_cite  # noqa: E402
from billradar import evaluation as ev  # noqa: E402
from billradar import nemotron_client as nc  # noqa: E402
from billradar.policies.source import compact  # noqa: E402


def _forms(f) -> list[str]:
    out = []
    if f.amount is not None:
        out += [f"{f.amount:,.2f}", f"{f.amount:.2f}"]
    if f.deadline:
        out.append(f.deadline.isoformat())
    return out


def honest(case, report, a: ask.Answer, expect: list[str]) -> bool:
    if not right(a, expect):
        return False
    keys = [compact(e).replace("$", "") for e in expect]
    refs = {c.ref for c in a.citations}
    for c in a.citations:
        if c.ref in expect or any(k in compact(c.quote).replace("$", "") for k in keys):
            return True
        if c.source == "sheet" and any(k in (ask._sheet_text(case, c.ref) or "") for k in keys):
            return True
    for f in report.findings + report.worth_asking:
        if any(compact(x) in keys for x in _forms(f)):
            if refs & ({e.doc_id for e in f.evidence} | set(f.policy_refs) | set(f.laws)):
                return True
    return False


def main(argv: list[str]) -> int:
    split = argv[0]
    model = argv[1] if len(argv) > 1 else nc.QA_MODEL
    nc.TEXT_ROUTE["ask"] = model
    packs = sorted(p for p in (PACKS / split).glob("*") if (p / "key.json").exists() and (p / "answers").exists())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    paths = {"typed (ask.answer_typed)": ask.answer_typed, "picked (qa_cite.answer)": qa_cite.answer}
    if len(argv) > 2:
        paths = {k: v for k, v in paths.items() if k.startswith(argv[2])}
    rows, detail = [], []
    for name, fn in paths.items():
        client = nc.NemotronClient()
        n = ok = good = refused = 0
        for pack in packs:
            case = case_from_reading(pack)
            report = checks.run(case)
            for cls, q, expect in questions(ev.Key.model_validate_json((pack / "key.json").read_text())):
                a = fn(client, case, report, q)
                r, h = right(a, expect), honest(case, report, a, expect)
                n, ok, good, refused = n + 1, ok + r, good + h, refused + a.refused
                detail.append({"path": name, "model": model, "pack": pack.name, "cls": cls, "question": q,
                               "expect": expect, "right": r, "honest": h, "refused": a.refused, "reason": a.reason,
                               "answer": a.text, "citations": [c.__dict__ for c in a.citations],
                               "dropped": [c.__dict__ for c in a.dropped]})
        usd = sum(c["usd"] for c in client.calls)
        secs = sum(c["seconds"] for c in client.calls) / max(1, len(client.calls))
        rows.append((name, ok, good, n, refused, usd, secs, client.live))
    out = ROOT / "eval" / "results" / f"{stamp}-qa"
    out.mkdir(parents=True, exist_ok=True)
    (out / "answers.json").write_text(json.dumps(detail, indent=1, default=str))
    lines = [f"# Questions about a case: typed quotes against picked quotes ({split} packs, {model}, {stamp}, "
             f"{'live' if all(r[7] for r in rows) else 'REPLAYED (not a measurement)'})", "",
             "| Path | Right (strict checker) | Honest | Refused | Cost | Seconds per answer |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {p} | {ok} of {n} | {g} of {n} | {rf} | ${usd:.4f} | {s:.1f} |" for p, ok, g, n, rf, usd, s, _ in rows]
    md = "\n".join(lines) + "\n"
    (out / "qa.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
