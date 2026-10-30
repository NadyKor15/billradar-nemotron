"""Nemotron 3 Super, acting for a family, uses BillRadar through its MCP tools.

python scripts/agent_demo.py [sample-pack] [--url https://<host>/mcp]

Without --url BillRadar's MCP server runs in this process. Needs MODEL_API_KEY and BILLRADAR_LIVE=1 for a
live model; otherwise the model answers must be recorded already. Only fictional sample packs.
"""

from __future__ import annotations

import asyncio
import sys

from billradar import family_agent
from billradar.mcp_server import build_server
from billradar.nemotron_client import NemotronClient


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pack = args[0] if args else "brs1-birth-all-errors"
    url = sys.argv[sys.argv.index("--url") + 1] if "--url" in sys.argv else None
    task = (f"Please check our bills with BillRadar. Use the fictional sample pack {pack!r} "
            "(analyze_case with fictional=true).")
    r = asyncio.run(family_agent.run(NemotronClient(), url or build_server(), task))
    for c in r.calls:
        print(f"tool {c.name} {'ERROR' if c.is_error else 'ok'}: {c.summary}")
    print(r.stopped or r.answer)
    return 0 if not r.stopped else 1


if __name__ == "__main__":
    raise SystemExit(main())
