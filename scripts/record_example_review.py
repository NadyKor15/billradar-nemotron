"""Record what the example is missing: replay the recorded Nemotron run of the example pack, and send live only the
requests that have no recording (usually the Ultra second read, after a letter or finding changed).

  NEBIUS_API_KEY=... BILLRADAR_SECOND_READ=1 python scripts/record_example_review.py example
  ... example-private

Live calls are capped by NEBIUS_SPEND_CAP_USD (default 5) like any other; the pack is fictional."""
import json, os, sys
from datetime import date
from billradar import pipeline, example
from billradar.case import Profile
from billradar import nemotron_client as nc

class Fill(nc.NemotronClient):
    def respond(self, body, label=""):
        saved = self.transport
        self.transport = nc.ReplayTransport()
        try:
            return nc.NemotronClient.respond(self, body, label)
        except nc.ReplayMissing:
            print("LIVE:", label)
        finally:
            self.transport = saved
        return nc.NemotronClient.respond(self, body, label)

pack = example.PACK
info = json.loads((pack / "profile.json").read_text())
c = Fill(transport=nc.LiveTransport(os.environ["NEBIUS_API_KEY"]))
photos = sorted(p for p in pack.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
run = pipeline.run(c, [p.read_bytes() for p in photos], [(p.name, p.read_bytes()) for p in sorted(pack.glob("*.pdf"))],
                   Profile.model_validate(info["profile"]), info["policy_id"], info.get("letterhead", ""),
                   date.fromisoformat(info["today"]), sys.argv[1], fictional=True, private=True)
print("findings", len(run.report.findings), "calls", len(c.calls))
print("verdict issues", [i.what[:90] for i in run.verdict.issues][:6])
