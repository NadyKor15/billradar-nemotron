"""The case receipt: which Nemotron did each step, how long it took, what it cost, and what the same case would
have cost if the biggest Nemotron had done every text step.

Built only from the client's own call log (NemotronClient.calls), never from estimates of steps that did not run.
Costs use Token Factory's per-model prices (nemotron_client.PRICES). Replayed calls cost nothing now, so the
receipt says what the recorded run cost when it was live.
"""

from __future__ import annotations

from billradar import nemotron_client as nc

STEP = {"vl:sheets": "Find sheets", "read": "Read every sheet", "review": "Second read of the case"}


def _cost(model: str, usage: dict) -> float:
    return nc.cost_of(model, usage) if model in nc.PRICES else 0.0


def build(calls: list[dict]) -> dict | None:
    """calls: NemotronClient.calls entries of one case, in order. None when there were no Nemotron calls."""
    if not calls:
        return None
    rows, biggest = [], 0.0
    for c in calls:
        u = c.get("usage") or {}
        usd = _cost(c["model"], u)
        sees_images = c["model"] in nc.SEES_IMAGES
        alt = usd if sees_images else _cost(nc.BIGGEST_MODEL, u)  # no larger vision model on Token Factory
        biggest += alt
        rows.append({
            "step": STEP.get(c.get("label", ""), c.get("label", "")),
            "model": c.get("answered_by") or c["model"],
            "seconds": c.get("seconds", 0.0),
            "input_tokens": u.get("input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0),
            "reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
            "usd": round(usd, 6),
            "live": bool(c.get("live")),
            "request_id": c.get("request_id", ""),
        })
    total = sum(r["usd"] for r in rows)
    return {
        "rows": rows,
        "total_usd": round(total, 6),
        "total_seconds": round(sum(r["seconds"] for r in rows), 2),
        "all_on_biggest_usd": round(biggest, 6),
        "biggest_model": nc.BIGGEST_MODEL,
        "note": ("All on the biggest: the same tokens priced at " + nc.BIGGEST_MODEL + " for every text step; photo "
                 "steps stay on Nemotron 3.5 Super VL, the only Nemotron on Token Factory that sees images."),
        "replayed": not any(r["live"] for r in rows),
    }
