"""Which Nemotron should answer questions about a case? Same questions, same cases, one model per run.

BILLRADAR_LIVE=1 python scripts/eval_routing.py work nvidia/Nemotron-3_5-Lightning nvidia/nemotron-3-super-120b-a12b

Each case is built from the pack's perfect reading (answers/reading.json), so the reading step is not measured here,
only the answering model. Questions come from the pack's key: the deadline to apply, the overcharge of a duplicate,
a credit to get back, two amounts on one sheet, the baby billed under the mother's ID, the assistance amount.
An answer counts as right only if it is not refused, keeps at least one citation that BillRadar verified against the
papers or the policy, and contains the expected value. Every call goes to runs/requests.jsonl as usual.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from billradar import ask, checks, i18n, reader, writer  # noqa: E402
from billradar import evaluation as ev  # noqa: E402
from billradar import nemotron_client as nc  # noqa: E402
from billradar.case import Case, Profile, Source  # noqa: E402

PACKS = ROOT / "eval" / "packs"


def case_from_reading(pack: Path) -> Case:
    info = json.loads((pack / "profile.json").read_text())
    reading = json.loads((pack / "answers" / "reading.json").read_text())
    expected = {d["doc_id"]: (Source.PDF if d["doc_id"].startswith("D") else Source.PAPER) for d in reading["documents"]}
    docs, _ = reader.to_documents(reading, expected)
    return Case(case_id=pack.name, letterhead=info.get("letterhead", ""), policy_id=info["policy_id"],
                today=date.fromisoformat(info["today"]), profile=Profile.model_validate(info["profile"]), documents=docs)


def _money_forms(amount: str) -> list[str]:
    v = float(amount)
    return [f"{v:,.2f}", f"{v:.2f}"]


def _date_forms(d: str) -> list[str]:
    x = date.fromisoformat(d)
    return [x.isoformat(), x.strftime("%B %-d, %Y"), x.strftime("%b %-d, %Y"), x.strftime("%B %d, %Y"),
            x.strftime("%b %d, %Y"), x.strftime("%m/%d/%Y")]


def questions(key: ev.Key) -> list[tuple[str, str, list[str]]]:
    """(class, question, any-of expected strings) for the classes this pack's key holds."""
    out = []
    for item in key.items:
        c = item.cls
        if c == "F2" and item.deadline:
            out.append((c, "What is the last day to apply for financial assistance?", _date_forms(str(item.deadline))))
        elif c == "E2" and item.amount:
            out.append((c, "Was any service billed twice, and how much too much was charged?", _money_forms(str(item.amount))))
        elif c == "E3" and item.amount:
            out.append((c, "Is there a credit or an overpayment I should get back? How much?", _money_forms(str(item.amount))))
        elif c == "E4":
            m = re.search(r"\$([\d,]+\.\d\d)", item.note or "")
            if m:
                out.append((c, "Does any sheet ask me to pay two different amounts? Which amount does it ask?",
                            _money_forms(m.group(1).replace(",", ""))))
        elif c == "B1":
            out.append((c, "Was the baby's care billed under the wrong person's insurance? Which sheet shows it?",
                        [s for s in item.sheets if s.startswith("S")]))
        elif c == "F1" and item.amount:
            out.append((c, "How much could financial assistance take off my hospital bills?", _money_forms(str(item.amount))))
    return out


def right(a: ask.Answer, expect: list[str]) -> bool:
    text = a.text.replace(" ", " ").replace(" ", " ")
    return (not a.refused) and bool(a.citations) and any(e in text for e in expect)


def translations(packs: list[Path], model: str) -> tuple[int, int, nc.NemotronClient]:
    """Every letter of every pack, into Russian and Spanish. Right = every paragraph keeps its numbers and quotes
    (i18n.check_translation); a paragraph that fails stays in English, as the app does."""
    nc.TEXT_MODEL = model
    client = nc.NemotronClient()
    n = ok = 0
    for pack in packs:
        case = case_from_reading(pack)
        case.demo = True  # fictional pack: live calls allowed
        for letter in writer.all_letters(case, checks.run(case)):
            for lang in ("ru", "es"):
                tr = i18n.letter(client, case, letter, lang)
                parts = 1 + len(letter.paragraphs)
                n, ok = n + parts, ok + parts - len(tr.failed)
    return ok, n, client


def main(argv: list[str]) -> int:
    split, models = argv[0], argv[1:]
    packs = sorted(p for p in (PACKS / split).glob("*") if (p / "key.json").exists() and (p / "answers").exists())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    rows, detail = [], []
    for model in models:
        nc.TEXT_ROUTE["ask"] = model  # this run sets the role explicitly (env is read at import)
        client = nc.NemotronClient()
        n = ok = refused = 0
        for pack in packs:
            case = case_from_reading(pack)
            report = checks.run(case)
            for cls, q, expect in questions(ev.Key.model_validate_json((pack / "key.json").read_text())):
                a = ask.answer(client, case, report, q)
                good = right(a, expect)
                n, ok, refused = n + 1, ok + good, refused + a.refused
                detail.append({"model": model, "pack": pack.name, "cls": cls, "question": q, "right": good,
                               "refused": a.refused, "reason": a.reason, "answer": a.text,
                               "citations": len(a.citations), "dropped": len(a.dropped)})
        usd = sum(c["usd"] for c in client.calls)
        secs = sum(c["seconds"] for c in client.calls)
        rows.append((model, ok, n, refused, usd, secs / max(1, len(client.calls)), client.live))
    trows = []
    for model in models:
        ok, n, client = translations(packs, model)
        trows.append((model, ok, n, sum(c["usd"] for c in client.calls),
                      sum(c["seconds"] for c in client.calls) / max(1, len(client.calls)), client.live))
    out = ROOT / "eval" / "results" / f"{stamp}-routing"
    out.mkdir(parents=True, exist_ok=True)
    (out / "answers.json").write_text(json.dumps(detail, indent=1))
    lines = [f"# Questions about a case: which Nemotron ({split} packs, {stamp}, "
             f"{'live' if all(r[6] for r in rows) else 'REPLAYED (not a measurement)'})", "",
             "| Model | Right | Refused | Cost of all answers | Seconds per answer |", "|---|---|---|---|---|"]
    lines += [f"| {m} | {ok} of {n} | {rf} | ${usd:.4f} | {s:.1f} |" for m, ok, n, rf, usd, s, _ in rows]
    lines += ["", "Letters translated into Russian and Spanish (every number and quote kept):", "",
              "| Model | Parts kept | Cost | Seconds per letter |", "|---|---|---|---|"]
    lines += [f"| {m} | {ok} of {n} | ${usd:.4f} | {s:.1f} |" for m, ok, n, usd, s, _ in trows]
    report_md = "\n".join(lines) + "\n"
    (out / "routing.md").write_text(report_md)
    print(report_md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
