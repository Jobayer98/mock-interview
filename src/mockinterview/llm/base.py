"""Provider-agnostic LLM interface. Everything else in the product talks to this, never to a vendor SDK/CLI."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResult:
    text: str
    latency_s: float
    cost_usd: float | None = None
    model: str | None = None


class LLM(Protocol):
    def complete(self, *, system: str, prompt: str, model: str | None = None) -> LLMResult: ...


class LLMError(RuntimeError):
    pass


class MeteredLLM:
    """Wraps any LLM and records every call, retries and failures included. Views created with `for_purpose` share one log."""

    def __init__(self, inner: LLM, log: list[dict] | None = None, purpose: str = "other"):
        self.inner = inner
        self.log: list[dict] = [] if log is None else log
        self.purpose = purpose

    def for_purpose(self, purpose: str) -> "MeteredLLM":
        return MeteredLLM(self.inner, self.log, purpose)

    def complete(self, *, system: str, prompt: str, model: str | None = None) -> LLMResult:
        started = time.perf_counter()
        try:
            result = self.inner.complete(system=system, prompt=prompt, model=model)
        except LLMError:
            self.log.append({"purpose": self.purpose, "model": model, "latency_s": round(time.perf_counter() - started, 3), "cost_usd": None, "error": True})
            raise
        self.log.append({"purpose": self.purpose, "model": result.model or model, "latency_s": round(result.latency_s, 3), "cost_usd": result.cost_usd, "error": False})
        return result


def summarize_calls(log: list[dict]) -> dict:
    """Per-session totals, overall and by purpose."""
    purposes: dict[str, dict] = {}
    for call in log:
        entry = purposes.setdefault(call["purpose"], {"calls": 0, "errors": 0, "cost_usd": 0.0, "latency_s": 0.0})
        entry["calls"] += 1
        entry["errors"] += int(call["error"])
        entry["cost_usd"] = round(entry["cost_usd"] + (call["cost_usd"] or 0.0), 6)
        entry["latency_s"] = round(entry["latency_s"] + call["latency_s"], 3)
    return {"calls": len(log), "cost_usd": round(sum(c["cost_usd"] or 0.0 for c in log), 6), "by_purpose": purposes}


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def escape_tags(text: str, *tags: str) -> str:
    """Neutralize any form of these tags, whatever the case or spacing, so untrusted text cannot break out of its data block."""
    pattern = re.compile(r"<\s*(/?)\s*(" + "|".join(re.escape(tag) for tag in tags) + r")\b[^>]*>", re.IGNORECASE)
    return pattern.sub(lambda m: f"[{m.group(1)}{m.group(2)}]", text)


def extract_json(text: str) -> dict:
    """Parse a JSON object from model output, tolerating code fences and surrounding prose."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"no JSON object in model output: {text[:200]!r}") from None
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"invalid JSON in model output: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("model output JSON is not an object")
    return value
