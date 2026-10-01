import io
import unittest
from contextlib import redirect_stdout

from literature_review.models import EvidenceChunk
from literature_review.section_stats import (
    canonical_section_name,
    classify_chunk_category,
    is_appendix_chunk,
    print_section_distribution,
    render_section_distribution,
    section_distribution,
)


def _chunk(chunk_id: str, section: str | None) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        paper_id=chunk_id.split("-")[0],
        section=section,
        text="A sufficiently long chunk text for the section stats test.",
    )


class CanonicalSectionNameTests(unittest.TestCase):
    def test_layer_one_singular_and_plural(self) -> None:
        cases = [
            ("Introduction", "introduction"),
            ("Conclusion", "conclusion"),
            ("Conclusions", "conclusion"),
            ("Method", "method"),
            ("Methods", "method"),
            ("Methodology", "method"),
            ("Methodologies", "method"),
            ("Evaluation", "evaluation"),
            ("Evaluations", "evaluation"),
            ("Experiments", "experiments"),
            ("Results", "results"),
            ("Related Work", "related work"),
            ("Background", "background"),
            ("Discussion", "discussion"),
            ("Future Work", "future work"),
            ("Limitations", "limitations"),
            ("Abstract", "abstract"),
        ]
        for heading, expected in cases:
            with self.subTest(heading=heading):
                self.assertEqual(canonical_section_name(heading), expected)

    def test_layer_two_aliases_fold_into_families(self) -> None:
        cases = [
            ("Approach", "method"),
            ("Our Approach", "method"),
            ("Proposed Method", "method"),
            ("Proposed Approach", "method"),
            ("Proposed Framework", "method"),
            ("Experimental Setup", "experiments"),
            ("Experimental Results", "results"),
            ("Empirical Evaluation", "evaluation"),
        ]
        for heading, expected in cases:
            with self.subTest(heading=heading):
                self.assertEqual(canonical_section_name(heading), expected)

    def test_keep_name_bucketing(self) -> None:
        cases = [
            ("Datasets", "datasets"),
            ("Case Studies", "case studies"),
            ("12 Case Studies", "12 case studies"),
            ("Survey of Tools", "survey of tools"),
        ]
        for heading, expected in cases:
            with self.subTest(heading=heading):
                self.assertEqual(canonical_section_name(heading), expected)

    def test_none_empty_and_post_drop_collapse_to_other(self) -> None:
        cases = [
            (None, "other"),
            ("", "other"),
            ("   ", "other"),
            ("References", "other"),
            ("Bibliography", "other"),
            ("Reference List", "other"),
            ("Appendix A", "other"),
            ("Acknowledgements", "other"),
        ]
        for heading, expected in cases:
            with self.subTest(heading=heading):
                self.assertEqual(canonical_section_name(heading), expected)


class SectionDistributionTests(unittest.TestCase):
    def test_counts_top_level_sections_per_paper(self) -> None:
        sampled = {
            "p1": [
                _chunk("p1-c1", "**Paper** > **Introduction**"),
                _chunk("p1-c2", "**Paper** > **Approach**"),
                _chunk("p1-c3", "**Paper** > **Approach**"),
            ],
            "p2": [
                _chunk("p2-c1", "**Paper** > **Experimental Setup**"),
                _chunk("p2-c2", None),
            ],
        }

        distribution = section_distribution(sampled)

        self.assertEqual(
            distribution,
            {
                "p1": {"context": 1, "method": 2},
                "p2": {"evaluation_setup": 1, "other": 1},
            },
        )

    def test_category_aliases_and_nested_headings(self) -> None:
        cases = {
            "**Paper** > **1 Introduction**": "context",
            "**Paper** > **3.2 Proposed Method**": "method",
            "**Paper** > **4 Experimental Setup**": "evaluation_setup",
            "**Paper** > **5.1 Ablation Results**": "results",
            "**Paper** > **6 Limitations and Future Work**": "limitations_future",
        }
        for section, expected in cases.items():
            with self.subTest(section=section):
                self.assertEqual(classify_chunk_category(_chunk("p1-c1", section)), expected)

    def test_generic_experiments_uses_setup_cues(self) -> None:
        chunk = _chunk("p1-c1", "Paper > 4 Experiments")
        chunk.text = "We use datasets, baselines, and metrics for evaluation."
        self.assertEqual(classify_chunk_category(chunk), "evaluation_setup")

    def test_appendix_subsection_is_classified_but_unmapped_is_other(self) -> None:
        eligible = _chunk("p1-c1", "Paper > Appendix > A.1 Additional Results")
        unknown = _chunk("p1-c2", "Paper > Appendix > C.2 Extra Proof")
        self.assertTrue(is_appendix_chunk(eligible))
        self.assertEqual(classify_chunk_category(eligible), "results")
        self.assertEqual(classify_chunk_category(unknown), "other")

    def test_empty_and_none_safe(self) -> None:
        self.assertEqual(section_distribution({}), {})
        self.assertEqual(section_distribution({"p1": []}), {"p1": {}})


class RenderTests(unittest.TestCase):
    def test_render_has_per_paper_lines_and_aggregate_table(self) -> None:
        distribution = {"p1": {"method": 2, "introduction": 1}}

        rendered = render_section_distribution(distribution, paper_titles={"p1": "A Title"})

        lines = rendered.splitlines()
        self.assertIn("p1 (A Title): introduction:1 method:2", lines)
        header = "Section distribution across 3 sampled chunks (evidence categories)"
        self.assertIn(header, lines)
        self.assertIn("  introduction          1  (papers: 1)", lines)
        self.assertIn("  method                2  (papers: 1)", lines)

    def test_print_section_distribution_captured_on_stdout(self) -> None:
        sampled = {
            "p1": [
                _chunk("p1-c1", "**Paper** > **Methodology**"),
                _chunk("p1-c2", "**Paper** > **Results**"),
            ]
        }
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_section_distribution(sampled, paper_titles={"p1": "A Title"})
        self.assertIn("Section distribution across 2 sampled chunks", buffer.getvalue())
        self.assertIn("method:1", buffer.getvalue())
        self.assertIn("results:1", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
