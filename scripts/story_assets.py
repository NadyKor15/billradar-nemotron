"""What the home page story shows from the recorded Nemotron run of the example (fictional pack), never typed by hand:

  python scripts/story_assets.py

Replays the example run exactly as the app does (no key, no spend) and writes to billradar/web/static/story/:
  table.webp  the table photo with the most sheets, made smaller
  found.json  the boxes Nemotron 3.5 Super VL put around its sheets, and the recording they came from;
              the people and the first sheet in the request Nemotron 3 Ultra got for the second read, cut verbatim,
              and the name of that recording (its name is the hash of the request, so a different request
              would not have found it)."""
import io
import json
import sys
from pathlib import Path

from PIL import Image

from billradar import example, sheets_vl
from billradar import nemotron_client as nc

OUT = Path(__file__).resolve().parents[1] / "billradar" / "web" / "static" / "story"
EXCERPT_DOC = '"people": ['  # mother and baby with empty names, then the first sheet: only tokens left the device
EXCERPT_LEN = 470

found, asked = [], []
_find, _answer = sheets_vl.find_sheets, nc.ReplayTransport.answer_for


def spy_find(client, photo, *a, **k):
    res = _find(client, photo, *a, **k)
    found.append((photo, res))
    return res


def spy_answer(self, body, label=""):
    asked.append((label, self.path(body, label), body))
    return _answer(self, body, label)


sheets_vl.find_sheets, nc.ReplayTransport.answer_for = spy_find, spy_answer
example.run_recorded(example.PACK, example.EXAMPLE_ID)

photo, res = max(found, key=lambda f: len(f[1].sheets))
_, vl, vl_body = [a for a in asked if a[0] == "vl:sheets"][found.index((photo, res))]
img = Image.open(io.BytesIO(photo)).convert("RGB")
w, h = img.size
img.resize((round(900 * w / h), 900), Image.LANCZOS).save(OUT / "table.webp", "WEBP", quality=80, method=6)

label, ultra, body = next(a for a in asked if a[0] == "review")
if not ultra.exists():
    sys.exit(f"the second read is not recorded for this request ({ultra.name}): run scripts/record_example_review.py")
text = next(p["text"] for m in body["input"] if m["role"] == "user" for p in m["content"] if p.get("text"))
start = text.index(EXCERPT_DOC)
excerpt = text[start:start + EXCERPT_LEN]
excerpt = excerpt[:excerpt.rindex(", ")] + ", …"

(OUT / "found.json").write_text(json.dumps({
    "photo": {"width": w, "height": h},
    "boxes": [{"sheet": s.doc_id, "box": list(s.box)} for s in res.sheets],
    "find_model": nc.chat_body(vl_body, "vl:sheets")["model"],
    "find_recording": vl.name,
    "sent": {"model": nc.chat_body(body, label)["model"], "recording": ultra.name, "excerpt": excerpt},
}, indent=1) + "\n")
print(f"{len(res.sheets)} boxes on a {w}x{h} photo ({vl.name}); excerpt {len(excerpt)} chars from {ultra.name}")
