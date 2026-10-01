import argparse
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from literature_review.llm_evidence import OpenAIJsonClient
from literature_review import main as main_module


class OpenAIJsonClientTests(unittest.TestCase):
    def test_constructor_uses_api_key_default_model_and_stage_override(self) -> None:
        sdk_client = mock.Mock()
        sdk_factory = mock.Mock(return_value=sdk_client)
        fake_sdk = types.SimpleNamespace(OpenAI=sdk_factory)
        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-openai-key",
                "OPENAI_MODEL": "global-model",
                "OPENAI_MODEL_REPORT": "report-model",
            },
            clear=True,
        ), mock.patch.dict(sys.modules, {"openai": fake_sdk}), mock.patch(
            "literature_review.llm_evidence.load_local_env"
        ):
            client = OpenAIJsonClient(label="report")

        self.assertEqual(client._model, "report-model")
        sdk_factory.assert_called_once_with(
            api_key="test-openai-key", max_retries=2, timeout=180.0
        )

    def test_generate_json_sends_schema_and_returns_content(self) -> None:
        completion = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"ok": true}', refusal=None)
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=20, completion_tokens=5, total_tokens=25
            ),
        )
        create = mock.Mock(return_value=completion)
        client = object.__new__(OpenAIJsonClient)
        client._client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create)
            )
        )
        client._model = "gpt-5.6-luna"
        client._label = "report"

        result = client.generate_json("Return JSON", {"type": "object"})

        self.assertEqual(result, '{"ok": true}')
        call = create.call_args.kwargs
        self.assertEqual(call["model"], "gpt-5.6-luna")
        self.assertEqual(call["response_format"]["type"], "json_schema")
        self.assertEqual(call["response_format"]["json_schema"]["schema"], {"type": "object"})

    def test_explicit_openai_provider_routes_all_stages_and_screening(self) -> None:
        args = argparse.Namespace(rule_based=False, dry_run=False)
        with mock.patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "openai",
                "OPENAI_API_KEY": "test-key",
                "GROQ_API_KEY": "also-set-groq-key",
                "OLLAMA_BASE_URL": "",
            },
            clear=True,
        ), mock.patch("literature_review.main.load_local_env"), mock.patch(
            "literature_review.main.OpenAIJsonClient"
        ) as client_cls:
            plan, notes, scoring, rcs, report = main_module._build_clients(args)
            screening = main_module._build_screen_client(args, plan)

        self.assertEqual(
            [call.kwargs["label"] for call in client_cls.call_args_list],
            ["plan+screening", "paper-notes", "functional-scoring", "report", "screening"],
        )
        self.assertIsNotNone(plan)
        self.assertIsNotNone(notes)
        self.assertIsNotNone(scoring)
        self.assertIsNone(rcs)
        self.assertIsNotNone(report)
        self.assertIsNotNone(screening)


if __name__ == "__main__":
    unittest.main()
