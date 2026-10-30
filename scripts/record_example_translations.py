"""Record the Russian and Spanish versions of every example letter, so the read-only copy can show them.

  NEBIUS_API_KEY=... python scripts/record_example_translations.py

Each translation is first looked up in the recordings; only a missing one is sent live (Nemotron 3.5 Lightning)
and recorded. Live spend is capped by NEBIUS_SPEND_CAP_USD like any other call. The letters are fictional."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402

from billradar import app as app_module, example, nemotron_client as nc, providers  # noqa: E402


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


live = nc.LiveTransport(os.environ["NEBIUS_API_KEY"])
providers.make_client = lambda: Fill(transport=live)
client = TestClient(app_module.app)
for case_id in (example.EXAMPLE_ID, example.APPEAL_ID):
    page = client.get(f"/cases/{case_id}").text
    kinds = sorted(set(__import__("re").findall(rf'/cases/{case_id}/letters/([a-z0-9-]+)"', page)))
    for kind in kinds:
        for lang in ("ru", "es"):
            r = client.get(f"/cases/{case_id}/letters/{kind}?lang={lang}")
            print(case_id, kind, lang, r.status_code, "not live" if "switched on" in r.text else "ok")
