"""NVIDIA Nemotron on Nebius Token Factory: respond(body, label) -> Result for every model step of BillRadar.

The modules that build requests (reader, verifier, ...) keep writing Responses-style bodies. This client
translates each body to Token Factory's OpenAI-compatible chat completions:
  model        "reader" -> Nemotron 3.5 Super VL (reads photos of sheets); a text-only "reader" body goes to the
                         text model chosen for its step (TEXT_ROUTE, else TEXT_MODEL)
               "checker" -> Nemotron 3 Ultra (the second read of a case)
  input_image  -> image_url part (Super VL only; a text model refuses images instead of dropping them)
  input_file   -> the PDF's text layer, extracted here; a PDF without one is an error, never skipped
  json_schema  -> response_format json_schema (strict); the answer is checked against the schema here
  reasoning    -> reasoning_effort

Two transports:
  live    POST https://api.tokenfactory.nebius.com/v1/chat/completions; only when NEBIUS_API_KEY is set
          AND BILLRADAR_LIVE=1. Every live answer is recorded to disk.
  replay  answers from recorded files. A request we have no recording for is an error.

Spend: Token Factory has no spend limit of its own. Before each live call its worst-case cost is reserved
in runs/nebius-spend.jsonl and the call is refused if that passes NEBIUS_SPEND_CAP_USD (default 5).
Journal: every request goes to runs/requests.jsonl with provider, endpoint, the model id Token Factory
answered with, its response id, x-request-id and token usage, which is how the logs prove Nemotron ran.
"""

from __future__ import annotations

import base64
import fcntl
import io
import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from pypdf import PdfReader

from billradar.model_api import ROOT, ModelError, Result, request_key
from billradar.model_api import ReplayMissing as _ReplayMissing

BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
RECORDINGS = ROOT / "tests" / "fixtures" / "recordings-nebius"
JOURNAL = ROOT / "runs" / "requests.jsonl"
SPEND = ROOT / "runs" / "nebius-spend.jsonl"

# One Nemotron per role, each by size: the only vision model reads photos, the fast small model takes everyday text,
# the second read of a case gets the big reasoning model. Every role is an env variable; there is no silent fallback.
READER_MODEL = os.environ.get("NEMOTRON_READER_MODEL", "nvidia/Nemotron-3.5-Super-VL")
TEXT_MODEL = os.environ.get("NEMOTRON_TEXT_MODEL", "nvidia/Nemotron-3_5-Lightning")
# Second read of the case on Ultra (scripts/eval_review.py, 2026-10-27, live, 17 findings of 3 fictional packs):
# Ultra doubted 6 of 8 wrong findings and 4 of 9 right ones; Super doubted 0 of 8 and 1 of 9 (agrees with almost
# everything); Lightning ran out of tokens on 2 of 3 cases.
CHECKER_MODEL = os.environ.get("NEMOTRON_CHECKER_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")
QA_MODEL = os.environ.get("NEMOTRON_ASK_MODEL", "nvidia/nemotron-3-super-120b-a12b")
BIGGEST_MODEL = "nvidia/Nemotron-3-Ultra-550b-a55b"
# Text steps by label, where a measurement chose a model other than TEXT_MODEL (scripts/eval_routing.py, 2026-10-27:
# questions about the case — Lightning 0 of 10 with a verified citation, Super 4 of 10).
# The family agent (MCP client over BillRadar's own tools) chooses and chains tool calls: Super handled tool calls
# in our spike of 2026-10-14 (2 of 2 facts), Lightning did not (1 of 2).
AGENT_MODEL = os.environ.get("NEMOTRON_AGENT_MODEL", "nvidia/nemotron-3-super-120b-a12b")
TEXT_ROUTE = {"ask": QA_MODEL, "family-agent": AGENT_MODEL}  # the receipt compares each case with this one doing every text step
READER, CHECKER = "reader", "checker"  # the role a request body names; the model behind it is chosen here
MODEL_FOR = {READER: READER_MODEL, CHECKER: CHECKER_MODEL}

# USD per 1M tokens (input, output), tokenfactory.nebius.com/api/public/models_info, 2026-10-27 11:03Z.
PRICES = {
    "nvidia/Nemotron-3.5-Super-VL": (0.30, 0.90),
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 3.00),
    "nvidia/Nemotron-3_5-Lightning": (0.06, 0.24),
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
}
SEES_IMAGES = {"nvidia/Nemotron-3.5-Super-VL"}
# NVIDIA model cards: Super VL 0.6 / 0.95 on images, the text models 1.0 / 0.95.
SAMPLING = {"nvidia/Nemotron-3.5-Super-VL": (0.6, 0.95)}
# On Super VL only "none" changes anything: every other level thinks in full (third-party probe 2026-10-15, and
# our first live reads 2026-10-27: effort "low" spent 13-24K reasoning tokens and ran out of a 32K budget).
# Same pack, thinking with a 64K budget vs "none": 5 of 7 error classes found vs 3 of 7, 166 vs 219 wrong fields,
# 4 min vs 1.5 min. So Super VL keeps thinking and gets the bigger budget; NEMOTRON_VL_LIGHT_EFFORT=none turns it off.
LIGHT = {"minimal", "low"}
VL_LIGHT_EFFORT = os.environ.get("NEMOTRON_VL_LIGHT_EFFORT", "low")
VL_MIN_MAX_TOKENS = 65536
# Lightning, like Super VL, thinks in full at every level but "none" (third-party probe 2026-08-29; our ask canary
# 2026-10-27: both answers cut at 3,000 tokens of reasoning). Everyday text on the fast model runs without reasoning
# unless NEMOTRON_TEXT_EFFORT says otherwise.
TEXT_EFFORT = os.environ.get("NEMOTRON_TEXT_EFFORT", "none")
RETRY_STATUS = {429, 500, 502, 503, 529}
IMAGE_TOKENS_GUESS = 3000  # per image, for the spend reservation only


class NemotronError(ModelError):
    pass


class ReplayMissing(NemotronError, _ReplayMissing):  # callers that catch model_api.ReplayMissing catch this too
    pass


class NotOnNemotron(NemotronError):
    """A step that has no model on Token Factory (voice transcription, computer use). The screen says so."""


class SpendCapReached(RuntimeError):
    """Not a NemotronError on purpose: it must stop the run, not be handled as one failed call."""


def nemotron_model(model: str) -> str:
    if model in PRICES:
        return model
    if model in MODEL_FOR:
        return MODEL_FOR[model]
    raise NemotronError(f"no Nemotron model for {model!r} (known: {sorted(MODEL_FOR)} and {sorted(PRICES)})")


# --- body translation ---------------------------------------------------------------------------

def pdf_text(data_url_: str, filename: str) -> str:
    raw = base64.b64decode(data_url_.split(",", 1)[1])
    pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(raw)).pages]
    if not "".join(pages).strip():
        raise NemotronError(f"{filename}: the PDF has no text layer; scans go in as photos")
    # No page numbers of our own: the model should copy only what the file prints ("Page 1 of 4" is on every page
    # of a complete file, and a made-up "[page 1 of 4]" label read as a missing-page hint in the first live runs).
    return f"(complete digital file, all {len(pages)} page(s) follow)\n" + "\n\n--- next page ---\n\n".join(
        t.strip() for t in pages)


def _part(part: dict, model: str) -> dict:
    t = part.get("type")
    if t in ("input_text", "text"):
        return {"type": "text", "text": part["text"]}
    if t == "input_image":
        if model not in SEES_IMAGES:
            raise NemotronError(f"{model} reads text only; an image was sent to it")
        return {"type": "image_url", "image_url": {"url": part["image_url"]}}
    if t == "input_file":
        name = part.get("filename", "file.pdf")
        return {"type": "text", "text": f"Text layer of {name}:\n{pdf_text(part['file_data'], name)}"}
    raise NemotronError(f"content part {t!r} is not supported on Token Factory")


HANDLED = {"model", "input", "instructions", "store", "stream", "reasoning", "max_output_tokens", "text", "metadata"}


def _has_images(body: dict) -> bool:
    return any(isinstance(item.get("content"), list) and any(p.get("type") == "input_image" for p in item["content"])
               for item in body.get("input", []))


def chat_body(body: dict, label: str = "") -> dict:
    """Responses-style body -> Token Factory chat completions body.
    Anything we do not translate is refused, never dropped."""
    other = sorted({t.get("type", "?") for t in body.get("tools") or [] if t.get("type") != "function"})
    if other:
        raise NotOnNemotron(f"tools {other} are not available through Token Factory here (computer use is not in this version)")
    unknown = sorted(set(body) - HANDLED - {"tools"})
    if unknown:
        raise NemotronError(f"request fields {unknown} are not translated for Token Factory")
    model = nemotron_model(body["model"])
    if body["model"] == READER and not _has_images(body):
        model = TEXT_ROUTE.get(label, TEXT_MODEL)  # the reader's text-only jobs go to the text model chosen for that step
    messages = [{"role": "system", "content": body["instructions"]}] if body.get("instructions") else []
    for item in body.get("input", []):
        if item.get("type") == "function_call":  # an earlier turn's tool call, as the Responses API returned it
            call = {"id": item["call_id"], "type": "function",
                    "function": {"name": item["name"], "arguments": item.get("arguments") or "{}"}}
            if messages and messages[-1].get("role") == "assistant" and messages[-1].get("tool_calls"):
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
            continue
        if item.get("type") == "function_call_output":
            messages.append({"role": "tool", "tool_call_id": item["call_id"], "content": str(item.get("output", ""))})
            continue
        role = {"developer": "system"}.get(item.get("role"), item.get("role", "user"))
        content = item.get("content")
        if isinstance(content, str):
            parts = [{"type": "text", "text": content}]
        else:
            parts = [_part(p, model) for p in content or []]
        if all(p["type"] == "text" for p in parts):  # plain string: every server takes it
            messages.append({"role": role, "content": "\n\n".join(p["text"] for p in parts)})
        else:
            messages.append({"role": role, "content": parts})
    temperature, top_p = SAMPLING.get(model, (1.0, 0.95))
    floor = int(os.environ.get("NEMOTRON_MIN_MAX_TOKENS", VL_MIN_MAX_TOKENS if model in SEES_IMAGES else 0))
    max_tokens = max(body.get("max_output_tokens", 8192), floor)
    out = {"model": model, "messages": messages, "stream": False,
           "max_tokens": max_tokens, "temperature": temperature, "top_p": top_p}
    effort = (body.get("reasoning") or {}).get("effort")
    if model == TEXT_MODEL and model != CHECKER_MODEL and model not in SEES_IMAGES and (not effort or effort in LIGHT):
        out["reasoning_effort"] = TEXT_EFFORT
    elif effort:
        out["reasoning_effort"] = VL_LIGHT_EFFORT if model in SEES_IMAGES and effort in LIGHT else effort
    fmt = (body.get("text") or {}).get("format") or {}
    if fmt.get("type") == "json_schema":
        out["response_format"] = {"type": "json_schema", "json_schema": {
            "name": fmt["name"], "schema": fmt["schema"], "strict": bool(fmt.get("strict"))}}
    if body.get("tools"):
        out["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                          "parameters": t.get("parameters") or {"type": "object"}}}
                        for t in body["tools"]]
        out["tool_choice"] = "auto"
    return out


def responses_events(raw: dict) -> list[dict]:
    """The chat answer as the Responses API would end it: function_call items for tool calls, then the text."""
    msg = ((raw.get("choices") or [{}])[0].get("message")) or {}
    output = [{"type": "function_call", "call_id": tc.get("id", ""), "name": (tc.get("function") or {}).get("name", ""),
               "arguments": (tc.get("function") or {}).get("arguments") or "{}"} for tc in msg.get("tool_calls") or []]
    if msg.get("content"):
        output.append({"type": "message", "role": "assistant",
                       "content": [{"type": "output_text", "text": msg["content"]}]})
    return [raw, {"type": "response.completed", "response": {"id": raw.get("id", ""), "output": output}}]


# --- answer checks ------------------------------------------------------------------------------

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}


def schema_errors(value, schema: dict, where: str = "$") -> list[str]:
    """The subset of JSON Schema our schemas use: type, required, properties, items, enum."""
    t = schema.get("type")
    if t and not isinstance(value, _TYPES[t]) or (t in ("integer", "number") and isinstance(value, bool)):
        return [f"{where}: expected {t}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where}: {value!r} is not one of {schema['enum']}"]
    errors = []
    if t == "object":
        errors += [f"{where}: missing {k}" for k in schema.get("required", []) if k not in value]
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errors += schema_errors(value[k], sub, f"{where}.{k}")
    if t == "array" and "items" in schema:
        for i, v in enumerate(value):
            errors += schema_errors(v, schema["items"], f"{where}[{i}]")
    return errors


def answer_text(raw: dict, body: dict) -> str:
    """The text of one chat completion; refuses cut, empty or off-schema answers instead of passing them on."""
    choices = raw.get("choices") or []
    if not choices:
        raise NemotronError(f"answer without choices: {json.dumps(raw)[:300]}")
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        raise NemotronError("answer cut at max_tokens (reasoning and answer share the budget); raise max_output_tokens")
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    if not text.strip() and msg.get("tool_calls") and body.get("tools"):
        return ""  # a turn that only calls tools: the caller reads the calls from the events
    if not text.strip():
        raise NemotronError("empty answer" + (" (only reasoning came back)" if msg.get("reasoning") else ""))
    fmt = (body.get("text") or {}).get("format") or {}
    if fmt.get("type") == "json_schema":
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise NemotronError(f"answer is not JSON ({exc}); first 200 chars: {text[:200]!r}") from exc
        errors = schema_errors(value, fmt["schema"])
        if errors:
            raise NemotronError(f"answer does not fit {fmt['name']}: " + "; ".join(errors[:5]))
    return text


def usage_of(raw: dict) -> dict:
    """Token Factory usage in the Responses names the rest of BillRadar reads, plus what we need for proof."""
    u = raw.get("usage") or {}
    return {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
            "output_tokens_details": {"reasoning_tokens":
                                      (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)}}


def cost_of(model: str, usage: dict) -> float:
    p_in, p_out = PRICES[model]
    return (usage["input_tokens"] * p_in + usage["output_tokens"] * p_out) / 1e6


# --- spend cap ----------------------------------------------------------------------------------

class SpendLedger:
    """Append-only reservations and settlements, flock-guarded so parallel runs share one cap."""

    def __init__(self, path: Path = SPEND, cap: float | None = None):
        self.path = path
        self.cap = cap if cap is not None else float(os.environ.get("NEBIUS_SPEND_CAP_USD", "5"))

    def _append(self, usd: float, note: str, check: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                if check:
                    spent = self._total(f)
                    if spent + usd > self.cap:
                        raise SpendCapReached(f"${usd:.4f} more would pass the ${self.cap:.2f} cap "
                                              f"(${spent:.4f} spent or reserved); raise NEBIUS_SPEND_CAP_USD on purpose")
                f.seek(0, os.SEEK_END)
                f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "usd": round(usd, 6), "note": note}) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    @staticmethod
    def _total(f) -> float:
        f.seek(0)
        total = 0.0
        for line in f:
            if line.strip():
                total += float(json.loads(line)["usd"])
        return total

    def spent(self) -> float:
        if not self.path.exists():
            return 0.0
        with self.path.open() as f:
            return self._total(f)

    def reserve(self, usd: float, note: str) -> None:
        self._append(usd, note, check=True)

    def settle(self, usd: float, note: str) -> None:
        self._append(usd, note, check=False)


def worst_case(chat: dict) -> float:
    p_in, p_out = PRICES[chat["model"]]
    chars = images = 0
    for m in chat["messages"]:
        c = m["content"]
        for p in ([{"type": "text", "text": c}] if isinstance(c, str) else c):
            chars += len(p.get("text", ""))
            images += p["type"] == "image_url"
    tokens_in = chars / 3 + images * IMAGE_TOKENS_GUESS
    return (tokens_in * p_in + chat["max_tokens"] * p_out) / 1e6


# --- transports ---------------------------------------------------------------------------------

@dataclass
class Answer:
    raw: dict
    request_id: str = ""


class LiveTransport:
    def __init__(self, api_key: str, ledger: SpendLedger | None = None, attempts: int = 4, timeout: float = 300.0):
        self.api_key = api_key
        self.ledger = ledger or SpendLedger()
        self.attempts = attempts
        self.timeout = timeout

    def answer(self, chat: dict) -> Answer:
        reserve = worst_case(chat)
        delay = 1.0
        for attempt in range(1, self.attempts + 1):
            self.ledger.reserve(reserve, f"reserve {chat['model']}")
            try:
                r = requests.post(f"{BASE_URL}/chat/completions", json=chat, timeout=self.timeout, headers={
                    "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            except requests.RequestException as exc:  # the request may still have been billed: keep the reservation
                if attempt < self.attempts:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise NemotronError(f"Token Factory unreachable: {exc}") from exc
            if r.status_code != 200:
                self.ledger.settle(-reserve, f"release {chat['model']} HTTP {r.status_code}")  # errors are not billed
                if r.status_code in RETRY_STATUS and attempt < self.attempts:
                    time.sleep(float(r.headers.get("Retry-After") or delay) + random.uniform(0, 0.25))
                    delay *= 2
                    continue
                raise NemotronError(f"HTTP {r.status_code}: {r.text[:500]}")
            raw = r.json()
            u = raw.get("usage")
            if u:
                self.ledger.settle(cost_of(chat["model"], usage_of(raw)) - reserve, f"settle {chat['model']}")
            return Answer(raw, r.headers.get("x-request-id", ""))
        raise NemotronError("retries exhausted")


class ReplayTransport:
    def __init__(self, directory: Path = RECORDINGS):
        self.directory = directory

    def path(self, body: dict, label: str = "") -> Path:
        return self.directory / f"{chat_body(body, label)['model'].split('/')[-1]}-{request_key(body)}.json"

    def answer_for(self, body: dict, label: str = "") -> Answer:
        p = self.path(body, label)
        if not p.exists():
            raise ReplayMissing(f"no recording {p.name}; run once with NEBIUS_API_KEY and BILLRADAR_LIVE=1")
        rec = json.loads(p.read_text())
        return Answer(rec["response"], rec.get("request_id", ""))


class NemotronClient:
    provider = "nebius"
    # Token Factory, 2026-10-27: "At most 8 image(s) may be provided in one prompt" (HTTP 400 on Super VL).
    max_images = int(os.environ.get("NEMOTRON_MAX_IMAGES", "8"))

    def __init__(self, transport=None, record_to: Path | None = RECORDINGS, journal: Path | None = JOURNAL):
        self.transport = transport or self._default_transport()
        self.record_to = record_to
        self.journal = journal
        self.calls: list[dict] = []  # every request of this client, for the case receipt (billradar.receipt)

    @staticmethod
    def _default_transport():
        key = os.environ.get("NEBIUS_API_KEY", "")
        if key and os.environ.get("BILLRADAR_LIVE") == "1":
            return LiveTransport(key)
        return ReplayTransport()

    @property
    def live(self) -> bool:
        return isinstance(self.transport, LiveTransport)

    def transcribe(self, request: dict, audio: bytes, label: str = "") -> tuple[dict, bool]:
        raise NotOnNemotron("voice transcription is not on Nemotron: Token Factory has no speech model; type the note")

    def respond(self, body: dict, label: str = "") -> Result:
        chat = chat_body(body, label)
        images = sum(1 for m in chat["messages"] if isinstance(m["content"], list)
                     for p in m["content"] if p["type"] == "image_url")
        if images > self.max_images:
            raise NemotronError(f"{images} images in one request; Token Factory takes at most {self.max_images}")
        started = time.monotonic()
        if isinstance(self.transport, ReplayTransport):
            ans = self.transport.answer_for(body, label)
        else:
            ans = self.transport.answer(chat)
        seconds = round(time.monotonic() - started, 2)
        if self.live and self.record_to:
            self.record_to.mkdir(parents=True, exist_ok=True)
            out = self.record_to / f"{chat['model'].split('/')[-1]}-{request_key(body)}.json"
            out.write_text(json.dumps({"request_id": ans.request_id, "response": ans.raw}, indent=1))
        usage = usage_of(ans.raw)
        self._log(body, chat, ans, label, usage, seconds)  # before the checks: a refused answer was still billed
        text = answer_text(ans.raw, body)
        return Result(text, responses_events(ans.raw), usage, ans.raw.get("id", ""), replayed=not self.live,
                      seconds=seconds)

    def _log(self, body: dict, chat: dict, ans: Answer, label: str, usage: dict, seconds: float) -> None:
        images = sum(1 for m in chat["messages"] if isinstance(m["content"], list)
                     for p in m["content"] if p["type"] == "image_url")
        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "label": label, "provider": self.provider, "endpoint": f"{BASE_URL}/chat/completions",
            "model": chat["model"], "answered_by": ans.raw.get("model", ""), "asked_as": body["model"],
            "key": request_key(body), "live": self.live, "response_id": ans.raw.get("id", ""),
            "request_id": ans.request_id, "images": images, "seconds": seconds, "usage": usage,
            "usd": round(cost_of(chat["model"], usage), 6),
        }
        self.calls.append(entry)
        if not self.journal:
            return
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a") as f:
            f.write(json.dumps(entry) + "\n")
