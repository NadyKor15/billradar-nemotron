# Measurement

Question: on a mixed pile of hospital bills and insurance statements, does BillRadar find more of what a family is owed than the same model asked directly?

Three paths, same model (Nemotron 3.5 Super VL on Nebius Token Factory), same sheets:

| Path | What the model gets |
|---|---|
| Simple prompt | The sheets and one question: "Here are my hospital bills and insurance letters. Is anything wrong? Can I pay less?" |
| Strong prompt | The sheets, the hospital's full policy, the federal application-period rule, the household numbers, our full list of checks and our output schema. No code. |
| BillRadar | The pipeline: Super VL finds and reads the sheets, code checks against quoted policy rules, a separate checker re-computes everything. |

The simple path answers in free text, so a judge model maps the answer to the key; its raw answer is saved next to the score.

## Packs

`eval/packs/work/` and `eval/packs/holdout/`. Each pack: photo(s) of fictional sheets on a table (every sheet marked SAMPLE), digital files, `profile.json` (with `"fictional": true`), and `key.json`: what is really in the pack, by class (E1, E2, E3, E4, B1, P1, F1, F2, F3), with sheets, amounts and dates.

- **Work packs** are for building and tuning.
- **Holdout packs** (about a third) and their keys are written by someone who has not seen the prompts or the code. Their key hashes are committed to `SEALED.txt` before the first run (`python scripts/eval_run.py seal holdout`); the run refuses any key whose hash no longer matches. Only holdout numbers go into the final table.

## Scored

Per class: found (k of n), false findings, wrong amounts (off by more than $1), found but with no amount, wrong deadlines. Plus, for the simple path, money claims the judge could not match to the key.

If the strong prompt catches up with BillRadar, we publish that.

## Results

`eval/results/<UTC time>/`. A run without live model calls is labelled "REPLAYED (not a measurement)".
