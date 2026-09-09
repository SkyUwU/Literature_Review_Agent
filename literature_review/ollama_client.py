"""Local Ollama implementation of :class:`JsonGenerationClient` (M6).

``OllamaJsonClient`` talks to a local Ollama OpenAI-compatible endpoint
(``/v1/chat/completions``) so the RCS scoring stage can run on a local model
(Qwen3 8B by default) instead of the Gemini free tier. The provider contract
is unchanged: ``generate_json`` returns exactly one JSON object as text.

Configuration comes from ``load_local_env``: ``OLLAMA_MODEL`` (default
``qwen3:8b``) and ``OLLAMA_BASE_URL`` (default ``http://localhost:11434/v1``).
The constructor performs a light ping (``GET {base_url}/models``) so a broken
URL fails fast before a full pipeline run reaches RCS (K-series D1 precedent:
a real run failure aborts rather than silently falling back).
"""

import json
import os
import urllib.error
import urllib.request
from typing import Any

from langfuse import observe

from literature_review.llm_evidence import LlmEvidenceError, load_local_env
from literature_review.models import LlmEvidenceAssessmentBatch

_DEFAULT_MODEL = "qwen3:8b"
_DEFAULT_BASE_URL = "http://localhost:11434/v1"
_TIMEOUT_PING_SECONDS = 5
_TIMEOUT_GENERATE_SECONDS = 120
_TEMPERATURE = 0.2


def _urlopen(request: urllib.request.Request, timeout: int) -> Any:
    """Indirection so tests can monkeypatch the socket opener."""
    return urllib.request.urlopen(request, timeout=timeout)


class OllamaJsonClient:
    """Local Ollama implementation of the JSON generation provider protocol."""

    def __init__(self, model: str | None = None, base_url: str | None = None) -> None:
        load_local_env()
        self._model = model or os.getenv("OLLAMA_MODEL") or _DEFAULT_MODEL
        self._base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
        if not self._model:
            raise LlmEvidenceError("OLLAMA_MODEL is not set in the environment.")
        self._ping()

    def _ping(self) -> None:
        """Fail fast when the base URL is unreachable or not an Ollama /v1 endpoint."""
        try:
            self._request("GET", f"{self._base_url}/models", timeout=_TIMEOUT_PING_SECONDS)
        except urllib.error.HTTPError as error:
            raise LlmEvidenceError(f"Ollama ping failed with HTTP {error.code}.") from error
        except LlmEvidenceError:
            raise
        except Exception as error:  # network errors, timeouts, malformed URL
            raise LlmEvidenceError(f"Ollama is unreachable at {self._base_url}.") from error

    @observe(name="llm_call")
    def generate_json(self, prompt: str, schema: dict | None = None) -> str:
        """POST one chat completion and return the raw JSON text of the reply."""
        response_schema = schema or LlmEvidenceAssessmentBatch.model_json_schema()
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "evidence_batch", "schema": response_schema},
            },
            "temperature": _TEMPERATURE,
            "stream": False,
        }
        try:
            parsed = self._request(
                "POST",
                f"{self._base_url}/chat/completions",
                payload=body,
                timeout=_TIMEOUT_GENERATE_SECONDS,
            )
        except urllib.error.HTTPError as error:
            raise LlmEvidenceError(f"Ollama request failed with HTTP {error.code}.") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise LlmEvidenceError(f"Ollama request failed: {error!r}") from error

        try:
            content = parsed["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise LlmEvidenceError("Ollama response was missing choices[0].message.content.") from error
        if not content:
            raise LlmEvidenceError("Ollama returned no text output.")
        return content

    def _request(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: int,
    ) -> dict[str, Any]:
        """Perform one HTTP request and parse the JSON body."""
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with _urlopen(request, timeout=timeout) as raw:
            body = raw.read().decode("utf-8")
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise LlmEvidenceError("Ollama response was not a JSON object.")
        return parsed