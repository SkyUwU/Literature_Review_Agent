import json
import unittest

from literature_review.models import LlmSynthesisOutline, SynthesisResponse
from literature_review.synthesis import (
    SynthesisError, _section_report_call, build_section_report_prompt, synthesize_report,
)
from test_synthesis import (
    llm_synthesis_fixture, valid_directions_payload,
)


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate_json(self, prompt, schema=None):
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        return response if isinstance(response, str) else json.dumps(response)


def body(marker):
    return {"report": "The supplied evidence describes a bounded approach whose results "
            "must be interpreted within the stated setting and evaluation scope. " + marker}


def outline():
    return LlmSynthesisOutline.model_validate({"sections": [
        {"title": "Methods", "purpose": "Describe method mechanisms.",
         "supporting_claim_ids": ["claim-1", "claim-2"]},
        {"title": "Limitations", "purpose": "Describe evaluation limitations.",
         "supporting_claim_ids": ["claim-2", "claim-4"]},
    ]})


class SectionReportTests(unittest.TestCase):
    def test_scopes_partial_paper_and_multiple_papers(self):
        notes = [
            {"paper_id": "p1", "claims": [
                {"claim_id": "claim-1", "text": "METHOD_CONTENT", "aspect": "method"},
                {"claim_id": "claim-4", "text": "PRIVATE_OTHER_SECTION", "aspect": "limitations"},
            ]},
            {"paper_id": "p2", "claims": [
                {"claim_id": "claim-2", "text": "SHARED_CONTENT", "aspect": "results"},
            ]},
        ]
        prompt = build_section_report_prompt("original query", outline(), 0, notes)
        self.assertIn("original query", prompt)
        self.assertIn("Limitations", prompt)
        self.assertIn("METHOD_CONTENT", prompt)
        self.assertIn("SHARED_CONTENT", prompt)
        self.assertIn('"paper_id": "p2"', prompt)
        self.assertNotIn("PRIVATE_OTHER_SECTION", prompt)
        self.assertNotIn('"claim-4"', prompt)

    def test_cross_section_id_repairs_once(self):
        client = Client([body("[claim-4]"), body("[claim-2]")])
        section = _section_report_call(client, "query", outline(), 0, [])
        self.assertEqual(section.cited_claim_ids, ["claim-2"])
        self.assertEqual(len(client.prompts), 2)
        self.assertIn("Repair", client.prompts[1])

    def test_schema_then_marker_failure_does_not_get_third_call(self):
        client = Client(['{"report":', body("[claim-99]")])
        with self.assertRaisesRegex(SynthesisError, "section 1.*Methods"):
            _section_report_call(client, "query", outline(), 0, [])
        self.assertEqual(len(client.prompts), 2)

    def test_invalid_body_types_have_one_repair(self):
        for bad in [body(""), body("[claim-99]"),
                    {"report": "## Unwanted heading\n" + body("[claim-1]")["report"]},
                    {"report": "Heading\n=======\n" + body("[claim-1]")["report"]}]:
            with self.subTest(bad=bad):
                client = Client([bad, body("[claim-1]")])
                result = _section_report_call(client, "query", outline(), 0, [])
                self.assertEqual(len(client.prompts), 2)
                self.assertEqual(result.cited_claim_ids, ["claim-1"])

    def test_multiple_sections_and_global_directions_and_old_json(self):
        response, packs, note = llm_synthesis_fixture()
        client = Client([outline().model_dump(), body("[claim-1] [claim-2]"),
                         body("[claim-2] [claim-4]"), valid_directions_payload()])
        result = synthesize_report(response, packs, [note], client, query="original query")
        self.assertEqual(len(client.prompts), 4)
        self.assertEqual([s.section_index for s in result.report_sections], [1, 2])
        self.assertTrue(result.report.startswith("# original query\n\n## Methods"))
        self.assertLess(result.report.index("## Methods"), result.report.index("## Limitations"))
        self.assertNotIn('"claim_id": "claim-4"', client.prompts[1])
        self.assertIn('"claim_id": "claim-4"', client.prompts[3])
        self.assertEqual(result.outline, outline())
        payload = result.model_dump()
        payload.pop("outline")
        payload.pop("report_sections")
        old = SynthesisResponse.model_validate(payload)
        self.assertIsNone(old.outline)
        self.assertEqual(old.report_sections, [])

    def test_failed_section_stops_before_directions(self):
        response, packs, note = llm_synthesis_fixture()
        client = Client([outline().model_dump(), body("[claim-1]"),
                         body("[claim-1]"), body("[claim-1]")])
        with self.assertRaisesRegex(SynthesisError, "section 2.*Limitations"):
            synthesize_report(response, packs, [note], client)
        self.assertEqual(len(client.prompts), 4)
        self.assertFalse(any(p.startswith("Propose future") for p in client.prompts))
