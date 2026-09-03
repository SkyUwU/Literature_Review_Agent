# retrieval-quality-analysis - Work Plan

## TL;DR (For humans)

**What you'll get:** A reproducible tool that runs your 3 real PDFs through BOTH the current keyword-based search (lexical top-k) and a new meaning-based search (embedding top-k), asks the LLM to score how relevant each found passage is, and tells you which search method finds more relevant passages. The output is a clear report you can read to decide whether switching to meaning-based search is worth it.

**Why this approach:** You don't have hand-labeled data, so the LLM acts as the judge. Scoring each method's results in a separate LLM call avoids the LLM favoring whichever method is listed first, so the comparison is fair. We use a small free local model (bge-small-en-v1.5) so no paid API or new account is needed.

**What it will NOT do:** It will not secretly switch the system to embedding search — it only measures and compares. It will not change how papers are scored. It will not touch the existing retrieval/pipeline code. It will not change the paper-level scoring formula (that is recorded as a future idea, deliberately deferred).

**Effort:** Medium
**Risk:** Low - adds one dependency (sentence-transformers), no change to existing pipeline core; the only real unknown is running the LLM judge (needs GEMINI_API_KEY) and the local model download.

**Decisions to sanity-check:** (1) BGE query prefix is required for a fair embedding comparison; (2) separate LLM call per method to avoid order bias, and any empty group (no unique chunks) is skipped with no LLM call; (3) overlapping chunks scored once, unique ones per group; (4) verdict rule is explicit: average-relevance difference >= 0.5 → winner, < 0.5 → comparable; (5) scope is compare-only, adoption is a later decision; (6) the CLI's first real run downloads ~130MB bge-small weights (tests never do, they use a fake encoder).

Your next move: approve this plan (already drafted), then execute it in a separate worker session (e.g. `$start-work`). Full execution detail follows below.

---

> TL;DR (machine): Medium effort, Low risk, deliverables = semantic top-k retriever + compare/eval tool + JSON report + tests; adds sentence-transformers; compare-only, no pipeline change.

## Scope
### Must have
- New module `literature_review/embedding_retriever.py`: uses `sentence-transformers` + `BAAI/bge-small-en-v1.5` to rank all chunks by semantic similarity to the query and return a top-k (mirroring `retrieve_evidence`'s `EvidenceRetrievalResponse` shape so it is directly comparable). Query gets the official BGE prefix `"Represent this sentence for searching relevant passages: "`; document chunks do NOT.
- New module `literature_review/retrieval_eval.py`: (a) run lexical `retrieve_evidence` top-k and embedding top-k over the same chunks/query/top_k; (b) emit a separate LLM judge call per method (same model, same prompted, same score format) using the existing `JsonGenerationClient`; score overlapping chunk IDs once; (c) compute per-method average relevance, overlap count, unique-to-each chunk IDs, and a verdict; (d) serialize a JSON report.
- Add `sentence-transformers` dependency via `uv add`.
- CLI entry to run the eval over `data/papers` with a query and `--top-k` (like existing pipeline CLI pattern `python -m literature_review.xxx`).
- Deterministic unit tests (new `tests/test_embedding_retriever.py`, `tests/test_retrieval_eval.py`) using a fake LLM client (no key) plus an injectable/fake vector encoder so no model download is needed in tests.
- JSON report output (in-program rendering; reuse `LlmEvidenceAssessment` for per-chunk scores; no forced new formal report model class).

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do NOT modify `evidence_ranking.py` / `retrieve_evidence` scoring logic (no refactor, no shared-score abstraction).
- Do NOT modify `assessment.py` paper-score formula (chunk-support/paper-length) — deferred (recorded in architecture-observations.md).
- Do NOT formally adopt or replace the lexical retriever; this milestone only compares.
- Do NOT change `summarize_and_rerank` / RCS / pipeline core behavior.
- Do NOT implement random sampling of unselected chunks in this version (deferred).
- Do NOT touch `ranking.py`, `selection.py`, `planning.py`, `synthesis.py`.
- Do NOT add heavier models (bge-m3, gte-large, etc.) — only bge-small-en-v1.5.
- Do NOT require/ask for any new API key or account for embedding (local model); LLM judge still needs existing `GEMINI_API_KEY` from `.env`/env (loaded via existing `load_local_env`).
- Do NOT commit `data/papers/` or `.env`; do NOT print keys.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD (write tests alongside each module) + framework `unittest`.
- Evidence: `.omo/evidence/` with files `task-<N>-retrieval-quality-analysis.log` per todo.
- Run: `uv run python -m unittest discover -s tests -v` (expect 74 existing + new tests green, no network / no key for tests).

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Do not over-split; keep each todo one bounded, committable unit.
- Wave 1: dependency + embedding retriever + its tests.
- Wave 2: eval tool + its tests + CLI.
- Wave 3: full-suite run, real data smoke (with key, if user opts in), HANDOFF/AGENTS updates.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (dependency) | none | 2 | none |
| 2 (embedding_retriever+tests) | 1 | 3 | none |
| 3 (eval tool+tests+CLI) | 2 | 4,5 | none |
| 4 (full suite) | 3 | 5 | none |
| 5 (docs/AGENTS/HANDOFF) | 3 | - | 4 |

## Todos
> Implementation + Test = ONE todo. Never separate.
- [ ] 1. Add `sentence-transformers` dependency
  What to do / Must NOT do: Run `uv add sentence-transformers` in the project root so it is recorded in `pyproject.toml` and `uv.lock`. Do NOT pin an arbitrary old version; use the latest resolvable. Do NOT install global/pip outside uv.
  Parallelization: Wave 1 | Blocked by: none | Blocks: Todo 2
  References (executor has NO interview context - be exhaustive): AGENTS.md `Commands` (uv sync); HANDOFF.md setup notes.
  Acceptance criteria (agent-executable): `uv run python -c "import sentence_transformers; print(sentence_transformers.__version__)"` succeeds; `pyproject.toml` contains `sentence-transformers`.
  QA scenarios (name the exact tool + invocation): happy = import succeeds; failure = a bad pin would not import. Evidence `.omo/evidence/task-1-retrieval-quality-analysis.log`.
  Commit: Y | chore(deps): add sentence-transformers

- [ ] 2. New module `embedding_retriever.py` (semantic top-k) + tests
  What to do / Must NOT do: Implement `retrieve_evidence_embedding(chunks, query, policy, encoder)` returning an `EvidenceRetrievalResponse`-shaped object (reuse `RetrievalPolicy`, `RankedEvidenceChunk`, `EvidenceRetrievalResponse` from models.py) ranking ALL chunks by cosine similarity of query-vector vs chunk-vector, capped at `top_k`. The query string is prefixed with `"Represent this sentence for searching relevant passages: "`; chunk text is NOT prefixed. Use `sentence_transformers.SentenceTransformer("BAAI/bge-small-en-v1.5")` and `normalize_embeddings=True` so dot-product equals cosine. Accept an injected `encoder` callable (encode(list[str])->list[vectors]) so tests can fake it without downloading the model. Do NOT modify `evidence_ranking.py`. Do NOT add the prefix to document chunks. Clarify the two ways an encoder is provided: (a) tests pass a fake encoder callable directly (no model download); (b) the CLI / runtime builds a real `SentenceTransformer("BAAI/bge-small-en-v1.5")` as the DEFAULT — define a module-level `default_encoder()` factory that constructs it, and have `retrieve_evidence_embedding` default its `encoder` param to that factory when not supplied, so callers that do not inject one still work in real runs.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 3
  References: `evidence_ranking.py:12-46` (shape to mirror); `models.py:143-164` (RankedEvidenceChunk/EvidenceRetrievalResponse/EvidenceRetrievalPolicy); BGE card: query prefix.
  Acceptance criteria (agent-executable): `tests/test_embedding_retriever.py` passes, asserting (a) top-k ordering follows a fake encoder's similarity, (b) query passed to encoder carries the official prefix, (c) chunk text passed to encoder does NOT carry the prefix, (d) response uses existing `EvidenceRetrievalResponse` model.
  QA scenarios: happy = fake encoder returns fixed vectors, assert ordering & prefix; failure = encoder missing → clean error. Evidence `.omo/evidence/task-2-retrieval-quality-analysis.log`.
  Commit: Y | feat(retrieval): add embedding semantic top-k retriever

- [ ] 3. New module `retrieval_eval.py` (compare + separate LLM judge + report) + tests + CLI
  What to do / Must NOT do: Implement `compare_retrieval(chunks, query, retrieval_policy, client, encoder)` that: (1) runs lexical `retrieve_evidence(chunks, query, retrieval_policy)` to get top-k A; (2) runs `retrieve_evidence_embedding(...)` to get top-k B; (3) computes overlap `A∩B`, `A−B`, `B−A`; (4) issues SEPARATE LLM judge calls: one for `A−B` unique chunks, one for `B−A` unique chunks, one for the shared overlap (reuse the existing `build_evidence_prompt`/assessment flow against `JsonGenerationClient` so each chunk gets an `EvidenceSummary` with relevance_score); (5) computes per-method average relevance over that method's scored chunks, overlap count, unique counts, and a verdict string; (6) returns a JSON-serializable report dict. EMPTY-GROUP GUARD: if any of `A−B`, `B−A`, or overlap is an EMPTY list, SKIP that LLM call entirely (do NOT send a zero-chunk prompt); compute averages only over the groups that actually have chunks, and note any skipped group in the report. VERDICT RULE (explicit, no guessing): compare `avg_relevance_embedding` vs `avg_relevance_lexical`; if the absolute difference is >= 0.5 → that method is the winner ("embedding better" / "lexical better"); if < 0.5 → "comparable". If one method had NO scored chunks at all, mark its average as null and base the verdict on the method that has data, flagging the missing one. CLI: `python -m literature_review.retrieval_eval <pdf_or_folder> "<query>" --top-k N [--judge-model M]` where `--judge-model` is the LLM used to judge (default `gemini-2.5-flash`); the embedding model is always `BAAI/bge-small-en-v1.5` (created via the `default_encoder()` from Todo 2) and is NOT selectable via CLI in this milestone. The CLI builds chunks via existing `extract_pdf_text`/`chunk_document`, runs the compare, and prints the report JSON. Do NOT modify `retrieve_evidence`, `summarize_and_rerank`, or pipeline core. Do NOT sample unselected chunks. Overlapping chunk IDs are scored ONCE (shared call), not twice.
  Parallelization: Wave 2 | Blocked by: 2 | Blocks: 4,5
  References: `llm_evidence.py:79-99` (build_evidence_prompt), `:26-31` (JsonGenerationClient), `:111-128` (validate), `models.py:167-197` (EvidenceSummary/LlmEvidenceAssessment), `pipeline.py:49-62` (pattern for PDF → chunks), `pipeline.py:155-200` (CLI pattern, `--dry-run`, `--model`).
  Acceptance criteria (agent-executable): `tests/test_retrieval_eval.py` passes asserting (a) separate LLM call count: exactly one call per non-empty group (unique-A, unique-B, overlap), and ZERO calls for any empty group; (b) overlap scored once, not twice; (c) average & verdict computed correctly from injected fake scores, including the empty-group skip path and the verdict threshold (diff >= 0.5 → winner; < 0.5 → comparable); (d) report JSON has required keys (overlap, only_lexical, only_embedding, avg_relevance_lexical, avg_relevance_embedding, verdict) and correctly records any skipped group. CLI runs on a temp minimal PDF (or injected docs) and prints valid JSON.
  QA scenarios: happy = fake client with known scores → assert verdict; failure = client raises / malformed → clean error message. Evidence `.omo/evidence/task-3-retrieval-quality-analysis.log`.
  Commit: Y | feat(eval): add lexical-vs-embedding retrieval comparison tool

- [ ] 4. Full test suite + real-data smoke (optional LLM)
  What to do / Must NOT do: Run `uv run python -m unittest discover -s tests -v` and confirm all tests green (74 existing + new). Optionally (requires GEMINI_API_KEY + data/papers present on the executing environment) run `uv run python -m literature_review.retrieval_eval data/papers "<query>" --top-k 8 --judge-model <same model used for judge>` and confirm it prints a sensible JSON report (embedding/lexical averages + verdict). NOTE: the FIRST real run that constructs the bge-small encoder will trigger a one-time model weight download (~130MB) from Hugging Face — requires network; the tests themselves never download the model (they use a fake encoder). Do NOT commit data/papers or .env; do NOT print keys. If no key or no PDFs in WSL, record the smoke as skipped with reason rather than forcing.
  Parallelization: Wave 3 | Blocked by: 3 | Blocks: 5
  References: AGENTS.md Commands (test suite command).
  Acceptance criteria (agent-executable): test suite exits 0 with all tests passing; smoke (if run) exits 0 and writes a report file to `.omo/evidence/` or stdout capture.
  QA scenarios: happy = suite green + smoke report; failure = any broken import/test surfaced. Evidence `.omo/evidence/task-4-retrieval-quality-analysis.log`.
  Commit: N (unless smoke reveals fixes → forward to a fix commit) | (commit only if fixes needed)

- [ ] 5. Update AGENTS.md Commands + HANDOFF.md milestone note
  What to do / Must NOT do: In AGENTS.md `Commands` block, add a line documenting the eval CLI (e.g. `uv run python -m literature_review.retrieval_eval data/papers "literature review agent" --top-k 8 --judge-model gemini-3.6-flash`). In HANDOFF.md, add a short "Latest milestone: lexical-vs-embedding retrieval comparison" note (files added, dependency added, compare-only scope, verdict-driven next step). Do NOT paste/commit keys; do NOT touch other AGENTS.md/HANDOFF.md content.
  Parallelization: Wave 3 | Blocked by: 3 | Blocks: -
  References: AGENTS.md `Commands` section; HANDOFF.md "Latest milestone" area.
  Acceptance criteria (agent-executable): diff shows only the intended additions in the two files; tests still pass.
  QA scenarios: happy = diff correct; failure = accidental edits caught by diff review. Evidence `.omo/evidence/task-5-retrieval-quality-analysis.log`.
  Commit: N (docs; user commits) | (assess: docs on the same commit as Todo 3, or separate)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Scope fidelity

## Commit strategy
- Commit each Todo as listed (Y/N markers). The user runs all git commands (per AGENTS.md the executor does not run git; WSL may lack credentials — user commits on Windows or after `git pull --ff-only` in WSL). Sequence: Todo 1 → 2 → 3 (the functional code) can be one logical change set, then Todo 5 docs, Todo 4 is verify-only (commit only if a fix is needed). Final push by user. Do NOT commit `data/papers/`, `.env`, `Summer_Project.pdf`.

## Success criteria
- `uv run python -m unittest discover -s tests -v` → all green (74 + new), no network/key needed for tests.
- `retrieval_eval` CLI runs over real PDFs (where PDFs+key exist) and emits a valid JSON report with: overlap, only_lexical, only_embedding, avg_relevance_lexical, avg_relevance_embedding, verdict.
- Verdict gives the user an evidence-based "embedding better / lexical better / comparable" decision.
- No existing pipeline/assessment/ranking core file modified (filesAdded: embedding_retriever.py, retrieval_eval.py, 2 test files; dependency added).
- AGENTS.md and HANDOFF.md updated to document the milestone.
- User reviews the report and then decides (future milestone) whether to formally adopt embedding — this milestone does NOT adopt it.
