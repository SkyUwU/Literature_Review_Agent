# Task 1A Handoff

## Start here

1. Read `AGENTS.md`.
2. Run `git status` before changing anything.
3. Run the test suite:

   ```powershell
   uv sync
   uv run python -m unittest discover -s tests -v
   ```

As of 2026-08-22, the latest committed work includes the local PDF-to-LLM pipeline. The only expected untracked source PDF is `Summer_Project.pdf`; do not commit it.

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
| LLM contextual-summary/re-ranking contract | `llm_evidence.py` | Done; fake-client tested and live Gemini smoke test succeeded |
| Evidence-based paper assessment | `assessment.py`, `models.py` | Done; deterministic aggregation retains chunk IDs and page ranges |
| Evidence-cited synthesis and future directions | `synthesis.py`, `pipeline.py`, `models.py`, `assessment.py` | Done; three-layer traceability from chunks to report |

The current suite has 74 tests. Do not replace tests with only live API checks.

## Important design decisions

- `SelectedPaperSet` is a candidate set for acquiring/reading full text, not a final literature-review inclusion decision.
- PDFs are local inputs stored in `data/papers/`, which is ignored due to size/copyright. `pypdf` extracts text and preserves source page numbers.
- `EvidenceChunk` can span pages and records `page_start` and `page_end`.
- The first chunk retrieval stage is intentionally cheap and deterministic. It currently ranks lexical matches; an embedding retriever can later replace it without changing the response contract.
- The second stage should be an LLM contextual-summary/re-ranking step. It must receive a bounded set of retrieved chunks, return structured evidence summaries with page references, and then support a paper-level assessment.
- Same-title records are a pragmatic deduplication baseline. The current rule prefers higher citation count, then venue presence, abstract length, then year. It is not proof of identity. Add DOI-based deduplication when DOI metadata is available.
- Evidence supports two distinct perspectives: relevance (how directly a chunk answers the query) and coverage (which topic aspects a paper addresses). Relevance drives retrieval and scoring; per-paper notes capture coverage so both views stay traceable.
- Low-relevance/exclude chunks are NOT gap evidence. Research gaps come only from explicit limitations stated in the retrieved evidence plus subsequent aspect-coverage analysis across papers; an unretrieved or excluded chunk does not prove absence.
- Aggregate scores use shrunk means (the sample mean blended toward a prior score when few chunks support a paper). Caveat: per-paper top-k retrieval allocation gives papers unequal chunk counts, so raw mean comparisons remain partially confounded even after shrinkage.

## Latest milestone: evidence-cited synthesis and future directions

`literature_review.synthesis` implements the evidence-cited synthesis stage with three-layer traceability: every synthesized claim traces from an `EvidenceChunk` through a per-paper `PaperSummary` note to the final report prose.

- `literature_review.pipeline` accepts multiple PDFs or a folder of PDFs in one run (`expand_pdf_inputs()` with order-preserving deduplication) and retrieves top-k chunks corpus-wide across all papers in one LLM call before aggregation. `--dry-run` still performs only local steps and never reads a key or calls an API.
- Because top-k is corpus-wide, a paper whose chunks never enter the top-k receives no assessment, no per-paper notes, and no place in the report (PaperQA2 semantics; the dry-run path always behaved this way).
- Aggregate paper scores use deterministic shrunk means; each retained `PaperAssessment.evidence` item keeps the chunk ID, page range, summary, scores, and original chunk recommendation.
- Per-paper notes are generated only for papers whose aggregated recommendation is include or consider; exclude papers contribute no synthesized claims.
- Future directions are anchored in explicit limitations found in the retrieved evidence, with deterministic convergence and fallback sources. LLM output is validated with `model_validate_json()` against Pydantic schemas; unknown chunk IDs or missing/invalid inline citation markers raise `SynthesisError`.
- The report is direct prose with inline `[chunk_id]` citation markers validated against the supplied evidence set; deterministic and fake-client tests cover the path without a live API call.

## Latest: Langfuse observability (2026-08-31)

Added Langfuse SDK (4.15.1) observability with Plan B tree tracing:
- `@observe(name=...)` added to `rcs` (llm_evidence.py), `llm_call` (GeminiJsonClient.generate_json), `paper_notes` and `synthesis_report` (synthesis.py).
- `pipeline.py` flushes Langfuse telemetry before CLI exit with graceful degradation (Langfuse down / missing keys never blocks the pipeline; dry-run reads no keys).
- Dashboard at `http://localhost:3000` shows per-stage tree traces (rcs / paper_notes / synthesis_report), each wrapping an `llm_call` child layer (Plan B tree structure).
- 74 tests still green; `@observe` is a no-op in the test env (no keys), so no insurance code was needed (YAGNI).
- Requires a self-hosted Langfuse (`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`) and Gemini for the LLM stage; confirm the server is up via `curl http://localhost:3000/api/public/health`.

The next milestone is report assembly driven by search convergence; see the suggested sequence below.

## Windows and WSL/OpenCode handoff

- The Windows folder (`C:\Users\User\Desktop\Literature_Review_Agent`) uses Anaconda/Windows `uv`. OpenCode runs in WSL and should use WSL-native `uv`, not the Windows environment. The user has already installed WSL `uv`; verify it with `uv --version` rather than reinstalling it.
- Recommended one-time WSL project setup:

  ```bash
  mkdir -p ~/projects
  cd ~/projects
  git clone https://github.com/SkyUwU/Literature_Review_Agent.git
  cd ~/projects/Literature_Review_Agent
  uv sync
  ```

- These are two independent Git working copies. Clone only once; do not repeatedly copy or re-clone after every handoff. The private GitHub remote `origin` is now configured. Choose one as the active copy for a milestone, commit there, run `git push`, then run `git pull --ff-only` in the other copy before editing. A configured local bare repository could also serve this role, but do not use either checked-out working directory as an informal bidirectional remote.
- Do not copy `.venv`, PDFs, or uncommitted source files between copies. For a one-time WSL setup on this same private machine, copying the ignored `.env` from Windows is acceptable; alternatively create it from `.env.example`. Never commit, paste, or upload `.env`. If Gemini is needed in WSL, ensure that the WSL clone has its own local `.env`.

Suggested sequence after the evidence-cited synthesis step:

1. report assembly (search convergence);
2. aspect-coverage gap analysis;
3. embedding retriever/GROBID/LLM planner upgrades once the baseline works on real PDFs.

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
Work in this repository on ADSL summer-project Task 1A. Read AGENTS.md and HANDOFF.md first. Inspect git status and preserve user changes. Implement only the current next milestone in HANDOFF.md (report assembly/search convergence). Do not request or print API keys; use .env/environment variables. Keep Pydantic contracts, add tests where needed, run the full test suite, and report the exact PowerShell or WSL commands for the user to commit. Reply in Traditional Chinese and keep explanations concise.
```

## New Codex session starter prompt

Use this prompt when starting a fresh Codex task for this repository. It intentionally delegates details to the tracked documents instead of replaying long chat history:

```text
Continue the ADSL Summer Project Task 1A in C:\Users\User\Desktop\Literature_Review_Agent. Read AGENTS.md and HANDOFF.md before taking action. The Gemini PDF evidence smoke test, provenance-preserving PaperAssessment aggregation, and evidence-cited synthesis with future directions are complete; implement only the current next milestone: report assembly (search convergence). Inspect git status first, do not commit Summer_Project.pdf or data/papers/, add tests, run the full suite, and give exact PowerShell commit commands. Reply concisely in Traditional Chinese. Do not ask for or print API keys.
```
