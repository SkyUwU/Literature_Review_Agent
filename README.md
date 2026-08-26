# Literature Review Agent

Task 1A of the ADSL summer project.

The goal is to turn a research idea into a traceable literature-review report:

1. plan a search;
2. retrieve and assess papers;
3. synthesize findings; and
4. propose evidence-backed future directions.

## Current milestone

The first milestone defines validated data models. It intentionally does not call an LLM or a paper-search API yet.

The second milestone searches the OpenAlex API without requiring an API key. Papers without an abstract or author metadata are skipped because the later summarization stage needs source evidence. The third milestone filters, ranks, and selects a small reading set while preserving the full search provenance.

Before retrieval, `literature_review.planning.create_rule_based_plan()` creates a traceable `SearchPlan` from a `ResearchIdea`. A future LLM planner will produce the same validated format.

The evidence layer extracts local PDFs with `literature_review.extraction.extract_pdf_text()` and splits the page text into overlapping `EvidenceChunk` objects. Chunks may span consecutive pages and retain their inclusive page range. Store local paper PDFs in `data/papers/`; this directory is intentionally not tracked by Git.

`literature_review.evidence_ranking.retrieve_evidence()` is the first evidence-selection stage. It currently uses an inexpensive lexical baseline to retrieve top-k chunks; a later embedding retriever and LLM contextual-summary stage will use the same data contracts.

`literature_review.llm_evidence.summarize_and_rerank()` is the second stage. It validates structured LLM assessments and preserves the trusted paper ID and page range from retrieved chunks. Gemini reads `GEMINI_API_KEY` only from the environment; no key is needed for unit tests.

Run the local extraction and retrieval path without any API key:

```powershell
uv run python -m literature_review.pipeline data/papers/example.pdf "literature review agent" --top-k 3 --dry-run
```

After copying `.env.example` to `.env` and setting `GEMINI_API_KEY`, omit `--dry-run` to run the LLM re-ranking stage. Start with one PDF and `--top-k 2` or `--top-k 3`.

`literature_review.synthesis` completes the evidence-cited report with three-layer traceability: every claim flows from an `EvidenceChunk` through a per-paper note (`PaperSummary`) into the final report prose, which carries inline `[chunk_id]` citation markers. Per-paper notes are built only for papers recommended as include or consider. Future directions come from three sources: strong cross-paper convergence, explicit limitations stated in the retrieved evidence, and a deterministic fallback when neither yields a direction.

Run the multi-PDF synthesis flow over a folder of papers (requires `GEMINI_API_KEY`):

```powershell
uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 8
```

## Run the demo

After setting up the environment, run:

```powershell
python -m literature_review.demo
```

## Search papers

```powershell
uv run python -m literature_review.search "literature review agent" --limit 5 --year-from 2024
```

Add `--rank` to apply the current transparent baseline ranking:

```powershell
uv run python -m literature_review.search "literature review agent" --limit 10 --year-from 2024 --rank
```

Select a bounded reading set (this still uses metadata and abstracts only):

```powershell
uv run python -m literature_review.search "literature review agent" --limit 20 --year-from 2024 --top-k 5 --min-score 3
```

Create an initial metadata-and-abstract assessment. It is a reading-priority recommendation, not a full-text review:

```powershell
uv run python -m literature_review.search "literature review agent" --limit 20 --year-from 2024 --top-k 5 --assess
```
