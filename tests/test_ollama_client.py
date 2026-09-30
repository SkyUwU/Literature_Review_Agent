"""Tests for the Ollama JSON client: monkeypatched opener only, no real network."""

import json
import unittest
from unittest.mock import patch

from literature_review.llm_evidence import LlmEvidenceError
from literature_review.ollama_client import _urlopen, OllamaJsonClient


class _FakeRaw:
    """Context-manager response with a byte payload (patched urlopen return)."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def __enter__(self) -> "_FakeRaw":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _ok_json(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


class OllamaJsonClientTests(unittest.TestCase):
    def test_returns_choices_content_from_chat_completion(self) -> None:
        """A normal /v1/chat/completions response yields the raw JSON text."""
        completion = {"choices": [{"message": {"content": '{"assessments": []}'}}]}
        calls: list[tuple] = []

        def fake_urlopen(request, timeout: int) -> _FakeRaw:
            body = json.loads(request.data.decode("utf-8")) if request.data else None
            calls.append((request.method, request.full_url, timeout, body))
            return _FakeRaw(_ok_json(completion))

        with patch("literature_review.ollama_client._urlopen", side_effect=fake_urlopen):
            client = OllamaJsonClient(model="qwen3:8b", base_url="http://ollama:11434/v1")

            text = client.generate_json("Assess these chunks.", {"type": "object"})

        self.assertEqual(text, '{"assessments": []}')
        self.assertEqual(len(calls), 2)  # ping models + chat completions
        self.assertEqual(calls[0][0], "GET")  # type: ignore[union-attr]
        self.assertEqual(calls[1][0], "POST")  # type: ignore[union-attr]
        self.assertIn("/chat/completions", calls[1][1])  # type: ignore[union-attr]
        self.assertEqual(calls[1][2], 120)  # type: ignore[union-attr]  generation timeout

        request_body = calls[1][3]  # type: ignore[index]
        self.assertEqual(request_body["response_format"]["type"], "json_schema")  # type: ignore[index]
        self.assertEqual(request_body["response_format"]["json_schema"]["schema"], {"type": "object"})  # type: ignore[index]
        self.assertEqual(request_body["temperature"], 0.2)  # type: ignore[index]
        self.assertFalse(request_body["stream"])  # type: ignore[index]

    def test_empty_content_raises_llm_evidence_error(self) -> None:
        """An empty message content is a provider failure, not a valid response."""
        completion = {"choices": [{"message": {"content": ""}}]}

        def fake_urlopen(request, timeout: int) -> _FakeRaw:
            return _FakeRaw(_ok_json(completion))

        with patch("literature_review.ollama_client._urlopen", side_effect=fake_urlopen):
            client = OllamaJsonClient(model="qwen3:8b", base_url="http://ollama:11434/v1")
            with self.assertRaises(LlmEvidenceError):
                client.generate_json("Assess these chunks.")

    def test_http_500_raises_llm_evidence_error(self) -> None:
        """HTTP failures surface as LlmEvidenceError with the status code."""
        from urllib.error import HTTPError

        def fake_urlopen(request, timeout: int) -> _FakeRaw:
            raise HTTPError(request.full_url, 500, "Internal Server Error", {}, None)

        with patch("literature_review.ollama_client._urlopen", side_effect=fake_urlopen):
            with self.assertRaisesRegex(LlmEvidenceError, "HTTP 500"):
                OllamaJsonClient(model="qwen3:8b", base_url="http://ollama:11434/v1")

    def test_connection_error_on_ping_raises_early(self) -> None:
        """An unreachable base URL fails at construction time, before any run."""
        from urllib.error import URLError

        def fake_urlopen(request, timeout: int) -> _FakeRaw:
            raise URLError("connection refused")

        with patch("literature_review.ollama_client._urlopen", side_effect=fake_urlopen):
            with self.assertRaisesRegex(LlmEvidenceError, "unreachable"):
                OllamaJsonClient(model="qwen3:8b", base_url="http://ollama:11434/v1")


if __name__ == "__main__":
    unittest.main()