# Task 1A Handoff

## Start here

1. Read `AGENTS.md`.
2. Run `git status` before changing anything.
3. Run the test suite:

   ```powershell
   uv sync
   uv run python -m unittest discover -s tests -v
   ```

As of 2026-08-21, the latest committed milestone is `9abd987 feat: retrieve evidence chunks and improve deduplication`. The working tree also contains an uncommitted LLM interface and handoff documentation. The only expected untracked source PDF is `Summer_Project.pdf`; do not commit it.

## Goal and scope

This repository implements ADSL summer-project **Task 1A: Literature Review Agent**. The intended end-to-end result is a traceable report that starts with a research idea, retrieves papers, evaluates relevance with evidence, synthesizes findings, and proposes future directions.

Task Connection does not prescribe a concrete protocol. Therefore, the project uses Pydantic models and JSON-friendly outputs so later tasks can consume the results.

## Implemented milestones

| Stage | Main files | Status |
| --- | --- | --- |
| Validated task contracts | `literature_review/models.py` | Done |
| OpenAlex search and normalization | `search.py` | Done; no key required |
| Metadata filtering/ranking | `ranking.py` | Done; explainable baseline |
| Candidate selection | `selection.py` | Done |
| Metadata-only initial assessment | `assessment.py` | Done; not full-text review |
| Search planning contract | `planning.py` | Done; rule-based fallback |
| PDF extraction and cross-page chunks | `extraction.py`, `evidence.py` | Done |
| First-stage chunk retrieval | `evidence_ranking.py` | Done; lexical baseline |
| LLM contextual-summary/re-ranking contract | `llm_evidence.py` | Done; fake-client tested, not live-tested |

The current suite has 24 tests. Do not replace tests with only live API checks.

## Important design decisions

- `SelectedPaperSet` is a candidate set for acquiring/reading full text, not a final literature-review inclusion decision.
- PDFs are local inputs stored in `data/papers/`, which is ignored due to size/copyright. `pypdf` extracts text and preserves source page numbers.
- `EvidenceChunk` can span pages and records `page_start` and `page_end`.
- The first chunk retrieval stage is intentionally cheap and deterministic. It currently ranks lexical matches; an embedding retriever can later replace it without changing the response contract.
- The second stage should be an LLM contextual-summary/re-ranking step. It must receive a bounded set of retrieved chunks, return structured evidence summaries with page references, and then support a paper-level assessment.
- Same-title records are a pragmatic deduplication baseline. The current rule prefers higher citation count, then venue presence, abstract length, then year. It is not proof of identity. Add DOI-based deduplication when DOI metadata is available.

## Next milestone: wire the LLM stage into a small end-to-end flow

`literature_review.llm_evidence` now contains a provider-agnostic `JsonGenerationClient`, a `GeminiJsonClient`, prompt construction, Pydantic validation, provenance enrichment, and fake-client tests. It has not made a live request.

1. Ask the user to run `uv sync` and confirm that Google AI Studio shows an existing key and quota. Do not ask them to share the key.
2. The user copies `.env.example` to `.env` and fills in `GEMINI_API_KEY` locally. `.env` is ignored by Git.
3. Add a small opt-in CLI flow: local PDF -> extraction -> chunking -> top-k retrieval -> `summarize_and_rerank`.
4. Start with one PDF and 2-3 chunks. Display only non-secret error details and page-traceable output.
5. Run one live smoke test only after the user confirms the key setup. Keep the fake-client tests.

Suggested sequence after the live LLM step:

1. aggregate selected evidence summaries into an evidence-based `PaperAssessment`;
2. build synthesis with citations to chunks/pages;
3. add an LLM search planner only if the rule-based planner proves inadequate;
4. consider an embedding retriever and GROBID/Docling only after the baseline works on real PDFs.

## API and operational notes

- OpenAlex is the primary paper search provider because it works without an API key. `search.py` uses `urllib`, a User-Agent, timeout, and retries. A past Windows/Python TLS EOF error was intermittent; `curl.exe` returned HTTP 200. If it returns repeatedly, consider switching that module to `httpx`, not disabling TLS verification.
- Semantic Scholar previously returned rate-limit errors in the shared environment. Treat it as an optional later metadata/citation-graph source, with caching and backoff.
- Google Gemini API keys and quotas are managed in Google AI Studio. The user should inspect **API Keys** and **Dashboard > Usage**. Free-tier limits are adequate for a very small smoke test but vary by model/project.

## External implementation references

- PaperQA2: use its pattern of retrieve chunks -> contextual summaries -> LLM re-ranking -> grounded answer. Do not copy/install the whole project.
  https://github.com/Future-House/paper-qa
- OpenScholar: reference for retrieval + reranking and optional citation constraints.
  https://github.com/AkariAsai/OpenScholar
- STORM: reference for research planning before report generation.
  https://github.com/stanford-oval/storm

## Handoff prompt for another coding agent

```text
Work in this repository on ADSL summer-project Task 1A. Read AGENTS.md and HANDOFF.md first. Inspect git status and preserve user changes. Implement only the “Next milestone: Gemini evidence summarization and re-ranking” described in HANDOFF.md. Do not request or print API keys; use .env/environment variables. Keep Pydantic contracts, add unit tests with a fake client, run the full test suite, and report the exact PowerShell commands for the user to commit. Reply in Traditional Chinese and keep explanations concise.
```
