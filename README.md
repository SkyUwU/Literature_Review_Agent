# Literature Review Agent

Task 1A of the ADSL summer project.

The goal is to turn a research idea into a traceable literature-review report:

1. plan a search;
2. retrieve and assess papers;
3. synthesize findings; and
4. propose evidence-backed future directions.

## Current system

The project includes an end-to-end literature-review pipeline: search planning, Semantic Scholar/OpenAlex retrieval, paper filtering and ranking, optional LLM screening, open-access PDF download, full-text extraction, evidence scoring, per-paper notes, and a claim-traceable synthesis report. The early data-model and OpenAlex-only milestones are historical, not the current system state.

Before retrieval, a search plan is produced from a bare query string. `literature_review.planning.create_llm_plan(query, client)` is the default planner (validated `SearchPlan`, `generated_by="llm"`); `literature_review.planning.create_rule_based_plan()` is the no-key fallback and always used for `--dry-run`. Both save the original query in `SearchPlan.idea`; optional/`None` remains supported for old JSON. New LLM plans also provide `task_interpretation`, subordinate to the original idea, in the same response. The original idea and optional interpretation are passed to screening, scoring, and report generation; per-paper notes retain the paper's own task without receiving the user's idea.

The evidence layer extracts local PDFs with `literature_review.extraction.extract_pdf_text()` and splits the page text into overlapping `EvidenceChunk` objects. The legacy word-based splitter can preserve inclusive page ranges. The formal chapter-based splitter merges page Markdown before splitting and currently leaves chunk page bounds empty. Store local paper PDFs in `data/papers/`; this directory is intentionally not tracked by Git.

`literature_review.functional.sample_formal_chunks_per_paper()` is the evidence-sampling stage of the formal end-to-end run. It classifies headings, then ranks each paper's chunks *inside that paper* with bge-small (`BAAI/bge-small-en-v1.5`, local and free; first run downloads ~130MB from Hugging Face). Functional scoring receives one Method and one Results chunk when available (missing slots are filled from remaining candidates). Per-paper notes receive one Abstract/context chunk plus up to two each from Method, Evaluation Setup, Results, and Limitations/Future Work (maximum nine). References and acknowledgments are dropped; Appendix chunks survive only when their subsection maps to one of those categories. Chunk vectors are reused for both selections. The corpus-wide embedding retriever `literature_review.embedding_retriever.retrieve_evidence_embedding()` survives only in the CLI/compare paths (`run_evidence_pipeline`, `retrieve_from_pdf`, `retrieval_eval`, `pairwise_eval`); the lexical `evidence_ranking.retrieve_evidence()` is compare/legacy only.

The nine-chunk notes cap is about 2,150–2,300 words at the default chunk size, roughly 3,000–4,500 prompt tokens per paper before completion tokens. With Groq's default estimated 2,500-token notes batch budget, a paper will commonly use about two requests; actual usage is reported by the provider.

PDF acquisition starts with the provider URL in `Paper.open_access_pdf_url`. Every download must pass PDF signature, PyMuPDF parser/page, and DOI/title identity checks before atomic write and full-text processing. For screened keep/maybe candidates, `download_and_backfill()` can recover PDF links from the original HTML page or query Unpaywall by DOI when local `UNPAYWALL_EMAIL` is set. Recovery uses at most three additional literature URLs per paper and one lookup per DOI/run, without recursive crawling. Published, accepted, and submitted copies are distinguished; OA recovery does not change venue policy. Missing configuration and failed candidates retain structured `download_attempts` in papers JSON; failed downloads do not count as successful duplicates. Dry run does not enable these extra recovery requests. See HANDOFF.md for limits and provenance.

`literature_review.llm_evidence.summarize_and_rerank()` is the RCS (contextual-summary/re-ranking) interface, retained as compare/legacy since C2b — the formal run scores the per-paper sampled chunks with a single 1-10 utility instead (`literature_review.functional.score_chunks_functionally`, 4 chunks per batch). Gemini reads `GEMINI_API_KEY` only from the environment; no key is needed for unit tests.

Run the local extraction and retrieval path without any API key:

```powershell
uv run python -m literature_review.pipeline data/papers/example.pdf "literature review agent" --top-k 3 --dry-run
```

After copying `.env.example` to `.env` and setting the required keys, omit `--dry-run` to run the LLM stage. Start with one PDF and `--top-k 2` or `--top-k 3`. Note the `pipeline` CLI is the compare/legacy path (corpus-wide top-k across all papers combined, not per-paper); the formal end-to-end run is `literature_review.main`, which samples chunks per paper.

`literature_review.synthesis` completes the evidence-cited report with three-layer traceability: every claim flows from an `EvidenceChunk` through a per-paper note (`PaperSummary`) into the final report prose, which carries inline `[claim-N]` citation markers (A5: the programmatic `材料來源清單` maps each claim to its source chunks). Per-paper notes are built for papers retained by the formal functional threshold/quota selection; legacy assessments use `include`/`exclude` (`consider` is retired). Report generation first creates a global thematic outline, then writes each section sequentially using only its allocated claims, and finally proposes future directions independently from all claims. With K sections this takes K+2 calls before repair or provider retries. Section schema, missing or disallowed citations, and extra headings share one content-repair attempt. The report JSON preserves `outline` and `report_sections`; old JSON remains readable. Citation-ID checks do not establish factual support or ensure every factual sentence is cited. Future directions come from explicit limitations stated in the retrieved evidence, with a deterministic fallback when none yields a direction.

Run the multi-PDF synthesis flow over a folder of papers (requires `GEMINI_API_KEY`):

```powershell
uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 8
```

`--top-k 8` means 8 chunks across all papers combined in this compare/legacy CLI (not per-paper); the formal end-to-end run (`literature_review.main`) uses section-aware sampling: up to two chunks per paper for scoring and up to nine for notes.

## Run the formal full pipeline

One interactive query produces a literature-review report: plan -> search/rank -> screening -> validated PDF acquisition -> extraction -> functional selection -> notes/report. Defaults include `LIMIT=100`, `TOTAL_TARGET=20`, and functional policy `top_chunks_per_paper=2`, `batch_size=4`, `threshold=6.0`. Screened downloads take all keep papers and backfill with maybe to 20; keep may exceed 20. Only the legacy unscreened path uses `target_n = ceil(20 / query_count)`. Every query uses the last `YEAR_WINDOW=3` years (2026 → 2024) and the top-venue whitelist (`ranking.TOP_VENUE_ALIASES`, 17 venues across ML/NLP/IR/CV/AI).

With `SEMANTIC_SCHOLAR_API_KEY`, search uses Semantic Scholar first; a missing key, empty normalized result, or failed request falls back to OpenAlex. SS placeholder abstracts are backfilled by DOI; papers still lacking abstracts are dropped before ranking. BGE semantic ranking and formal chunk sampling combine original-idea and source-query similarity, with default `idea_weight=0.5` (not a validated optimum). Citation/recency terms remain unchanged. Dry run uses the lexical baseline without loading the encoder or calling LLMs:

```powershell
uv run python -m literature_review.main --dry-run --dest-dir "data/dry_run_$(Get-Date -Format 'yyyyMMdd_HHmmss_fff')"
```

The dry run does not call any LLM provider. The main entry loads only the optional `OPENALEX_API_KEY` from `.env` for authenticated search; `uv --env-file .env` would load all variables instead. Dry run still searches and downloads over the network, so it is not an offline test. Use a new `--dest-dir` to protect previous downloads; the default folder is rebuilt even for dry runs.

The full run plans and screens with one key (default `GEMINI_API_KEY`), performs functional scoring with `GEMINI_API_KEY_2` by default, writes per-paper notes with `GEMINI_API_KEY_2`, and produces the synthesis report with `GEMINI_API_KEY_3`. Use `--scoring-key N` and `--notes-key N` to assign separate keys; when `--scoring-key` is omitted, it follows `--notes-key` for backward compatibility. For example, `--plan-key 2 --scoring-key 3 --notes-key 4 --report-key 5` assigns four key groups. Gemini 503 responses are retried up to three times with exponential backoff (about 15, 30, and 60 seconds plus jitter); if retries are exhausted, the run stops without skipping screening or switching models. Set `SEMANTIC_SCHOLAR_API_KEY` in `.env` to make Semantic Scholar the primary search source (without it, OpenAlex is used directly). Every real run also persists the downloaded-papers list as `data/outputs/papers_%Y%m%d_%H%M%S_%f.json` (one entry per actually-downloaded PDF with full metadata, its originating query, screening priority, and on-disk path, plus a run overview) sharing the timestamp of the companion `report_*.json`; neither file is ever written by a `--dry-run`, and the folder accumulates across runs:

Alternatively, set `GROQ_API_KEY` in `.env` for Groq routing across planning, token-batched screening with a final global gap synthesis, functional scoring, per-paper notes, and report generation. The default model is `openai/gpt-oss-120b`; set `GROQ_MODEL` to choose one model for every Groq stage, or override stages individually with `GROQ_MODEL_PLAN`, `GROQ_MODEL_SCREENING`, `GROQ_MODEL_SCORING`, `GROQ_MODEL_NOTES`, and `GROQ_MODEL_REPORT`. Stage-specific values take precedence over `GROQ_MODEL`. Check the Groq Console for active model IDs, structured-output support, and each model's current quota before a full run. Screening targets at most 3,000 estimated input tokens per batch. All Groq stages share a process-wide TPM tracker that learns from exact response usage and uses provider remaining/reset headers when available, instead of imposing a fixed 61-second delay. TPM 429 errors with a retry hint are retried a bounded number of times; daily quota errors stop the run without switching models automatically. Functional scoring defaults to four chunks per call to reduce oversized requests. Groq logs exact returned prompt/completion/total token usage; preflight estimates are approximate. `uv sync` installs the Groq Python SDK used by the client. Groq HTTP 503 is retried up to three times with exponential backoff (about 15, 30, and 60 seconds plus jitter). Before a Groq run, check the selected model's Groq limits; check Gemini key limits only when Gemini is actually used. With no explicit `LLM_PROVIDER`, Groq is selected when `GROQ_API_KEY` is set; otherwise Gemini is selected. `--dry-run` never calls either model provider.

To use the lab's OpenAI API account instead, set `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, and optionally `OPENAI_MODEL=gpt-5.6-luna` in `.env`; the model value is already the default. OpenAI is routed across planning, screening, scoring, notes, and report. Optional `OPENAI_MODEL_PLAN`, `OPENAI_MODEL_SCREENING`, `OPENAI_MODEL_SCORING`, `OPENAI_MODEL_NOTES`, and `OPENAI_MODEL_REPORT` values override individual stages. The official SDK uses its default API endpoint, so no Base URL is needed. Run the same full-pipeline command below. Before a real run, confirm model access and usage limits in the OpenAI API Usage Dashboard for the key's organization/project. `uv sync` installs the OpenAI SDK. If `LLM_PROVIDER` is blank, legacy automatic routing remains: Groq when `GROQ_API_KEY` is set, otherwise Gemini.

```powershell
uv run --env-file .env python -m literature_review.main --dest-dir "data/run_$(Get-Date -Format 'yyyyMMdd_HHmmss_fff')"
```

Use `--rule-based` to force the deterministic fallback plan; `--dry-run` always uses it, so a dry run needs no LLM key; search authentication may still be needed. Downloads go to `data/run/`, which `main()` rebuilds (removes and recreates) at the start of every default run; pass `--dest-dir <folder>` or set `DEST_DIR` to download into a folder you manage instead (that folder is never cleared). Temporary one-off runs can widen or move the window with `--year-from` / `--year-to` and swap the venue whitelist with `--venues` (conference names matched against `ranking.TOP_VENUE_ALIASES` by key or alias and expanded to that conference's full alias set; unrecognized names are warned and used as raw substring tokens; `none` or an empty string disables the filter; all-unrecognized inputs disable the filter with a warning). Multiple planned queries share one dedup set, so a paper already downloaded by an earlier query counts as satisfied and is never re-downloaded or backfilled.

## Offline tests

After dependencies are installed, run the complete suite without real keys or network:

```powershell
uv run --offline --no-sync python tests/run_offline.py
# Selected modules, with the same isolation
uv run --offline --no-sync python tests/run_offline.py test_main test_pdf_recovery
```

The runner clears provider configuration, blocks network/child processes and the workspace `.env`, and uses a temporary cwd for generated files. It prohibits writes to existing project data. Pairwise CLI tests receive only a short fake key for an unused SDK client. Direct `unittest discover` does not provide these protections. Offline success does not establish real API availability, PDF recovery rate, or LLM semantic quality.

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
