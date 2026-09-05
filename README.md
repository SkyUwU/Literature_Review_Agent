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

Before retrieval, a search plan is produced from a bare query string. `literature_review.planning.create_llm_plan(query, client)` is the default planner (validated `SearchPlan`, `generated_by="llm"`); `literature_review.planning.create_rule_based_plan()` is the fallback (no key needed), used whenever the LLM planner is unavailable and always for `--dry-run`. Both keep `SearchPlan.idea` optional/`None`, so the downstream OpenAlex search consumes one query-only contract.

The evidence layer extracts local PDFs with `literature_review.extraction.extract_pdf_text()` and splits the page text into overlapping `EvidenceChunk` objects. Chunks may span consecutive pages and retain their inclusive page range. Store local paper PDFs in `data/papers/`; this directory is intentionally not tracked by Git.

`literature_review.embedding_retriever.retrieve_evidence_embedding()` is the first evidence-selection stage. It uses semantic (embedding) ranking with a local `BAAI/bge-small-en-v1.5` encoder (free, no key needed) to retrieve the top-k chunks across all papers combined. The first run downloads ~130MB of model weights from Hugging Face. The lexical `evidence_ranking.retrieve_evidence()` is retained only as a compare/legacy baseline; it is no longer used by the pipeline. The LLM contextual-summary stage `summarize_and_rerank()` uses the same retrieval response contract.

Automatic PDF acquisition (M3B): `literature_review.pdf_downloader.download_pdf()` fetches an open-access PDF from the OpenAlex `best_oa_location.pdf_url` recorded at search time (`Paper.open_access_pdf_url`). `download_and_backfill()` keeps the top-N selection — papers that fail to download (no OA link or network error) are replaced by the next ranked candidates — and reports OA coverage ratios (`oa_ratio_candidates`, `oa_ratio_attempted`, `shortfall`), optionally as JSON. Real smoke runs always download into a temp directory, never into `data/papers/`.

`literature_review.llm_evidence.summarize_and_rerank()` is the second stage. It calls the LLM once for the corpus-wide top-k chunks across all papers and validates the structured LLM assessments while preserving the trusted paper ID and page range from retrieved chunks. Gemini reads `GEMINI_API_KEY` only from the environment; no key is needed for unit tests.

Run the local extraction and retrieval path without any API key:

```powershell
uv run python -m literature_review.pipeline data/papers/example.pdf "literature review agent" --top-k 3 --dry-run
```

After copying `.env.example` to `.env` and setting `GEMINI_API_KEY`, omit `--dry-run` to run the LLM re-ranking stage. Start with one PDF and `--top-k 2` or `--top-k 3` (chunks across all papers combined, not per-paper).

`literature_review.synthesis` completes the evidence-cited report with three-layer traceability: every claim flows from an `EvidenceChunk` through a per-paper note (`PaperSummary`) into the final report prose, which carries inline `[chunk_id]` citation markers. Per-paper notes are built only for papers recommended as include or consider. Future directions come from three sources: strong cross-paper convergence, explicit limitations stated in the retrieved evidence, and a deterministic fallback when neither yields a direction.

Run the multi-PDF synthesis flow over a folder of papers (requires `GEMINI_API_KEY`):

```powershell
uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 8
```

`--top-k 8` means 8 chunks across all papers combined, not per-paper.

## Run the full pipeline (M3C)

One interactive query produces a complete literature-review report: plan -> search/rank -> download open-access PDFs -> extract evidence -> LLM synthesis. Parameters are hard-coded (`LIMIT=50`, `MIN_YEAR=2021`, `TOTAL_TARGET=15`, `TOP_K_CHUNKS=8`; `target_n = ceil(15 / query_count)`). With no key, dry-run stops after downloads:

```powershell
uv run python -m literature_review.main --dry-run
```

The full run plans with the LLM planner (default, `GEMINI_API_KEY`) and synthesizes with `GEMINI_API_KEY_2`:

```powershell
uv run python -m literature_review.main
```

Use `--rule-based` to force the deterministic fallback plan; `--dry-run` always uses it, so a dry run needs no key. Downloads go to `data/papers/`. Multiple planned queries share one dedup set, so a paper already downloaded by an earlier query counts as satisfied and is never re-downloaded or backfilled.

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
