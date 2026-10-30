"""Record the live run of policy_finder behind /policy-finder: real hospitals, public policies, no bill.

    infisical run ... -- env BILLRADAR_LIVE=1 TAVILY_CREDIT_CAP=60 NEBIUS_SPEND_CAP_USD=0.20 \
        python scripts/record_policy_finder.py

Needs TAVILY_API_KEY and NEBIUS_API_KEY. A request recorded before is answered from its recording, so a second
run after a code change pays only for the requests that changed. Sends Tavily only the hospital's name, city or ZIP, state and site
(billradar/policy_finder_run.json). Writes recordings to tests/fixtures/recordings-tavily/ and
tests/fixtures/recordings-nebius/, sets "recorded" in the run file, prints one line per hospital.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from billradar import nemotron_client as nc
from billradar import policy_finder as pf
from billradar import tavily_client as tc
from billradar.nemotron_client import NemotronClient
from billradar.tavily_client import TavilyClient

REUSED = {"tavily": 0, "nemotron": 0}


class ReuseTavily(tc.LiveTransport):
    """A request already recorded today is answered from its recording (no second charge); others go live."""

    def answer(self, endpoint, body):
        try:
            ans = tc.ReplayTransport().answer(endpoint, body)
            REUSED["tavily"] += 1
            return ans
        except tc.ReplayMissing:
            return super().answer(endpoint, body)


class ReuseNemotron(NemotronClient):
    def respond(self, body, label=""):
        if nc.ReplayTransport().path(body, label).exists():
            replay = NemotronClient(transport=nc.ReplayTransport(), record_to=None, journal=None)
            result = replay.respond(body, label)
            self.calls += replay.calls
            REUSED["nemotron"] += 1
            return result
        return super().respond(body, label)


def main() -> int:
    tavily, nemotron = TavilyClient(), ReuseNemotron()
    if tavily.live:
        tavily.transport = ReuseTavily(tavily.transport.api_key)
    if not (tavily.live and nemotron.live):
        print("not live: set BILLRADAR_LIVE=1, TAVILY_API_KEY and NEBIUS_API_KEY", file=sys.stderr)
        return 2
    plan = pf.run_plan()
    rows = []
    for item in plan["runs"]:
        hint = pf.Hint(**item["hint"])
        first = len(tavily.calls)
        found = pf.find(hint, tavily, nemotron, confirmed=item.get("confirmed", False))
        credits = sum(int(c.get("credits") or 0) for c in tavily.calls[first:])
        rows.append({"hospital": hint.name, "state": hint.state, "status": found.status, "domain": found.domain,
                     "step": found.domain_step, "url": found.url, "doc_date": found.doc_date, "tiers": len(found.tiers),
                     "dropped": len(found.dropped), "credits": credits, "usd": found.nemotron_usd,
                     "message": found.message, "date_note": found.date_note})
        print(json.dumps(rows[-1]))
    plan["recorded"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pf.RUN_FILE.write_text(json.dumps(plan, indent=1) + "\n")
    print(json.dumps({"hospitals": len({(r["hospital"], r["state"]) for r in rows}),
                      "states": len({r["state"] for r in rows}),
                      "credits": sum(r["credits"] for r in rows), "usd": round(sum(r["usd"] for r in rows), 6),
                      "tavily_request_ids": [c["request_id"] for c in tavily.calls], "reused": REUSED}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
