"""Run the three paths on every pack and write the table.

python scripts/eval_run.py work               # packs in eval/packs/work/
python scripts/eval_run.py holdout            # refuses any key whose hash is not in eval/SEALED.txt
python scripts/eval_run.py seal holdout       # append the holdout key hashes to eval/SEALED.txt (before any run)

A pack directory: table photo(s) *.jpg, digital files *.pdf, profile.json (with "fictional": true), key.json.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from billradar import evaluation as ev  # noqa: E402
from billradar import pipeline, providers, reader  # noqa: E402
from billradar.case import Profile  # noqa: E402
from billradar.nemotron_client import NemotronClient  # noqa: E402

PACKS = ROOT / "eval" / "packs"
LEDGER = ROOT / "eval" / "SEALED.txt"


def jpeg(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def run_pack(client: NemotronClient, pack: Path, out: Path) -> dict[str, ev.Score]:
    info = json.loads((pack / "profile.json").read_text())
    key = ev.Key.model_validate_json((pack / "key.json").read_text())
    photos = [p.read_bytes() for p in sorted(pack.glob("*")) if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    pdfs = [(p.name, p.read_bytes()) for p in sorted(pack.glob("*.pdf"))]
    profile = Profile.model_validate(info["profile"])
    out.mkdir(parents=True, exist_ok=True)

    # BillRadar: the whole pipeline
    run = pipeline.run(client, photos, pdfs, profile, info["policy_id"], info.get("letterhead", ""),
                       date.fromisoformat(info["today"]), pack.name, fictional=bool(info.get("fictional")))
    pipeline.save(run, out / "billradar")
    scores = {"BillRadar": ev.score(key, ev.from_report(run.report))}

    # the same sheets for both prompts: the sheets the pipeline cut, in the same order
    sheets = [(s.doc_id, jpeg(s.image)) for s in run.sheets]
    files = [(f"D{i}", n, b) for i, (n, b) in enumerate(pdfs, start=1)]

    # A model that takes fewer images per prompt (Nemotron 3.5 Super VL: 8) is asked once per group of sheets,
    # exactly as a person would have to; the answers are joined before judging.
    parts = reader.batches(sheets, files, getattr(client, "max_images", None))
    simple = [client.respond(ev.simple_body(s, f), label="eval:simple") for s, f in parts]
    simple_text = "\n\n".join(f"[answer {i} of {len(simple)}]\n{r.text}" if len(simple) > 1 else r.text
                               for i, r in enumerate(simple, start=1))
    (out / "simple.txt").write_text(simple_text)
    judged = client.respond(ev.judge_body(simple_text, key), label="eval:judge")
    (out / "judge.json").write_text(judged.text)
    preds, extra = ev.from_judge(judged.text, key)
    scores["Simple prompt"] = ev.score(key, preds, extra)

    household = {"size": profile.household_size, "yearly_income": str(profile.annual_income),
                 "savings": str(profile.liquid_assets) if profile.liquid_assets is not None else "",
                 "insured": profile.insured, "today": info["today"]}
    strong = [client.respond(ev.strong_body(s, f, info["policy_id"], household), label="eval:strong") for s, f in parts]
    strong_text = json.dumps({"findings": [x for r in strong for x in json.loads(r.text).get("findings", [])]})
    (out / "strong.json").write_text(strong_text)
    scores["Strong prompt"] = ev.score(key, ev.from_strong(strong_text))
    return {k: scores[k] for k in ("Simple prompt", "Strong prompt", "BillRadar")}


def main(argv: list[str]) -> int:
    if argv[:1] == ["seal"]:
        keys = sorted((PACKS / argv[1]).glob("*/key.json"))
        for line in ev.seal(keys, LEDGER):
            print(line)
        return 0
    split = argv[0] if argv else "work"
    packs = sorted(p for p in (PACKS / split).glob("*") if (p / "key.json").exists())
    if split == "holdout":
        unsealed = [p.name for p in packs if not ev.check_seal(p / "key.json", LEDGER)]
        if unsealed:
            print(f"refusing: keys not sealed or changed since sealing: {unsealed}")
            return 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    client = providers.make_client()
    stamp = f"{stamp}-{providers.name()}"
    total = {k: ev.Score() for k in ("Simple prompt", "Strong prompt", "BillRadar")}
    for pack in packs:
        for name, s in run_pack(client, pack, ROOT / "eval" / "results" / stamp / split / pack.name).items():
            total[name].add(s)
    table = ev.table(total)
    mode = "live" if client.live else "REPLAYED (not a measurement)"
    mode += ", models: " + ", ".join(f"{k} {v}" for k, v in providers.models(client).items())
    report = f"# {split}: {len(packs)} packs, {stamp}, {mode}\n\n{table}\n"
    (ROOT / "eval" / "results" / stamp / f"{split}.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
