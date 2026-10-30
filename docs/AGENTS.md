# BillRadar for AI agents

Any AI agent can use BillRadar the way a family does: send a pack of hospital bills and insurance statements, get back the money that may come off, every finding with the exact quote and page it rests on, the patient protections that apply, letters ready to sign, a call cheat sheet, the deadlines and a PDF report.

Two ways in, the same tools behind both:

| | Protocol | Where |
|---|---|---|
| MCP | Model Context Protocol, specification 2026-07-28, Streamable HTTP (stateless) | `POST /mcp` |
| MCP, on your own machine | stdio | `python -m billradar.mcp_server` |
| A2A | Agent2Agent protocol 1.0, JSON-RPC | card: `GET /.well-known/agent-card.json`, messages: `POST /a2a` (`SendMessage`) |

## Try it without a key

The fictional sample packs are open: no key, a limit of 30 requests a minute per address. They run without a live model: the reading of each sheet comes from the pack's generator, and every answer says `"replayed": true`.

MCP client config (any desktop or IDE client that speaks Streamable HTTP):

```json
{"mcpServers": {"billradar": {"url": "https://<host>/mcp"}}}
```

A2A, one call:

```bash
curl -s https://<host>/a2a -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' -d '{
  "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
  "params": {"message": {"role": "ROLE_USER", "messageId": "m1", "parts": [
    {"data": {"skill": "analyze_case", "args": {"sample_pack": "brs1-birth-all-errors", "fictional": true}}}]}}}'
```

## Tools (MCP) and skills (A2A)

| Name | What it returns |
|---|---|
| `list_sample_packs` | The fictional packs you can run now |
| `analyze_case` | A `case_id`, the total that may come off, the last day to apply. Takes `sample_pack`, or your own fictional papers (`photos`, `files`) |
| `get_findings` | Every finding: sheets and lines, amount, quotes with title and page; "worth asking" items without a sum; what was checked and holds no money |
| `get_protections` | The 13 federal and Florida patient protections, whether each applies, quotes, deadlines |
| `list_letters`, `draft_letter` | Letters in English (the version the family signs); `lang` `ru` or `es` adds a translation to read |
| `get_call_sheet` | What to say to the billing office or the insurer, numbers to have ready, answers with quotes |
| `get_deadlines` | Every deadline, earliest first, with its source |
| `get_report_pdf` | The case as a PDF (base64), `en`, `ru` or `es` |

On A2A, send one data part `{"skill": <name>, "args": {...}}`; the answer is one data part, or a text part starting with `Refused:` that says why.

## What BillRadar will not do, for agents as for people

- **Fictional papers only for now.** `analyze_case` without `fictional: true` is refused. Real bills need covering of names and numbers on the device first, a test of it, and the owner's yes.
- **It never sends anything.** No tool sends a letter, submits a form, calls anyone or pays. The family reviews and signs every letter.
- **No medical or legal advice.** There are no free-text questions; every answer is a finding, a quote, a date or a letter built in code.
- **Your own papers** (photos and PDFs) need an API key in `X-API-Key` and a live model run. Both are off on the public demo; keys are issued privately, never published.

## For the people running it

- `billradar.agent_entry.app()` is one ASGI app; the web app mounts it at the root after its own routes, and must run its lifespan once (the MCP session manager starts once per app).
- `BILLRADAR_AGENT_KEYS` (comma-separated, at least 24 characters each, from the vault), `BILLRADAR_AGENT_LIVE=1` together with `BILLRADAR_LIVE=1` for live runs (the app refuses to start live without keys), `BILLRADAR_PUBLIC_URL`, `BILLRADAR_PUBLIC_HOSTS` (allowed Host and Origin), `BILLRADAR_AGENT_PER_MINUTE`, `BILLRADAR_TRUST_PROXY=1` behind a proxy that sets X-Forwarded-For.
- Pinned: `mcp==2.2.0` (includes the fix for GHSA-rwrf-2pqf-9j8j), `a2a-sdk[http-server]==1.1.2`.

## An agent that uses it

`python scripts/agent_demo.py brs1-birth-all-errors` runs Nemotron 3 Super as the family's agent: it gets BillRadar's MCP tools as they are listed, calls them itself and answers the family in a few lines, using only what the tools returned (`billradar/family_agent.py`). It needs a live model run.
