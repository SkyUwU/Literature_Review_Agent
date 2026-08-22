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

The current suite has 28 tests. Do not replace tests with only live API checks.

## Important design decisions

- `SelectedPaperSet` is a candidate set for acquiring/reading full text, not a final literature-review inclusion decision.
- PDFs are local inputs stored in `data/papers/`, which is ignored due to size/copyright. `pypdf` extracts text and preserves source page numbers.
- `EvidenceChunk` can span pages and records `page_start` and `page_end`.
- The first chunk retrieval stage is intentionally cheap and deterministic. It currently ranks lexical matches; an embedding retriever can later replace it without changing the response contract.
- The second stage should be an LLM contextual-summary/re-ranking step. It must receive a bounded set of retrieved chunks, return structured evidence summaries with page references, and then support a paper-level assessment.
- Same-title records are a pragmatic deduplication baseline. The current rule prefers higher citation count, then venue presence, abstract length, then year. It is not proof of identity. Add DOI-based deduplication when DOI metadata is available.

## Next milestone: aggregate evidence into a paper-level assessment

`literature_review.llm_evidence` contains a provider-agnostic `JsonGenerationClient`, a `GeminiJsonClient`, prompt construction, Pydantic-schema structured output, `model_validate_json()` validation, provenance enrichment, and fake-client tests. A live Gemini smoke test succeeded on `PaperQA2.pdf`, producing page-traceable summaries such as `PaperQA2.pdf-p12-13-c32`.

`literature_review.pipeline` now provides the opt-in CLI flow: local PDF -> extraction -> chunking -> top-k retrieval -> `summarize_and_rerank`. Its `--dry-run` option performs only the local steps and never reads a key or calls an API.

1. Preserve the successful two-stage flow: local lexical top-k chunks -> LLM structured summaries/re-ranking.
2. Add a Pydantic-based aggregation from `EvidenceRerankResponse` to an evidence-based `PaperAssessment`; preserve chunk IDs and page ranges as rationale/provenance.
3. Do not present this as a whole-paper scholarly verdict: it is limited to supplied top-k chunks.
4. Keep deterministic and fake-client tests. Do not require a live API call for tests.

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
Work in this repository on ADSL summer-project Task 1A. Read AGENTS.md and HANDOFF.md first. Inspect git status and preserve user changes. Implement only the current next milestone in HANDOFF.md. Do not request or print API keys; use .env/environment variables. Keep Pydantic contracts, add tests where needed, run the full test suite, and report the exact PowerShell or WSL commands for the user to commit. Reply in Traditional Chinese and keep explanations concise.
```
