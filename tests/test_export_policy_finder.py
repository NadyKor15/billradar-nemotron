"""The static copy of the site carries /policy-finder (the recorded live run of policy_finder)."""

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_static.py"


def test_policy_finder_is_a_start_page_of_the_export():
    tree = ast.parse(SCRIPT.read_text())
    start = next(ast.literal_eval(n.value) for n in tree.body
                 if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "START" for t in n.targets))
    assert "/policy-finder" in start


def test_the_export_writes_the_policy_finder_page(tmp_path):
    out = tmp_path / "site"
    out.mkdir()
    code = (
        "import re, sys, json\n"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
        "import export_static as e\n"
        "e.START = ['/policy-finder']\n"
        "e.LINK = re.compile(r'(?!x)x')  # this page alone, not the whole site\n"
        f"seen = e.export(__import__('pathlib').Path({str(out)!r}))\n"
        "print(json.dumps(seen))\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    seen = json.loads(r.stdout.strip().splitlines()[-1])
    assert seen == {"/policy-finder": "/policy-finder.html"}
    html = (out / "policy-finder.html").read_text()
    assert "Find your hospital's policy" in html and "Recorded live run" in html
    assert "http://testserver" not in html
