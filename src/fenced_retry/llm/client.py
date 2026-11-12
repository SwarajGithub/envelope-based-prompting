"""LLMClient: the only path from the controller to the model (D-015).

Talks to Ollama's native chat endpoint (D-007). Every call is recorded by the
usage tracker (D-016). Checks before and after each call are described in
design/HLD.md §6.4.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from fenced_retry.llm.tokens import estimate_prompt_tokens
from fenced_retry.llm.usage import UsageTracker, new_call_id, normalise_project, sha256_text

DEFAULT_ENDPOINT = "http://localhost:11434"


class LLMError(Exception):
    """Base class for errors that must halt a batch."""


class OllamaUnavailable(LLMError):
    """Ollama is not reachable. Configuration error, not a record failure (HLD §16)."""


class DigestMismatch(LLMError):
    """The model digest reported by Ollama differs from the envelope pin (D-033)."""


class BudgetExceeded(LLMError):
    """Token or wall-clock budget reached (D-018). Batch pauses."""


class PromptTooLarge(LLMError):
    """Prompt does not fit the context with the output allowance (D-010)."""


@dataclass(frozen=True)
class LLMSettings:
    model: str = "qwen3:8b"
    endpoint: str = DEFAULT_ENDPOINT
    num_ctx: int = 4096           # effective limit measured on this machine (D-010)
    num_predict: int = 512        # output allowance inside the context
    temperature: float = 0.0      # repeatability (D-009)
    seed: int = 0                 # varied across repeats (D-030)
    think: bool = False           # thinking off by default (D-013)
    call_timeout_s: float = 120.0 # per-call timeout (A13); allows for model load
    expected_digest: str | None = None  # pin from the envelope (D-033)
    max_total_tokens: int | None = None   # budget (D-018); set after smoke test (D-032)
    max_wall_seconds: float | None = None
    store_full_text: bool = False  # D-017
    usage_db: Path = Path("runs/usage.sqlite")
    stage_temperatures: tuple[tuple[str, float], ...] = ()  # from configs/sampling.toml (D-057)

    def temperature_for(self, stage: str) -> float:
        """Executor uses the envelope temperature; other stages use their configured value."""
        if stage == "executor":
            return self.temperature
        return dict(self.stage_temperatures).get(stage, self.temperature)


@dataclass
class LLMResult:
    call_id: str
    status: str                    # ok | timeout | error | truncated | too_large
    text: str | None = None
    thinking: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    load_ms: float | None = None
    latency_ms: float | None = None
    error_text: str | None = None


class LLMClient:
    def __init__(
        self,
        settings: LLMSettings,
        tracker: UsageTracker | None = None,
        http: Any | None = None,
    ) -> None:
        self.settings = settings
        self.tracker = tracker or UsageTracker(settings.usage_db)
        self._http = http or httpx.Client(base_url=settings.endpoint, timeout=settings.call_timeout_s)
        self._digest_checked = False
        self._digest: str | None = None
        self._total_tokens = 0
        self._total_seconds = 0.0

    # ----- checks -------------------------------------------------------

    def _check_digest(self) -> str | None:
        """Read the model digest from Ollama; compare with the pin once per client."""
        try:
            resp = self._http.get("/api/tags")
        except httpx.ConnectError as exc:
            raise OllamaUnavailable(f"cannot reach {self.settings.endpoint}: {exc}") from exc
        data = resp.json()
        digest = None
        for entry in data.get("models", []):
            if entry.get("name") == self.settings.model or entry.get("model") == self.settings.model:
                digest = entry.get("digest")
                break
        if digest is None:
            raise OllamaUnavailable(f"model {self.settings.model} not listed by Ollama")
        if self.settings.expected_digest and digest != self.settings.expected_digest:
            raise DigestMismatch(f"digest {digest} != pinned {self.settings.expected_digest}")
        self._digest_checked = True
        self._digest = digest
        return digest

    def _check_budget(self) -> None:
        s = self.settings
        if s.max_total_tokens is not None and self._total_tokens >= s.max_total_tokens:
            raise BudgetExceeded(f"token budget {s.max_total_tokens} reached")
        if s.max_wall_seconds is not None and self._total_seconds >= s.max_wall_seconds:
            raise BudgetExceeded(f"time budget {s.max_wall_seconds}s reached")

    # ----- call ---------------------------------------------------------

    def chat(self, messages: list[dict[str, str]], project_name: str | None = None,
             stage: str = "executor") -> LLMResult:
        s = self.settings
        project = normalise_project(project_name)
        temperature = s.temperature_for(stage)
        call_id = new_call_id()
        prompt_text = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        base_row: dict[str, Any] = {
            "call_id": call_id,
            "project_name": project,
            "model": s.model,
            "endpoint": s.endpoint,
            "num_ctx": s.num_ctx,
            "num_predict": s.num_predict,
            "temperature": temperature,
            "stage": stage,
            "seed": s.seed,
            "think": int(s.think),
            "prompt_hash": sha256_text(prompt_text),
            "store_full_text": int(s.store_full_text),
            "input_text": prompt_text if s.store_full_text else None,
        }

        # Pre-call checks.
        self._check_budget()
        if not self._digest_checked:
            self._check_digest()
        base_row["model_digest"] = self._digest

        est = estimate_prompt_tokens(messages)
        if est + s.num_predict > s.num_ctx:
            result = LLMResult(call_id=call_id, status="too_large",
                               error_text=f"estimated prompt {est} + output {s.num_predict} > context {s.num_ctx}")
            self._record(base_row, result)
            return result

        body = {
            "model": s.model,
            "messages": messages,
            "stream": False,
            "think": s.think,
            "options": {
                "num_ctx": s.num_ctx,
                "num_predict": s.num_predict,
                "temperature": temperature,
                "seed": s.seed,
            },
        }

        started = time.monotonic()
        try:
            resp = self._http.post("/api/chat", json=body)
            elapsed_ms = (time.monotonic() - started) * 1000
            if resp.status_code != 200:
                result = LLMResult(call_id=call_id, status="error", latency_ms=elapsed_ms,
                                   error_text=f"HTTP {resp.status_code}: {resp.text[:300]}")
            else:
                result = self._parse(call_id, resp.json(), elapsed_ms)
        except httpx.ConnectError as exc:
            raise OllamaUnavailable(f"cannot reach {s.endpoint}: {exc}") from exc
        except httpx.TimeoutException as exc:
            elapsed_ms = (time.monotonic() - started) * 1000
            result = LLMResult(call_id=call_id, status="timeout", latency_ms=elapsed_ms,
                               error_text=str(exc))

        self._total_tokens += (result.input_tokens or 0) + (result.output_tokens or 0)
        self._total_seconds += (result.latency_ms or 0) / 1000
        self._record(base_row, result)
        return result

    def _parse(self, call_id: str, data: dict[str, Any], elapsed_ms: float) -> LLMResult:
        message = data.get("message")
        if not isinstance(message, dict) or "content" not in message:
            return LLMResult(call_id=call_id, status="error", latency_ms=elapsed_ms,
                             error_text="response has no message.content")
        input_tokens = data.get("prompt_eval_count")
        output_tokens = data.get("eval_count")
        load_ms = (data.get("load_duration") or 0) / 1e6
        if input_tokens is not None and input_tokens >= self.settings.num_ctx:
            # Silent truncation is possible at the context limit (D-011).
            return LLMResult(call_id=call_id, status="truncated", text=message.get("content"),
                             thinking=message.get("thinking") if self.settings.think else None,
                             input_tokens=input_tokens, output_tokens=output_tokens,
                             load_ms=load_ms, latency_ms=elapsed_ms,
                             error_text=f"prompt_eval_count {input_tokens} reached context {self.settings.num_ctx}")
        return LLMResult(
            call_id=call_id,
            status="ok",
            text=message.get("content"),
            thinking=message.get("thinking") if self.settings.think else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            load_ms=load_ms,
            latency_ms=elapsed_ms,
        )

    def _record(self, base_row: dict[str, Any], result: LLMResult) -> None:
        s = self.settings
        row = dict(base_row)
        row.update({
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "thinking_tokens": None,  # Ollama does not report thinking tokens separately (D-016)
            "load_ms": result.load_ms,
            "latency_ms": result.latency_ms,
            "status": result.status,
            "error_text": result.error_text,
            "output_hash": sha256_text(result.text),
            "thinking_hash": sha256_text(result.thinking),
            "output_text": result.text if s.store_full_text else None,
            "thinking_text": result.thinking if s.store_full_text else None,
        })
        self.tracker.record(row)

    def close(self) -> None:
        self.tracker.close()
        close = getattr(self._http, "close", None)
        if close:
            close()
