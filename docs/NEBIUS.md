# BillRadar on NVIDIA Nemotron (Nebius Token Factory)

BillRadar runs every model step on NVIDIA Nemotron through Nebius Token Factory. Around the models: the checks in code, the quoted hospital policies, the letters and forms.

```bash
NEBIUS_API_KEY=...             # tokenfactory.nebius.com -> API keys; never commit it
BILLRADAR_LIVE=1               # without it, recorded answers are replayed (no key, no network)
NEBIUS_SPEND_CAP_USD=5         # hard stop for live spend, counted from token usage
```

## Which model does what

| Step | Model on Token Factory | What it gets | What it returns |
|---|---|---|---|
| Find the sheets on a photo of the table | `nvidia/Nemotron-3.5-Super-VL`, no reasoning | a smaller copy of the photo | one box per sheet (strict JSON) |
| Corners and straightening | none (OpenCV on the device) | the full-size photo and the boxes | each sheet cut out and straightened |
| Read every sheet and digital file | `nvidia/Nemotron-3.5-Super-VL`, reasoning on | up to 8 sheets per request, PDF text layers | every printed field and line, copied as printed (strict JSON) |
| Count money and dates | none (plain code) | the reading | findings with amounts, deadlines and quoted rules |
| Check before anyone sees it | none (plain code, `verifier.verify`) | the case, the findings, the letters | a finding whose numbers, dates or quotes don't verify is held back |
| Second read of the case (`BILLRADAR_SECOND_READ=1`; the demo runs with it) | `nvidia/Nemotron-3-Ultra-550b-a55b` | the case, the findings with their quotes, the letters | confirmed / problem / cannot tell for each one; a doubt lowers the finding's confidence (it can move to "worth asking"), while holding back stays with the code check |

Text steps by size (measured with `scripts/eval_routing.py` on the three fictional work packs, 2026-10-27, live):

| Text step | Nemotron 3.5 Lightning ($0.06 / $0.24) | Nemotron 3 Super ($0.30 / $0.90) | Chosen |
|---|---|---|---|
| Questions about the case, answered only with a verified citation | 1 of 10 right, 9 refused (citations it gave could not be found on the papers), $0.009 | 7 of 10 right (an earlier run, rescored with the same checker on its saved answers: at least 5 of 10), $0.050 | Super |
| Letters into Russian and Spanish, every number and quote kept | 53 of 56 parts, $0.0014, 1.9 s | 54 of 56 parts, $0.0077, 3.2 s | Lightning |

Second read of the case (measured with `scripts/eval_review.py` on the live private-mode readings of the same three packs, 17 findings, 8 of them wrong by the pack keys):

| Model | Wrong findings it doubts | Right findings it doubts | Cost for 3 cases |
|---|---|---|---|
| Nemotron 3.5 Lightning | — (2 of 3 cases ran out of tokens) | — | $0.012 |
| Nemotron 3 Super | 0 of 8 | 1 of 9 | $0.030 |
| Nemotron 3 Ultra | 6 of 8 | 4 of 9 | $0.137 |

Super agrees with almost everything, so a second read on it adds nothing. Ultra is the only one that separates wrong findings from right ones, though it also doubts 4 of 9 right ones, which then show as "worth asking" without a sum. So the second read runs on Ultra, the track's "serious reasoning" model, and a doubt never hides a finding.

Ten questions is a small sample and two runs of the same model differed, so the question step is a choice,
not a precise score. Lightning runs without reasoning (`reasoning_effort: none`): with reasoning it spent the
whole 3,000-token answer budget on thinking and returned nothing.

Prices on 2026-10-27 (`tokenfactory.nebius.com/api/public/models_info`), per 1M input / output tokens: Super VL and Super $0.30 / $0.90, Lightning $0.06 / $0.24, Ultra $1.00 / $3.00.

## Proof that Nemotron ran

Every request is written to `runs/requests.jsonl` with:
- the model we asked for and the model id Token Factory answered with;
- its response id and `x-request-id`;
- token usage, including reasoning tokens;
- the cost.

Images are never written there. `python scripts/nemotron_proof.py --live-only` prints the table. Recorded answers in `tests/fixtures/recordings-nebius/` replay the same runs without a key.

## Measurement

`python scripts/eval_run.py work` runs the three paths: simple prompt, strong prompt and BillRadar. All three use the same model and the same fictional sample packs.

## What we learned about the platform

- Nemotron 3.5 Super VL takes at most 8 images in one request (HTTP 400 "At most 8 image(s) may be provided in one prompt"). BillRadar reads in groups of 8 and joins the documents before any check. The simple and strong prompts in the measurement are asked the same way.
- On Super VL, `reasoning_effort` `low` reasons in full (13–24K reasoning tokens on one read); only `none` turns it off. Reasoning and answer share `max_tokens`, so reading gets a 64K budget.
- Strict `json_schema` works on Super VL and Super, though `/v1/models?verbose=true` lists only `tools` and `reasoning` for them.
- Token Factory has no segmentation and no speech model, so sheet finding is split between Super VL (boxes) and local code (corners), and a voice note after a call is typed in Nemotron mode.
- There is no spend limit on the platform, so the app keeps its own: each live call reserves its worst case before it is sent.

## Limitations

- Only fictional SAMPLE data. No real bills have been sent to any model.
- Private mode (names covered on the device) does not send digital PDFs yet.
- Voice transcription is not available on Nemotron here.
