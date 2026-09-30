# Literature Review Agent

Task 1A of the ADSL summer project.

The goal is to turn a research idea into a traceable literature-review report:

1. plan a search;
2. retrieve and assess papers;
3. synthesize findings; and
4. propose evidence-backed future directions.

## Current system

The project includes an end-to-end literature-review pipeline: search planning, Semantic Scholar/OpenAlex retrieval, paper filtering and ranking, optional LLM screening, open-access PDF download, full-text extraction, evidence scoring, per-paper notes, and a claim-traceable synthesis report. The early data-model and OpenAlex-only milestones are historical, not the current system state.

Before retrieval, a search plan is produced from a bare query string. `literature_review.planning.create_llm_plan(query, client)` is the default planner (validated `SearchPlan`, `generated_by="llm"`); `literature_review.planning.create_rule_based_plan()` is the fallback (no key needed), used whenever the LLM planner is unavailable and always for `--dry-run`. Both keep `SearchPlan.idea` optional/`None`, so the downstream Semantic Scholar / OpenAlex search consumes one query-only contract.

The evidence layer extracts local PDFs with `literature_review.extraction.extract_pdf_text()` and splits the page text into overlapping `EvidenceChunk` objects. Chunks may span consecutive pages and retain their inclusive page range. Store local paper PDFs in `data/papers/`; this directory is intentionally not tracked by Git.

`literature_review.functional.sample_top_chunks_per_paper()` is the evidence-sampling stage of the formal end-to-end run: each paper's own chunks are ranked *inside that paper* by bge-small (`BAAI/bge-small-en-v1.5`, local and free; the first run downloads ~130MB of model weights from Hugging Face) and the top-2 are sampled for LLM scoring — there is no corpus-wide top-k. The corpus-wide embedding retriever `literature_review.embedding_retriever.retrieve_evidence_embedding()` survives only in the CLI/compare paths (`run_evidence_pipeline`, `retrieve_from_pdf`, `retrieval_eval`, `pairwise_eval`); the lexical `evidence_ranking.retrieve_evidence()` is compare/legacy only.

Automatic PDF acquisition (M3B): `literature_review.pdf_downloader.download_pdf()` fetches an open-access PDF from the source-provider link recorded at search time (`Paper.open_access_pdf_url`) — the OpenAlex `best_oa_location.pdf_url` or the Semantic Scholar `openAccessPdf.url`. `download_and_backfill()` keeps the top-N selection — papers that fail to download (no OA link or network error) are replaced by the next ranked candidates — and reports OA coverage ratios (`oa_ratio_candidates`, `oa_ratio_attempted`, `shortfall`), optionally as JSON. Real smoke runs always download into a temp directory, never into `data/papers/`.

`literature_review.llm_evidence.summarize_and_rerank()` is the RCS (contextual-summary/re-ranking) interface, retained as compare/legacy since C2b — the formal run scores the per-paper sampled chunks with a single 1-10 utility instead (`literature_review.functional.score_chunks_functionally`, 8 chunks per batch). Gemini reads `GEMINI_API_KEY` only from the environment; no key is needed for unit tests.

Run the local extraction and retrieval path without any API key:

```powershell
uv run python -m literature_review.pipeline data/papers/example.pdf "literature review agent" --top-k 3 --dry-run
```

After copying `.env.example` to `.env` and setting the required keys, omit `--dry-run` to run the LLM stage. Start with one PDF and `--top-k 2` or `--top-k 3`. Note the `pipeline` CLI is the compare/legacy path (corpus-wide top-k across all papers combined, not per-paper); the formal end-to-end run is `literature_review.main`, which samples chunks per paper.

`literature_review.synthesis` completes the evidence-cited report with three-layer traceability: every claim flows from an `EvidenceChunk` through a per-paper note (`PaperSummary`) into the final report prose, which carries inline `[claim-N]` citation markers (A5: the programmatic `材料來源清單` maps each claim to its source chunks). Per-paper notes are built only for papers recommended as include or consider. Report generation runs as a two-stage, three-call flow (outline → report → future directions): the LLM first plans a thematic outline (2-4 sections each anchored to `claim-N` ids), then writes the report prose, then proposes future directions in a separate call — each call receives the notes payload only, never the chunk-level assessments. Future directions come from explicit limitations stated in the retrieved evidence, with a deterministic fallback when none yields a direction.

Run the multi-PDF synthesis flow over a folder of papers (requires `GEMINI_API_KEY`):

```powershell
uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 8
```

`--top-k 8` means 8 chunks across all papers combined in this compare/legacy CLI (not per-paper); the formal end-to-end run (`literature_review.main`) samples top-2 chunks per paper instead.

## Run the full pipeline (M3C)

One interactive query produces a complete literature-review report: plan -> search/rank -> download open-access PDFs -> extract evidence -> LLM synthesis. Parameters are mostly hard-coded (`LIMIT=100`, `TOTAL_TARGET=20`, the functional policy `top_chunks_per_paper=2` / `batch_size=8` / `threshold=6.0`; `target_n = ceil(20 / query_count)`) with a research search policy: every planned query searches only the last `YEAR_WINDOW=3` years (2026 → 2024) and is hard-filtered to a top-venue whitelist (`ranking.TOP_VENUE_ALIASES`, 17 venues across ML/NLP/IR/CV/AI). Search provider (M5c): with `SEMANTIC_SCHOLAR_API_KEY` set, planned queries are searched through Semantic Scholar first; a missing key, an empty SS result, or a failed SS request falls back to OpenAlex, then SS placeholder abstracts are backfilled from OpenAlex by DOI and any paper still lacking an abstract is dropped before ranking. Paper ranking uses bge-small-en-v1.5 semantic similarity between the query and each paper's title+abstract (plus citation and recency, equal weight); `--dry-run` skips the embedding model and keeps the lexical baseline. Dry run needs no Gemini key; an OpenAlex key in `.env` is used for authenticated search:

```powershell
uv run --env-file .env python -m literature_review.main --dry-run
```

The dry run does not call Gemini or require Gemini keys. If `OPENALEX_API_KEY` is in `.env`, pass `--env-file .env` so OpenAlex can authenticate the search; the main entry also loads only this optional key from `.env` during a dry run. OpenAlex currently recommends a free API key when anonymous search is unavailable.

The full run plans and screens with one key (default `GEMINI_API_KEY`), performs functional scoring with `GEMINI_API_KEY_2` by default, writes per-paper notes with `GEMINI_API_KEY_2`, and produces the synthesis report with `GEMINI_API_KEY_3`. Use `--scoring-key N` and `--notes-key N` to assign separate keys; when `--scoring-key` is omitted, it follows `--notes-key` for backward compatibility. For example, `--plan-key 2 --scoring-key 3 --notes-key 4 --report-key 5` assigns four key groups. Gemini 503 responses are retried up to three times with exponential backoff (about 15, 30, and 60 seconds plus jitter); if retries are exhausted, the run stops without skipping screening or switching models. Set `SEMANTIC_SCHOLAR_API_KEY` in `.env` to make Semantic Scholar the primary search source (without it, OpenAlex is used directly). Every real run also persists the downloaded-papers list as `data/outputs/papers_%Y%m%d_%H%M%S_%f.json` (one entry per actually-downloaded PDF with full metadata, its originating query, screening priority, and on-disk path, plus a run overview) sharing the timestamp of the companion `report_*.json`; neither file is ever written by a `--dry-run`, and the folder accumulates across runs:

Alternatively, set `GROQ_API_KEY` in `.env` for hybrid routing: Groq `openai/gpt-oss-120b` handles planning, token-batched screening with a final global gap synthesis, functional scoring, and report generation; Gemini handles per-paper notes. Screening batches target at most 3,000 estimated input tokens and are spaced at least 61 seconds apart to leave room under an 8K TPM limit. This is a conservative estimate; actual tokenization and organization quotas can differ. An oversized single candidate or any failed screening call stops the run without truncating content or switching providers. `uv sync` installs the Groq Python SDK used by the client. Notes use the key selected by `--notes-key` (default 2). Groq HTTP 503 is retried up to three times with exponential backoff (about 15, 30, and 60 seconds plus jitter). Each LLM call logs prompt characters and UTF-8 bytes (not token estimates); screening also logs its rough token estimate and pacing. Check Groq Limits and each Gemini key's AI Studio rate limit before a full run. Without `GROQ_API_KEY`, the existing all-Gemini configuration remains in effect. `--dry-run` never calls either model provider.

```powershell
uv run python -m literature_review.main
```

Use `--rule-based` to force the deterministic fallback plan; `--dry-run` always uses it, so a dry run needs no key. Downloads go to `data/run/`, which `main()` rebuilds (removes and recreates) at the start of every default run; pass `--dest-dir <folder>` or set `DEST_DIR` to download into a folder you manage instead (that folder is never cleared). Temporary one-off runs can widen or move the window with `--year-from` / `--year-to` and swap the venue whitelist with `--venues` (conference names matched against `ranking.TOP_VENUE_ALIASES` by key or alias and expanded to that conference's full alias set; unrecognized names are warned and used as raw substring tokens; `none` or an empty string disables the filter; all-unrecognized inputs disable the filter with a warning). Multiple planned queries share one dedup set, so a paper already downloaded by an earlier query counts as satisfied and is never re-downloaded or backfilled.

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
