# BillRadar on NVIDIA Nemotron

BillRadar is an AI agent that reads the whole pile of hospital bills and insurance statements and finds what a family shouldn't have to pay: billing errors and missed hospital financial assistance. Every model step runs on NVIDIA Nemotron through Nebius Token Factory. Each model was picked by a measurement: on the second read Nemotron 3 Ultra doubted 6 of 8 wrong findings (Super doubted 0 of 8), and letters translated on Nemotron 3.5 Lightning kept every number and quote in 53 of 56 parts versus 54 of 56 on Super, about 5.5 times cheaper.

**Demo:** https://billradar-nemotron.retailbox-automation.com/ is a read-only copy of the app. The example case replays recorded live Nemotron runs (October 27–29, 2026) on fictional bills, so it needs no key. Uploading papers is turned off on the public copy so that nobody's medical bills reach a public server. To check papers yourself, run the app from this repository (below).

All bills, statements and people in this repository are fictional. No real patient data has been sent to any model. BillRadar helps with bills and insurance statements; it does not give medical advice. You review and sign every letter, and only you send anything.

## How it works

1. Lay the paper sheets on a table and take one photo. Add PDFs from the hospital portal.
2. **On your device**, names, member IDs, addresses and account numbers are covered with tokens (`[PATIENT_1]`, `[ACCT_1]`) before anything is sent. Dates, codes and amounts stay readable.
3. **Nemotron 3.5 Super VL** finds every sheet on the photo. Code on the device finds the corners and straightens each sheet.
4. **Nemotron 3.5 Super VL** reads every sheet and file (up to 8 images per request) and links them: whose bill, which claim, which line. Amounts and dates are copied as printed.
5. **Code** does the math against the hospital's own public financial assistance policy and the federal rules. Every rule is tied to a quote from its source page, and the quote is checked against that page before a finding is shown.
6. **A separate code checker** recomputes every amount and date from the sheets and holds back what it can't confirm. Then **Nemotron 3 Ultra** reads the whole case again, read-only. A finding it doubts is not hidden; it moves to "worth asking about" without a dollar amount.
7. The case screen shows each finding with the sheet and line behind it, the letters to sign, the hospital's real form filled in, what the models were sent, and a receipt: which model ran each step and what it cost.

Other AI agents can call BillRadar as a tool over MCP and A2A (`/mcp`, `/a2a`, `/.well-known/agent-card.json`; see `/agents` in the app).

## Which model does what

| Step | Model on Token Factory | Why this size |
|---|---|---|
| Find the sheets on a photo | `nvidia/Nemotron-3.5-Super-VL`, no reasoning | the only Nemotron on Token Factory that sees images |
| Read every sheet and file | `nvidia/Nemotron-3.5-Super-VL`, reasoning on (64K budget) | same; strict JSON schema |
| Count money and dates | none (plain code) | a model never decides whether there is money |
| Second read of the case | `nvidia/Nemotron-3-Ultra-550b-a55b` | doubted 6 of 8 wrong findings; Super doubted 0 of 8 |
| Questions about the case, answered only with a quote found on a sheet | `nvidia/nemotron-3-super-120b-a12b` | 7 of 10 right; Lightning 1 of 10 |
| Letters into Spanish and Russian, every number and quote kept | `nvidia/Nemotron-3_5-Lightning`, no reasoning | 53 of 56 parts versus 54 of 56 on Super, about 5.5 times cheaper |

The model choices were measured on fictional bill packs with `scripts/eval_routing.py` and `scripts/eval_review.py` (October 27, 2026, live). Details, prices and what we learned about the platform: [docs/NEBIUS.md](docs/NEBIUS.md).

The example case's receipt: 15 model calls, $0.146 at list price (the recorded live runs it replays): 7 calls to find the sheets $0.002, 7 calls to read them $0.099 (3 batches, then 4 sheets read again on their own where the device saw a share the reading missed), the second read on Ultra $0.045.

## Where Tavily comes in

Every hospital has its own financial assistance policy, and without it you cannot tell a family whether a discount applies. Tavily finds the document on the hospital's own site only (search with include_domains and the page text in the answer; extract as the fallback), Nemotron reads it, code checks it. It works for hospitals in any state that post their policy. Tested on 5 real hospitals in 4 states: the policy was found for all five. One was confirmed in full with a revision date: Memorial Regional, 5 of 5 tiers, exactly as in our hand-checked base. For three, only some tiers could be confirmed or the page has no date, and BillRadar labels them so. One page has no tier table, so BillRadar shows the link. In the same run BillRadar set aside Form 990 tax returns and community needs reports, Korean, Russian and Spanish versions, a 2018 policy, and Wikipedia and directory pages instead of the hospital's site. 20 Tavily credits for the five.

If the hospital is not in BillRadar's base, it finds that hospital's own financial assistance policy with Tavily. It searches only the hospital's own site: the site printed on the bill, or, if the bill has none, a site found by name and city that shows the same name and city or ZIP. When in doubt it asks "Is this your hospital?". Nemotron 3 Super copies the income tiers and discounts, and code keeps a tier only if its quote is in the policy text and the quote itself prints the tier's percentages. Anything unconfirmed is shown as a link: "we found the policy, check it yourself". When the page has no date, BillRadar says it may be out of date.

`/policy-finder` in the app replays the recorded live run of October 29, 2026 for 5 real hospitals in 4 states. A live search needs `TAVILY_API_KEY`, `BILLRADAR_LIVE=1` and `BILLRADAR_POLICY_FINDER=1` (see `.env.example`); the step is off by default.

## Proof that Nemotron ran

Every request is written to `runs/requests.jsonl` with:
- the model asked for and the model id Token Factory answered with;
- the response id and `x-request-id`;
- token usage, including reasoning tokens;
- the cost.

Images are never written there. `python scripts/nemotron_proof.py --live-only` prints the table. The recorded answers in `tests/fixtures/recordings-nebius/` replay the same runs without a key.

`docs/example-run-log.jsonl` is that log for the example case: its 15 calls with the response id, `x-request-id`, tokens, cost and the recording that holds each answer, without request text or images (`python scripts/example_run_log.py` writes it again from the recordings). They are the same calls as the receipt on the example page; letter translations (Lightning) are not in it.

## What it finds

| Class | What |
|---|---|
| F1 | The household qualifies under the hospital's policy, but no discount is on the bill |
| F2 | The last day to apply, counted from the first bill after discharge (policy + federal rule) |
| F3 | The discount is on the mother's bill but not on the baby's, same stay |
| E1 | A bill line asks more than the insurance statement says the patient owes |
| E2 | The same service billed twice |
| E3 | A credit or overpayment left on the account |
| E4 | One sheet asks two different amounts |
| B1 | Newborn care billed under the mother's insurance ID |
| P1 | Pages missing from a statement |

It also writes honest "checked, no money here" notes, for example when a line matches the insurance statement or the income is above the policy's limit.

## Run it

```sh
uv venv --python 3.12 && uv pip install -e ".[dev]"
uv pip install --no-deps rapidocr-onnxruntime==1.4.4   # local OCR for covering private values; models ship inside, no network
python scripts/fetch_policies.py        # public policies and the blank form -> .cache/ (not committed)
pytest
uvicorn billradar.app:app                # http://127.0.0.1:8000/example (replayed, no key)
```

Live calls on Token Factory:

```sh
NEBIUS_API_KEY=...             # tokenfactory.nebius.com -> API keys; never commit it
BILLRADAR_LIVE=1               # without it, recorded answers are replayed and a missing recording is an error
NEBIUS_SPEND_CAP_USD=5         # hard stop for live spend: each call reserves its worst case before it is sent
BILLRADAR_SECOND_READ=1        # the second read on Nemotron 3 Ultra (the demo runs with it)
```

Live calls run only on packs marked fictional. Real papers stay off the models until on-device covering, its test and the owner's yes are all in place.

Command line: `billradar run <pack-dir>` runs the whole pipeline on a pack (photos, PDFs, `profile.json`). `python scripts/export_static.py <dir>` writes the read-only copy used for the demo.

## What it can't do yet

- Token Factory takes 8 images per request, so big piles are read in groups.
- Token Factory has no speech model, so a note after a call is typed.
- Filling an insurer's web portal by an agent is not in this version.
- Private mode sends PDF pages as covered images; reading them is less exact than the text layer.
- Covering on the device works from OCR and pixels: what neither can see on a sheet is not covered. Each sheet is checked again before it leaves; if a name, number, address line, mailing line or barcode can still be read, it is not sent.

## Sources in the repo

| Source | From |
|---|---|
| Memorial Healthcare System, Financial Assistance Policy, revised May 2026 | mhs.net (public PDF) |
| Memorial Financial Evaluation Form 2310-10040 | mhs.net (public PDF), filled only on the device |
| Baptist Health South Florida, BHSF-355.14, revised 09/29/2026 | baptisthealth.net (public PDF) |
| 26 CFR 1.501(r)-1, application period | eCFR |
| Florida Statutes 395.301 (itemized statement, billing disputes) | leg.state.fl.us |

We store short quotes and page numbers, not the documents. Estimates follow the published policy; the hospital decides.

The code was written with an AI coding agent.

## License

The code is under the [Apache License 2.0](LICENSE). The name BillRadar and its logo are not covered by it (see [NOTICE](NOTICE)).
