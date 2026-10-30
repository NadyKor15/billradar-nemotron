"""The call log of the example case: every Nemotron request of its recorded live run, one JSON line each.

python scripts/example_run_log.py > docs/example-run-log.jsonl

The example replays tests/fixtures/recordings-nebius; this prints, for each call of that run, the step, the model
asked for and the model Token Factory answered with, its response id and x-request-id (both can be matched against
the Token Factory console), the tokens and the cost at list price, and the recording that holds the answer.
No request text, no images and no keys: only what the recording and the journal already say.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from billradar import example, pipeline, receipt  # noqa: E402
from billradar import nemotron_client as nc  # noqa: E402


def main() -> int:
    pack = example.PACK
    info = json.loads((pack / "profile.json").read_text())
    photos = sorted(p for p in pack.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    client = nc.NemotronClient(transport=nc.ReplayTransport(), record_to=None, journal=None)
    os.environ[pipeline.SECOND_READ] = "1"  # the run was recorded with the second read on
    example._run(client, pack, info, photos, example.EXAMPLE_ID, private=True)
    for c in client.calls:
        u = c["usage"]
        print(json.dumps({
            "step": receipt.STEP.get(c["label"], c["label"]), "label": c["label"],
            "model": c["model"], "answered_by": c["answered_by"],
            "response_id": c["response_id"], "request_id": c["request_id"],
            "input_tokens": u["input_tokens"], "output_tokens": u["output_tokens"],
            "reasoning_tokens": u["output_tokens_details"]["reasoning_tokens"], "usd": c["usd"],
            "recording": f"tests/fixtures/recordings-nebius/{c['model'].split('/')[-1]}-{c['key']}.json",
        }))
    print(f"{len(client.calls)} calls, ${sum(c['usd'] for c in client.calls):.3f} at list price", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
