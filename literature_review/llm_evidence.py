"""LLM-based contextual summaries for a bounded set of retrieved evidence chunks."""

import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from literature_review.models import (
    EvidenceRerankResponse,
    EvidenceRetrievalResponse,
    EvidenceSummary,
    LlmEvidenceAssessment,
    LlmEvidenceAssessmentBatch,
)

from langfuse import observe

RCS_BATCH_SIZE = 4

# Gemini free tier (gemini-3.6-flash): 5 requests per minute, 20 per day. Both
# are overridable per environment; 0 disables the corresponding local guard.
DEFAULT_REQUESTS_PER_MINUTE = 5
DEFAULT_REQUESTS_PER_DAY = 20
DEFAULT_429_RETRIES = 1
DEFAULT_503_RETRIES = 3
DEFAULT_503_BASE_DELAY_SECONDS = 15.0
DEFAULT_GROQ_TPM_LIMIT = 8_000
DEFAULT_GROQ_TPM_HEADROOM = 300
DEFAULT_GROQ_COMPLETION_RESERVE = 800
DEFAULT_GROQ_429_RETRIES = 2
# One second of slack on top of 60/rpm so a request never lands exactly on the
# provider's own per-minute boundary.
PACE_SLACK_SECONDS = 1.0


class LlmEvidenceError(RuntimeError):
    """Raised for missing configuration or invalid model output."""


class LlmOutputSyntaxError(LlmEvidenceError):
    """Raised when a model response is not syntactically valid JSON."""


class LlmServiceError(LlmEvidenceError):
    """Raised when a bounded retry budget for a provider-side 5xx is exhausted."""


def _log_prompt_size(label: str, model: str, prompt: str) -> None:
    """Log exact prompt characters and UTF-8 bytes, not an estimated token count."""
    print(
        f"[llm] label={label} model={model} prompt_chars={len(prompt)} "
        f"prompt_utf8_bytes={len(prompt.encode('utf-8'))}",
        file=sys.stderr,
    )


class DailyQuotaExhausted(LlmEvidenceError):
    """Raised when a provider limit makes the request pointless to retry.

    Covers the 429 "N requests per day" ceiling, any other 429 without a usable
    ``retry in Ns`` hint, and a per-minute 429 whose hint did not clear the
    condition within the retry budget. A rejected request still counts against
    the provider quota, so retrying further would only spend more of it.
    """


class JsonGenerationClient(Protocol):
    """Minimal provider interface; tests use a fake and Gemini is one implementation."""

    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        """Return exactly one JSON object encoded as text."""


class GeminiJsonClient:
    """Google Gemini implementation; the key comes from ``api_key`` or the environment.

    Every call passes through a per-key :class:`_RateTracker`, so one run cannot
    outpace the provider's per-minute ceiling or its daily one, and each call is
    logged to stderr as ``[llm] label=... calls=n/m`` to make a real run's key
    spend measurable (the previous runs left logs with no trace of how many
    requests were issued).

    Pacing is **per key**, so the notes key and the report key never wait on
    each other; a full run with many batches therefore grows by roughly
    ``60 / GEMINI_REQUESTS_PER_MINUTE`` seconds per additional key2 call.
    ``label`` names the stage for the log (never the key value itself).
    """

    def __init__(
        self,
        model: str = "gemini-3.6-flash",
        api_key: str | None = None,
        *,
        label: str | None = None,
    ) -> None:
        load_local_env()
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise LlmEvidenceError("GEMINI_API_KEY is not set in the environment.")
        try:
            from google import genai
        except ImportError as error:
            raise LlmEvidenceError("Install dependencies with 'uv sync' before using Gemini.") from error
        # The SDK's Interactions API has its own default 5xx retry loop. Disable
        # those nested retries so every provider attempt is paced and counted by
        # our per-key tracker and the bounded application-level policy below.
        self._client = genai.Client(
            api_key=key,
            http_options=genai.types.HttpOptions(
                retry_options=genai.types.HttpRetryOptions(attempts=1)
            ),
        )
        self._model = model
        self._label = label or "gemini"
        self._key_id = _key_slot(key)
        self._tracker = _tracker_for(key)

    @observe(name="llm_call")
    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        _log_prompt_size(self._label, self._model, prompt)
        retry_budget = _env_int("GEMINI_429_RETRIES", DEFAULT_429_RETRIES)
        retry_after_attempt = 0
        service_attempt = 0
        while True:
            self._tracker.require_slot()
            delay = self._tracker.wait_for_slot()
            self._tracker.record()
            try:
                interaction = self._client.interactions.create(
                    model=self._model,
                    input=prompt,
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": (
                            schema
                            if schema is not None
                            else LlmEvidenceAssessmentBatch.model_json_schema()
                        ),
                    },
                )
            except Exception as error:
                kind, retry_after = classify_provider_error(error)
                if kind == "retry_after" and retry_after_attempt < retry_budget:
                    retry_after_attempt += 1
                    self._log(
                        delay,
                        f"429 retry_after={retry_after:g}s "
                        f"attempt={retry_after_attempt}/{retry_budget}",
                    )
                    self._tracker.pause(retry_after)
                    continue
                if kind == "service" and service_attempt < DEFAULT_503_RETRIES:
                    service_attempt += 1
                    backoff = DEFAULT_503_BASE_DELAY_SECONDS * (2 ** (service_attempt - 1))
                    backoff += random.uniform(0.0, 1.0)
                    self._log(
                        delay,
                        f"503 retry_in={backoff:.1f}s "
                        f"attempt={service_attempt}/{DEFAULT_503_RETRIES}",
                    )
                    self._tracker.pause(backoff)
                    continue
                self._log(delay, f"error kind={kind}")
                raise _translate_error(kind, error) from error
            if not interaction.output_text:
                self._log(delay, "error kind=empty_output")
                raise LlmEvidenceError("Gemini returned no text output.")
            self._log(delay, "ok")
            return interaction.output_text

    def _log(self, delay: float, status: str) -> None:
        budget = "off" if self._tracker.requests_per_day <= 0 else self._tracker.requests_per_day
        print(
            f"[llm] label={self._label} key={self._key_id} delay_s={delay:.1f} "
            f"calls={self._tracker.calls}/{budget} status={status}",
            file=sys.stderr,
        )


class GroqJsonClient:
    """Groq OpenAI-compatible JSON client used across the formal LLM stages."""

    DEFAULT_MODEL = "openai/gpt-oss-120b"
    STAGE_MODEL_ENV = {
        "plan+screening": "GROQ_MODEL_PLAN",
        "screening": "GROQ_MODEL_SCREENING",
        "functional-scoring": "GROQ_MODEL_SCORING",
        "paper-notes": "GROQ_MODEL_NOTES",
        "report": "GROQ_MODEL_REPORT",
    }

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        label: str | None = None,
    ) -> None:
        load_local_env()
        key = api_key or os.getenv("GROQ_API_KEY")
        if not key:
            raise LlmEvidenceError("GROQ_API_KEY is not set in the environment.")
        try:
            from groq import Groq
        except ImportError as error:
            raise LlmEvidenceError(
                "Install dependencies with 'uv sync' before using Groq."
            ) from error
        self._client = Groq(api_key=key, max_retries=0, timeout=180.0)
        self._label = label or "groq"
        stage_model = os.getenv(self.STAGE_MODEL_ENV.get(self._label, ""))
        self._model = model or stage_model or os.getenv("GROQ_MODEL") or self.DEFAULT_MODEL
        self._tpm_pacer = _groq_tpm_pacer()

    @observe(name="llm_call")
    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        _log_prompt_size(self._label, self._model, prompt)
        response_schema = schema or LlmEvidenceAssessmentBatch.model_json_schema()
        service_retries = 0
        tpm_retries = 0
        bypass_pacer_once = False
        while True:
            if not bypass_pacer_once:
                self._tpm_pacer.wait_for_capacity(prompt, self._label)
            bypass_pacer_once = False
            started_at = time.monotonic()
            try:
                raw_response = self._client.chat.completions.with_raw_response.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "literature_review_output",
                            "strict": False,
                            "schema": response_schema,
                        },
                    },
                    temperature=0.2,
                    stream=False,
                )
                completion = raw_response.parse()
                break
            except Exception as error:
                status_code = getattr(error, "status_code", None)
                if status_code == 503 and service_retries < DEFAULT_503_RETRIES:
                    delay = 15.0 * (2**service_retries) + random.uniform(0.0, 1.0)
                    service_retries += 1
                    print(
                        f"[llm] label={self._label} model={self._model} "
                        f"status=503 retry_in={delay:.1f}s attempt={service_retries}/3",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                retry_after = _groq_tpm_retry_after(error)
                if retry_after is not None and tpm_retries < DEFAULT_GROQ_429_RETRIES:
                    tpm_retries += 1
                    delay = retry_after + random.uniform(0.1, 0.5)
                    self._tpm_pacer.update_rate_headers(
                        getattr(getattr(error, "response", None), "headers", {})
                    )
                    print(
                        f"[llm] label={self._label} model={self._model} "
                        f"status=429 kind=tpm retry_in={delay:.1f}s "
                        f"attempt={tpm_retries}/{DEFAULT_GROQ_429_RETRIES}",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    # Groq's retry-after reflects organization-wide usage,
                    # including calls outside this process; honor that reset.
                    bypass_pacer_once = True
                    continue
                if status_code is not None and 500 <= status_code < 600:
                    raise LlmServiceError(
                        f"Groq service returned HTTP {status_code} after bounded retries."
                    ) from error
                if status_code is None and type(error).__name__ in {
                    "APIConnectionError",
                    "APITimeoutError",
                }:
                    raise LlmServiceError(f"Groq request failed: {error}") from error
                if status_code is not None:
                    raise LlmEvidenceError(
                        f"Groq request failed with HTTP {status_code}: {error}"
                    ) from error
                raise LlmEvidenceError(f"Groq request failed: {error}") from error

        try:
            content = completion.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as error:
            raise LlmEvidenceError("Groq response was missing message content.") from error
        usage = getattr(completion, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        self._tpm_pacer.record_usage(
            prompt,
            self._label,
            started_at,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        self._tpm_pacer.update_rate_headers(raw_response.headers)
        print(
            f"[llm] label={self._label} model={self._model} "
            f"prompt_tokens={prompt_tokens if prompt_tokens is not None else 'unknown'} "
            f"completion_tokens={completion_tokens if completion_tokens is not None else 'unknown'} "
            f"total_tokens={total_tokens if total_tokens is not None else 'unknown'}",
            file=sys.stderr,
        )
        if not isinstance(content, str) or not content.strip():
            raise LlmEvidenceError("Groq returned no text output.")
        return content.strip()


class OpenAIJsonClient:
    """OpenAI Chat Completions client for schema-validated stage outputs."""

    DEFAULT_MODEL = "gpt-5.6-luna"
    STAGE_MODEL_ENV = {
        "plan+screening": "OPENAI_MODEL_PLAN",
        "screening": "OPENAI_MODEL_SCREENING",
        "functional-scoring": "OPENAI_MODEL_SCORING",
        "paper-notes": "OPENAI_MODEL_NOTES",
        "report": "OPENAI_MODEL_REPORT",
    }

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        label: str | None = None,
    ) -> None:
        load_local_env()
        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            raise LlmEvidenceError("OPENAI_API_KEY is not set in the environment.")
        try:
            from openai import OpenAI
        except ImportError as error:
            raise LlmEvidenceError(
                "Install dependencies with 'uv sync' before using OpenAI."
            ) from error
        self._client = OpenAI(api_key=key, max_retries=2, timeout=180.0)
        self._label = label or "openai"
        stage_model = os.getenv(self.STAGE_MODEL_ENV.get(self._label, ""))
        self._model = model or stage_model or os.getenv("OPENAI_MODEL") or self.DEFAULT_MODEL

    @observe(name="llm_call")
    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        _log_prompt_size(self._label, self._model, prompt)
        response_schema = schema or LlmEvidenceAssessmentBatch.model_json_schema()
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "literature_review_output",
                        "strict": False,
                        "schema": response_schema,
                    },
                },
                temperature=0.2,
            )
        except Exception as error:
            status_code = getattr(error, "status_code", None)
            if status_code is not None and 500 <= status_code < 600:
                raise LlmServiceError(
                    f"OpenAI service returned HTTP {status_code} after SDK retries."
                ) from error
            if status_code is None and type(error).__name__ in {
                "APIConnectionError",
                "APITimeoutError",
            }:
                raise LlmServiceError(f"OpenAI request failed: {error}") from error
            if status_code is not None:
                raise LlmEvidenceError(
                    f"OpenAI request failed with HTTP {status_code}: {error}"
                ) from error
            raise LlmEvidenceError(f"OpenAI request failed: {error}") from error

        try:
            message = completion.choices[0].message
        except (AttributeError, IndexError, TypeError) as error:
            raise LlmEvidenceError("OpenAI response was missing message content.") from error
        if getattr(message, "refusal", None):
            raise LlmEvidenceError("OpenAI refused the request instead of returning JSON.")
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise LlmEvidenceError("OpenAI returned no text output.")

        usage = getattr(completion, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        print(
            f"[llm] label={self._label} model={self._model} "
            f"prompt_tokens={prompt_tokens if prompt_tokens is not None else 'unknown'} "
            f"completion_tokens={completion_tokens if completion_tokens is not None else 'unknown'} "
            f"total_tokens={total_tokens if total_tokens is not None else 'unknown'}",
            file=sys.stderr,
        )
        return content.strip()


class _GroqTpmPacer:
    """Share measured token usage and conservative preflight estimates across Groq stages."""

    WINDOW_SECONDS = 60.0

    def __init__(self, limit: int, headroom: int) -> None:
        self.limit = limit
        self.target = max(1, limit - headroom) if limit > 0 else 0
        self._events: deque[tuple[float, int]] = deque()
        self._prompt_scale: dict[str, float] = {}
        self._completion_reserve: dict[str, int] = {}
        self._provider_remaining_tokens: int | None = None
        self._provider_reset_at: float | None = None
        self._lock = threading.Lock()

    def _estimate(self, prompt: str, label: str) -> int:
        chars_per_four_tokens = (len(prompt) + 3) // 4
        return max(1, round(chars_per_four_tokens * self._prompt_scale.get(label, 1.0)))

    def wait_for_capacity(self, prompt: str, label: str) -> None:
        if self.limit <= 0:
            return
        estimate = self._estimate(prompt, label)
        reserve = self._completion_reserve.get(label, DEFAULT_GROQ_COMPLETION_RESERVE)
        requested = estimate + reserve
        if requested > self.target:
            raise LlmEvidenceError(
                f"Groq {label} prompt is too large for the configured TPM budget: "
                f"estimated input {estimate} + output reserve {reserve} > {self.target}. "
                "Reduce this stage's batch size or input."
            )
        while True:
            now = time.monotonic()
            with self._lock:
                wait_seconds = 0.0
                used = 0
                if self._provider_remaining_tokens is not None:
                    remaining = self._provider_remaining_tokens
                    if requested > remaining:
                        if self._provider_reset_at is not None and self._provider_reset_at > now:
                            reset_at = self._provider_reset_at
                            wait_seconds = reset_at - now + 0.1
                            used = self.limit - remaining
                        elif self._provider_reset_at is not None:
                            self._provider_remaining_tokens = None
                            self._provider_reset_at = None
                            self._events.clear()
                            print(
                                f"[llm] label={label} tpm_ready after_provider_reset "
                                f"next_estimated_input={estimate} output_reserve={reserve}",
                                file=sys.stderr,
                            )
                            return
                        else:
                            self._provider_remaining_tokens = None
                            self._provider_reset_at = None
                    else:
                        self._provider_remaining_tokens = None
                        self._provider_reset_at = None
                        print(
                            f"[llm] label={label} tpm_ready "
                            f"next_estimated_input={estimate} output_reserve={reserve} "
                            f"provider_remaining={remaining}",
                            file=sys.stderr,
                        )
                        return
                while self._events and now - self._events[0][0] >= self.WINDOW_SECONDS:
                    self._events.popleft()
                if self._provider_remaining_tokens is None:
                    used = sum(tokens for _, tokens in self._events)
                    if used + requested <= self.target:
                        print(
                            f"[llm] label={label} tpm_ready "
                            f"recent_actual_tokens={used} next_estimated_input={estimate} "
                            f"output_reserve={reserve}",
                            file=sys.stderr,
                        )
                        return
                    release_needed = used + requested - self.target
                    releasing = 0
                    reset_at = now
                    for timestamp, tokens in self._events:
                        releasing += tokens
                        reset_at = timestamp + self.WINDOW_SECONDS
                        if releasing >= release_needed:
                            break
                    wait_seconds = max(0.1, reset_at - now)
            print(
                f"[llm] label={label} tpm_wait_s={wait_seconds:.1f} "
                f"recent_actual_tokens={used} next_estimated_input={estimate} "
                f"completion_reserve={reserve}",
                file=sys.stderr,
            )
            time.sleep(min(wait_seconds, self.WINDOW_SECONDS))

    def record_usage(
        self,
        prompt: str,
        label: str,
        started_at: float,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
    ) -> None:
        char_estimate = max(1, (len(prompt) + 3) // 4)
        actual_prompt = prompt_tokens if prompt_tokens is not None else char_estimate
        actual_completion = (
            completion_tokens
            if completion_tokens is not None
            else self._completion_reserve.get(label, DEFAULT_GROQ_COMPLETION_RESERVE)
        )
        actual_total = (
            total_tokens
            if total_tokens is not None
            else actual_prompt + actual_completion
        )
        with self._lock:
            old_scale = self._prompt_scale.get(label, actual_prompt / char_estimate)
            measured_scale = actual_prompt / char_estimate
            self._prompt_scale[label] = 0.5 * old_scale + 0.5 * measured_scale
            old_reserve = self._completion_reserve.get(
                label, DEFAULT_GROQ_COMPLETION_RESERVE
            )
            self._completion_reserve[label] = max(
                384, round(0.5 * old_reserve + 0.5 * actual_completion * 1.25)
            )
            self._events.append((started_at, actual_total))

    def update_rate_headers(self, headers: object) -> None:
        if not hasattr(headers, "get"):
            return
        remaining_raw = headers.get("x-ratelimit-remaining-tokens")
        reset_raw = headers.get("x-ratelimit-reset-tokens")
        try:
            remaining = int(remaining_raw) if remaining_raw is not None else None
        except (TypeError, ValueError):
            remaining = None
        reset_seconds = _duration_seconds(reset_raw)
        with self._lock:
            if remaining is not None and remaining >= 0:
                self._provider_remaining_tokens = remaining
                self._provider_reset_at = (
                    time.monotonic() + reset_seconds if reset_seconds is not None else None
                )


_GROQ_TPM_PACER: _GroqTpmPacer | None = None
_GROQ_TPM_PACER_LOCK = threading.Lock()


def _groq_tpm_pacer() -> _GroqTpmPacer:
    global _GROQ_TPM_PACER
    if _GROQ_TPM_PACER is None:
        with _GROQ_TPM_PACER_LOCK:
            if _GROQ_TPM_PACER is None:
                _GROQ_TPM_PACER = _GroqTpmPacer(
                    _env_int("GROQ_TPM_LIMIT", DEFAULT_GROQ_TPM_LIMIT),
                    _env_int("GROQ_TPM_HEADROOM", DEFAULT_GROQ_TPM_HEADROOM),
                )
    return _GROQ_TPM_PACER


def _groq_tpm_retry_after(error: BaseException) -> float | None:
    """Return a bounded retry delay only for retryable per-minute token limits."""
    if getattr(error, "status_code", None) != 429:
        return None
    message = str(error)
    lowered = message.lower()
    if "per day" in lowered or not any(
        marker in lowered for marker in ("tokens per minute", "token per minute", "tpm")
    ):
        return None
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) or {}
    header_value = headers.get("retry-after") or headers.get("Retry-After")
    if header_value is not None:
        delay = _duration_seconds(header_value) or 0.0
        if 0 < delay <= 90:
            return delay
    match = re.search(r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*s", lowered)
    if match:
        delay = float(match.group(1))
        if 0 < delay <= 90:
            return delay
    return None


def _duration_seconds(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(ms|s|m)?\s*", str(raw), re.I)
        if not match:
            return None
        value = float(match.group(1))
        unit = (match.group(2) or "s").lower()
        return value / 1000 if unit == "ms" else value * 60 if unit == "m" else value


class _RateTracker:
    """Per-key pacing and a process-local daily counter for one provider key.

    The daily count only knows about calls made by this process, so it is a
    guard against a single run overshooting the ceiling, not a model of the
    provider's reset window (that window is deliberately not guessed here; a
    cross-run overrun is the server's 429 to report). ``clock`` and ``sleeper``
    are injectable so tests never really sleep.
    """

    def __init__(
        self,
        *,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
        requests_per_day: int = DEFAULT_REQUESTS_PER_DAY,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.requests_per_day = requests_per_day
        self.interval_seconds = (
            0.0
            if requests_per_minute <= 0
            else 60.0 / requests_per_minute + PACE_SLACK_SECONDS
        )
        self._clock = clock
        self._sleeper = sleeper
        self._last_call_at: float | None = None
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    def can_call(self) -> bool:
        return self.requests_per_day <= 0 or self._calls < self.requests_per_day

    def require_slot(self) -> None:
        """Raise before spending quota once this process has used its daily budget."""
        if not self.can_call():
            raise DailyQuotaExhausted(
                f"This run already used its local budget of {self.requests_per_day} "
                "requests for this key; not calling again."
            )

    def wait_for_slot(self) -> float:
        """Sleep until the per-minute gap has elapsed; return the seconds slept."""
        if self._last_call_at is None or self.interval_seconds <= 0:
            return 0.0
        delay = self.interval_seconds - (self._clock() - self._last_call_at)
        if delay <= 0:
            return 0.0
        self._sleeper(delay)
        return delay

    def record(self) -> None:
        self._calls += 1
        self._last_call_at = self._clock()

    def pause(self, seconds: float) -> None:
        self._sleeper(seconds)


def _key_slot(api_key: str) -> str:
    """A short, non-reversible id so the call log can group calls without the key."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]


_TRACKERS: dict[str, _RateTracker] = {}


def _tracker_for(api_key: str) -> _RateTracker:
    """Return this process's tracker for *api_key*, building it on first use."""
    slot = _key_slot(api_key)
    tracker = _TRACKERS.get(slot)
    if tracker is None:
        tracker = _RateTracker(
            requests_per_minute=_env_int(
                "GEMINI_REQUESTS_PER_MINUTE", DEFAULT_REQUESTS_PER_MINUTE
            ),
            requests_per_day=_env_int("GEMINI_REQUESTS_PER_DAY", DEFAULT_REQUESTS_PER_DAY),
        )
        _TRACKERS[slot] = tracker
    return tracker


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


_RETRY_AFTER_RE = re.compile(r"retry in\s+([0-9]+(?:\.[0-9]+)?)\s*s", re.IGNORECASE)


def classify_provider_error(error: BaseException) -> tuple[str, float]:
    """Map a provider error to ``(kind, retry_after_seconds)``.

    The kind is one of ``retry_after`` (a 429 carrying a usable ``retry in Ns``
    hint), ``daily_quota`` (a 429 that a retry cannot fix), ``service`` (a 503 or
    an overload message) or ``other``. ``google.genai`` puts the HTTP status on
    ``error.code``; the message is only consulted when that is missing or when
    the reason has to be read out of the text.
    """
    text = str(error)
    lowered = text.lower()
    code = getattr(error, "code", None)
    is_429 = code == 429 or "429" in lowered
    if is_429:
        if "requests per day" in lowered or "per day" in lowered:
            return "daily_quota", 0.0
        match = _RETRY_AFTER_RE.search(text)
        if match is not None:
            return "retry_after", float(match.group(1))
        return "daily_quota", 0.0
    if (
        code == 503
        or "503" in lowered
        or "overloaded" in lowered
        or "unavailable" in lowered
    ):
        return "service", 0.0
    return "other", 0.0


def _translate_error(kind: str, error: BaseException) -> LlmEvidenceError:
    if kind in {"daily_quota", "retry_after"}:
        # A per-minute 429 lands here only once its retry hint failed to clear
        # the limit, so the key is still limited: same degrade path as a daily
        # 429, and never another blind retry.
        return DailyQuotaExhausted(str(error))
    if kind == "service":
        return LlmServiceError(str(error))
    if isinstance(error, LlmEvidenceError):
        return error
    return LlmEvidenceError(str(error))


def load_local_env(
    path: str | Path = ".env", *, only: set[str] | None = None
) -> None:
    """Load local KEY=VALUE entries, optionally restricting the variable names.

    Existing process environment values always win. ``only`` lets no-LLM flows
    load provider credentials needed for retrieval without loading model keys.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        if only is not None and key not in only:
            continue
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def build_evidence_prompt(
    response: EvidenceRetrievalResponse,
    chunk_ids: list[str] | None = None,
    paper_meta: dict[str, tuple[int | None, str | None]] | None = None,
) -> str:
    """Ask the model for grounded summaries without letting it alter provenance.

    Each chunk is presented as ``## Chunk N`` with a metadata line (citation
    count and venue from ``paper_meta``, ``n/a`` when unknown) followed by the
    raw text only: no chunk_id, paper_id, or page numbers are sent, so the
    model cannot copy or corrupt trusted identifiers (M5b RCS-input slimming).
    ``chunk_ids`` optionally narrows the prompt to a subset of the retrieved
    chunks (used by the batched RCS loop); ``None`` keeps the full corpus-wide
    prompt (the original single-call behavior).
    """
    selected = response.ranked_chunks
    if chunk_ids is not None:
        wanted = set(chunk_ids)
        selected = [item for item in response.ranked_chunks if item.chunk.chunk_id in wanted]
    blocks = []
    for index, item in enumerate(selected, start=1):
        citation, venue = (paper_meta or {}).get(item.chunk.paper_id, (None, None))
        citation_text = str(citation) if citation is not None else "n/a"
        venue_text = venue if venue else "n/a"
        blocks.append(
            f"## Chunk {index}\n"
            f"Citations: {citation_text} | Venue: {venue_text}\n"
            f"{item.chunk.text}"
        )
    return (
        "Assess each supplied evidence chunk only against the research query. "
        "Do not use outside knowledge and do not invent claims. Return exactly one JSON object, "
        "without Markdown code fences or any surrounding explanation. "
        "The object must contain an 'assessments' array. Each item must include chunk_id "
        "(the chunk number), summary, rationale_relevance, rationale_quality, "
        "relevance_score (1-10), and evidence_quality_score (1-10). "
        "Write the rationale first, then assign scores consistent with it. "
        "First state, in one sentence, the specific sub-question the query is asking. "
        "Then judge whether the chunk's main content — not just overlapping keywords — "
        "actually addresses that sub-question. "
        "A chunk with rich data, tables, or strong methodology is NOT automatically relevant. "
        "Score relevance solely on whether the chunk's core content addresses the query's specific question. "
        "Relevance and evidence quality are independent; a high-quality chunk must not inflate relevance. "
        "If a chunk mixes topics (e.g., a brief transitional sentence followed by unrelated content), "
        "base the relevance score on what the majority of the chunk substantively discusses, "
        "not on isolated overlapping phrases. "
        "Score the two dimensions independently: high relevance does not imply high evidence quality, and vice versa. "
        "Citation count and venue are secondary supporting signals only; "
        "never lower evidence quality solely for low citations when the text itself is concrete. "
        "Use them as context, not as a primary criterion. "
        "Scoring guide. relevance_score: 9-10 the chunk directly answers the query's core question "
        "(extremely rare; 9 = directly strong but not the very core); "
        "7-8 directly relevant and substantively discusses a core facet; "
        "5-6 related background that does not directly answer the core; "
        "3-4 indirectly related (adjacent topic or overlapping terms but different core); "
        "1-2 only a passing mention or marginal overlap. "
        "evidence_quality_score: 9-10 concrete methods, numbers, or conclusions directly backing the claim; "
        "7-8 has data or a clear method; "
        "5-6 clear argument but generic detail; "
        "3-4 vague with few details; "
        "1-2 almost no information (fragmented text). "
        "Do NOT penalize theory or framework papers just because they contain no numbers. "
        f"Research query: {response.query}\nEvidence chunks:\n" + "\n\n".join(blocks)
    )


def build_json_repair_prompt(raw_output: str, expected_chunk_ids: list[str] | None = None) -> str:
    """Request one bounded repair attempt when structured output was malformed.

    When ``expected_chunk_ids`` is supplied (a batch's full chunk-index set),
    the repair prompt additionally demands exactly one assessment for every
    listed index, so a model that silently dropped or renamed chunks gets one
    chance to complete the batch (M5b: single-repair budget per batch).
    """
    coverage_clause = ""
    if expected_chunk_ids is not None:
        coverage_clause = (
            " The repaired response must assess exactly these chunk indexes, each once: "
            f"{json.dumps(expected_chunk_ids)}."
        )
    return (
        "The previous response was malformed JSON. Return a repaired version as exactly one JSON object, "
        "without Markdown or explanation. Preserve the intended assessments and follow the response schema."
        f"{coverage_clause}\nPrevious response:\n{raw_output}"
    )


def validate_evidence_assessments(raw_output: str) -> LlmEvidenceAssessmentBatch:
    """Accept structured JSON while reporting schema failures without exposing model text."""
    normalized = raw_output.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        normalized = "\n".join(lines[1:-1]).strip()
    try:
        return LlmEvidenceAssessmentBatch.model_validate_json(normalized)
    except ValueError as error:
        details = "invalid JSON or schema mismatch"
        if hasattr(error, "errors"):
            issues = error.errors(include_url=False)
            if issues:
                location = ".".join(str(part) for part in issues[0]["loc"])
                details = f"{location}: {issues[0]['msg']}"
                if issues[0].get("type") == "json_invalid":
                    raise LlmOutputSyntaxError(f"LLM output is malformed JSON ({details}).") from error
        raise LlmEvidenceError(f"LLM output failed evidence-assessment validation ({details}).") from error


_MODEL_T = TypeVar("_MODEL_T", bound=BaseModel)


def strip_code_fence(raw_output: str) -> str:
    """Remove Markdown code fences and any trailing text after the last JSON brace."""
    normalized = raw_output.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        normalized = "\n".join(lines[1:-1]).strip()
    last_brace = normalized.rfind("}")
    if last_brace != -1:
        normalized = normalized[: last_brace + 1]
    return normalized


def generate_validated(
    client: JsonGenerationClient,
    model: type[_MODEL_T],
    prompt: str,
    schema: dict | None,
    *,
    parse: Callable[[str], _MODEL_T],
    repair_prompt: Callable[[str, BaseException], str],
) -> _MODEL_T:
    """Call the client once, validate, and repair exactly once on a parse failure.

    ``parse`` raises the caller's domain error type on validation failure and
    ``repair_prompt`` builds one repair request from the raw output and that error.
    The retry budget is exactly one repair: if the repaired output also fails to
    parse, the second ``parse`` call propagates the error (no further retries).
    """
    raw_output = client.generate_json(prompt, schema)
    try:
        return parse(raw_output)
    except Exception as exc:
        return parse(client.generate_json(repair_prompt(raw_output, exc), schema))


def _resolve_indexes(
    batch: LlmEvidenceAssessmentBatch,
    index_map: dict[str, str],
) -> list[LlmEvidenceAssessment]:
    resolved = []
    for assessment in batch.assessments:
        real_id = index_map.get(assessment.chunk_id)
        if real_id is None:
            raise LlmEvidenceError(
                f"LLM output referenced an unknown chunk index {assessment.chunk_id!r}."
            )
        resolved.append(assessment.model_copy(update={"chunk_id": real_id}))
    return resolved


@observe(name="rcs", capture_input=False, capture_output=False)
def summarize_and_rerank(
    response: EvidenceRetrievalResponse,
    client: JsonGenerationClient,
    *,
    batch_size: int = RCS_BATCH_SIZE,
    paper_meta: dict[str, tuple[int | None, str | None]] | None = None,
) -> EvidenceRerankResponse:
    """Validate LLM assessments per bounded batch and enrich them only with trusted provenance.

    The corpus-wide chunk set is split into batches of at most ``batch_size``
    chunks; each batch is an independent call with at most one repair attempt.
    Chunks are presented to the model by index only (``## Chunk N``), so the
    model can never copy or corrupt a real chunk id; the per-batch repair
    prompt names the expected chunk indexes, giving a model that silently
    dropped or renamed chunks one chance to complete the batch. The merged
    result keeps the existing every-chunk-exactly-once invariant.
    """
    ranked_chunks = response.ranked_chunks
    by_chunk_id = {item.chunk.chunk_id: item.chunk for item in ranked_chunks}
    all_assessments: list[LlmEvidenceAssessment] = []
    schema = LlmEvidenceAssessmentBatch.model_json_schema()

    for start in range(0, len(ranked_chunks), batch_size):
        batch_ids = [item.chunk.chunk_id for item in ranked_chunks[start : start + batch_size]]
        index_map = {str(index): chunk_id for index, chunk_id in enumerate(batch_ids, start=1)}
        raw_output = client.generate_json(
            build_evidence_prompt(response, chunk_ids=batch_ids, paper_meta=paper_meta), schema
        )
        try:
            generated = validate_evidence_assessments(raw_output)
            resolved = _resolve_indexes(generated, index_map)
        except LlmEvidenceError:
            # Local models (Ollama) may return valid JSON with the wrong shape
            # (e.g. a missing/renamed 'assessments' key) or an unknown chunk
            # index; give each batch one bounded repair that names the exact
            # expected chunk indexes so an index drift (M5b B=1 probe: qwen
            # echoed '2'/'A1'/'C1' for a single-chunk batch) can be repaired,
            # exactly like the completeness repair below.
            generated = validate_evidence_assessments(
                client.generate_json(
                    build_json_repair_prompt(raw_output, expected_chunk_ids=list(index_map)),
                    schema,
                )
            )
            resolved = _resolve_indexes(generated, index_map)
        returned_ids = [item.chunk_id for item in resolved]
        complete = len(returned_ids) == len(set(returned_ids)) and set(returned_ids) == set(batch_ids)
        if not complete:
            generated = validate_evidence_assessments(
                client.generate_json(
                    build_json_repair_prompt(raw_output, expected_chunk_ids=list(index_map)),
                    schema,
                )
            )
            resolved = _resolve_indexes(generated, index_map)
            returned_ids = [item.chunk_id for item in resolved]
            if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != set(batch_ids):
                raise LlmEvidenceError("LLM output must assess every retrieved chunk exactly once.")
        all_assessments.extend(resolved)

    try:
        from langfuse import langfuse_context

        langfuse_context.update_current_observation(
            input={
                "query": response.query,
                "chunk_ids": [item.chunk.chunk_id for item in ranked_chunks],
            }
        )
    except Exception:
        pass  # Observability must never break the RCS stage.

    summaries = [
        EvidenceSummary(
            **assessment.model_dump(),
            paper_id=by_chunk_id[assessment.chunk_id].paper_id,
            page_start=by_chunk_id[assessment.chunk_id].page_start,
            page_end=by_chunk_id[assessment.chunk_id].page_end,
        )
        for assessment in all_assessments
    ]
    summaries.sort(key=lambda item: (-item.relevance_score, -item.evidence_quality_score, item.chunk_id))
    return EvidenceRerankResponse(
        retrieval_response=response,
        summaries=summaries,
        limitations=[
            "The LLM assessment is limited to the supplied chunks and is not a full-paper review.",
            "Summaries should be checked against their cited source pages before final synthesis.",
        ],
    )
