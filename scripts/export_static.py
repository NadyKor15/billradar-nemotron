"""Export the example pages as a static site (the public demo on Cloudflare Pages): no server, no key, no uploads.

  python scripts/export_static.py OUT_DIR

Runs the app in-process with BILLRADAR_STATIC=1 (upload and questions are replaced by a note), starts at the
public pages and follows every same-site link, image, script and data-src. Each answer is written under OUT_DIR:
an HTML page at <path>.html (Pages serves /how from how.html), a file with an extension as is, anything else
with the extension of its content type, and links in the pages are rewritten to that name. A link with a query
string gets its own file too. The script stops with an error on any answer that is not 200, so a broken page
never ships quietly.
"""

from __future__ import annotations

import os
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

os.environ["BILLRADAR_STATIC"] = "1"
os.environ.setdefault("BILLRADAR_PUBLIC", "1")

from fastapi.testclient import TestClient  # noqa: E402

from billradar import app as app_module  # noqa: E402

START = ["/", "/how", "/safe", "/agents", "/upload", "/example", "/cases", "/cases/example",
         "/cases/example-appeal", "/.well-known/agent-card.json", "/cases/example/summary.json",
         "/cases/example-appeal/summary.json", "/policy-finder", "/404-not-a-page"]
LINK = re.compile(r'''(?:href|src|srcset|data-src|action)=["'](/[^"'#\s]*)["']''')
EXT = {"text/html": ".html", "application/json": ".json", "text/plain": ".txt", "application/pdf": ".pdf",
       "text/calendar": ".ics", "image/jpeg": ".jpg", "image/png": ".png", "text/css": ".css",
       "image/svg+xml": ".svg", "image/webp": ".webp", "application/javascript": ".js", "text/javascript": ".js"}
SKIP = re.compile(r"^/(mcp|a2a)\b|/delete$|/note$|/ask$|/sent$|/reply$")  # POST-only


def file_for(url: str, ctype: str) -> str:
    path, query = (url.split("?", 1) + [""])[:2]
    if query:
        path = path.rstrip("/") + "--" + re.sub(r"[^A-Za-z0-9]+", "-", query).strip("-")
    if path in ("", "/"):
        return "/index.html"
    ext = EXT.get(ctype.split(";")[0].strip(), "")
    if ctype.startswith("text/html"):
        return path.rstrip("/") + ".html"
    return path if Path(path).suffix and not query else path + ext


def export(out: Path) -> dict[str, str]:
    client = TestClient(app_module.app)
    seen: dict[str, str] = {}
    pages: dict[str, bytes] = {}
    queue, errors = list(START), []
    while queue:
        url = queue.pop(0)
        if url in seen or SKIP.search(urlsplit(url).path):
            continue
        r = client.get(url, follow_redirects=False)
        if url == "/404-not-a-page":
            seen[url] = "/404.html"  # Pages shows 404.html for any unknown path
            pages["/404.html"] = r.content
            continue
        if r.status_code != 200:
            errors.append(f"{r.status_code} {url}")
            seen[url] = ""
            continue
        name = file_for(url, r.headers.get("content-type", ""))
        seen[url] = name
        pages[name] = r.content
        if r.headers.get("content-type", "").startswith(("text/html", "text/css")):
            queue += [u for u in LINK.findall(r.text) if u not in seen]
    if errors:
        raise SystemExit("not 200:\n" + "\n".join(errors))
    # files with a new name; a page keeps its path (Pages drops ".html") unless it had a query string
    rename = {u: (f[:-5] if f.endswith(".html") else f) for u, f in seen.items()
              if f and f != u and (not f.endswith(".html") or "?" in u)}
    for name, body in pages.items():
        if name.endswith((".html", ".css", ".json")):
            text = body.decode().replace("http://testserver", "http://127.0.0.1:8000")  # no server behind the copy
            if name.endswith("agent-card.json"):  # say so in the card itself, so an agent does not call a dead address
                card = json.loads(text)
                card["description"] = (card.get("description", "").rstrip() + " This is a read-only copy with no server: "
                                       "run the app (see documentationUrl) and the A2A endpoint answers at the url below.").strip()
                text = json.dumps(card, indent=2)
            for u, f in sorted(rename.items(), key=lambda x: -len(x[0])):
                text = re.sub(r'''(?<=["'])''' + re.escape(u) + r'''(?=["'])''', f, text)
            body = text.encode()
        p = out / name.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    (out / "_headers").write_text("/*\n  X-Robots-Tag: noindex\n/static/*\n  Cache-Control: public, max-age=0, must-revalidate\n"
                                  "/cases/*\n  Cache-Control: public, max-age=0, must-revalidate\n")  # a rebuilt example shows at once
    return seen


if __name__ == "__main__":
    out = Path(sys.argv[1])
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty")
    seen = export(out)
    print(f"{sum(1 for f in seen.values() if f)} files in {out}")
