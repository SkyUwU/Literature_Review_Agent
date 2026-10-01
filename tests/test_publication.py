"""Publication venue and PDF-host separation, with fake provider records only."""

import unittest
from urllib.parse import parse_qs, urlparse

from literature_review.models import FilterPolicy, Paper, SearchRequest
from literature_review.ranking import filter_papers, resolve_venues
from literature_review.search import build_search_url, paper_from_openalex
from literature_review.ss_search import SS_FIELDS, paper_from_ss
from test_main import record_for
from test_ss_search import ss_record


def location(name, source_type, source_id, *, version="publishedVersion"):
    return {
        "source": {"display_name": name, "type": source_type, "id": source_id},
        "landing_page_url": "https://example.org/published-paper",
        "version": version,
    }


class PublicationTests(unittest.TestCase):
    def test_openalex_repository_primary_can_have_conference_publication(self):
        record = record_for("W1")
        record["primary_location"] = location("arXiv", "repository", "arxiv-source", version="submittedVersion")
        record["locations"] = [
            record["primary_location"],
            location("International Conference on Learning Representations", "conference", "iclr-source"),
        ]
        record["best_oa_location"] = {"pdf_url": "https://arxiv.org/pdf/2501.12345"}
        paper = paper_from_openalex(record)
        self.assertEqual(paper.venue, "International Conference on Learning Representations")
        self.assertEqual(paper.pdf_host, "arxiv.org")
        self.assertEqual(paper.venue_verification, "provider_reported")
        evidence = paper.publication_venues[0]
        self.assertEqual(evidence.source_id, "iclr-source")
        self.assertEqual(evidence.metadata_path, "locations[1].source")
        self.assertEqual(evidence.verification, "provider_reported")
        self.assertEqual(filter_papers([paper], FilterPolicy(venues=resolve_venues("ICLR"))), [paper])

    def test_arxiv_alone_is_unconfirmed_even_if_pdf_contains_conference_name(self):
        record = record_for("W1")
        record["primary_location"] = location("arXiv", "repository", "arxiv-source")
        record["best_oa_location"] = {"pdf_url": "https://arxiv.org/pdf/ICLR-paper.pdf"}
        paper = paper_from_openalex(record)
        self.assertIsNone(paper.venue)
        self.assertEqual(paper.publication_venues, [])
        self.assertEqual(paper.venue_verification, "unconfirmed")
        self.assertEqual(filter_papers([paper], FilterPolicy(venues=resolve_venues("ICLR"))), [])
        self.assertEqual(filter_papers([paper], FilterPolicy()), [paper])

    def test_repository_type_and_submitted_version_cannot_prove_conference(self):
        for source_type, version in (("repository", "publishedVersion"), ("conference", "submittedVersion")):
            with self.subTest(source_type=source_type, version=version):
                record = record_for("W1")
                record["primary_location"] = location("ICLR", source_type, "source", version=version)
                paper = paper_from_openalex(record)
                self.assertIsNone(paper.venue)
                self.assertEqual(paper.venue_verification, "unconfirmed")

    def test_all_non_repository_sources_are_checked_and_deduplicated(self):
        record = record_for("W1")
        primary = location("Proceedings Series", "book series", "series")
        iclr = location("ICLR", "conference", "iclr-source")
        record["primary_location"] = primary
        record["locations"] = [primary, iclr]
        record["best_oa_location"] = iclr
        paper = paper_from_openalex(record)
        self.assertEqual(len(paper.publication_venues), 2)
        self.assertEqual(paper.venue, "Proceedings Series")
        self.assertEqual(filter_papers([paper], FilterPolicy(venues=resolve_venues("ICLR"))), [paper])

    def test_ss_structured_venue_is_separate_from_arxiv_pdf(self):
        paper = paper_from_ss(ss_record(
            venue="arXiv",
            publicationVenue={"id": "iclr-id", "name": "ICLR", "type": "conference", "url": "https://iclr.cc"},
            openAccessPdf={"url": "https://arxiv.org/pdf/2501.12345"},
        ))
        self.assertEqual(paper.venue, "ICLR")
        self.assertEqual(paper.pdf_host, "arxiv.org")
        self.assertEqual(paper.publication_venues[0].metadata_path, "publicationVenue")
        self.assertEqual(paper.publication_venues[0].source_id, "iclr-id")
        self.assertEqual(paper.venue_verification, "provider_reported")

    def test_ss_preprint_metadata_does_not_fall_back_to_conflicting_legacy_venue(self):
        paper = paper_from_ss(ss_record(
            venue="ICLR", publicationVenue={"name": "arXiv", "type": "repository"},
        ))
        self.assertIsNone(paper.venue)
        self.assertEqual(paper.publication_venues, [])

    def test_legacy_venue_remains_usable_and_serialization_preserves_provenance(self):
        paper = paper_from_ss(ss_record(venue="ICLR", openAccessPdf=None))
        self.assertEqual(paper.venue, "ICLR")
        self.assertIsNone(paper.pdf_host)
        self.assertEqual(paper.publication_venues[0].metadata_path, "venue")
        payload = paper.model_dump(mode="json")
        self.assertEqual(payload["venue_verification"], "provider_reported")
        self.assertEqual(Paper.model_validate(payload), paper)
        # Old saved records still parse; a legacy name alone is not formal verification.
        payload.pop("publication_venues")
        old = Paper.model_validate(payload)
        self.assertEqual(old.venue_verification, "unconfirmed")
        self.assertEqual(filter_papers([old], FilterPolicy(venues=resolve_venues("ICLR"))), [old])

    def test_request_fields_include_publication_metadata(self):
        fields = parse_qs(urlparse(build_search_url(SearchRequest(query="review agents"))).query)["select"][0]
        self.assertIn("locations", fields.split(","))
        self.assertIn("publicationVenue", SS_FIELDS)


if __name__ == "__main__":
    unittest.main()
