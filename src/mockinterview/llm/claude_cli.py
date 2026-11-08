"""Dev-time LLM backend that shells out to headless Claude Code (`claude -p`).

Prototyping only: each call starts a process (seconds of overhead), gives no sampling control, and rides a personal
subscription. Production uses the Anthropic API behind the same `LLM` interface (see PRODUCT.md).
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time

from .base import LLMError, LLMResult


class ClaudeCliLLM:
    def __init__(self, model: str | None = None, timeout_s: float = 120.0, executable: str = "claude"):
        self.model = model
        self.timeout_s = timeout_s
        self.executable = executable
        # Run from an empty temp dir so no project instructions/memory leak into grading prompts.
        self._cwd = tempfile.mkdtemp(prefix="mockinterview_llm_")

    def complete(self, *, system: str, prompt: str, model: str | None = None) -> LLMResult:
        args = [
            self.executable, "-p", "--output-format", "json",
            "--tools", "", "--system-prompt", system,
            "--no-session-persistence", "--disable-slash-commands",
            "--strict-mcp-config", "--setting-sources", "",
        ]
        chosen = model or self.model
        if chosen:
            args += ["--model", chosen]
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                args, input=prompt, capture_output=True, text=True, encoding="utf-8",
                timeout=self.timeout_s, cwd=self._cwd,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude CLI timed out after {self.timeout_s}s") from exc
        latency = time.perf_counter() - started
        if proc.returncode != 0:
            raise LLMError(f"claude CLI exit {proc.returncode}: {proc.stderr.strip()[:300]}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError(f"claude CLI returned non-JSON: {proc.stdout[:200]!r}") from exc
        if payload.get("is_error"):
            raise LLMError(f"claude CLI error: {str(payload.get('result'))[:300]}")
        models = list((payload.get("modelUsage") or {}).keys())
        return LLMResult(
            text=payload.get("result", ""),
            latency_s=latency,
            cost_usd=payload.get("total_cost_usd"),
            model=models[0] if models else chosen,
        )
