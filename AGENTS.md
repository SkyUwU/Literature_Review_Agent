# Collaboration Guide

## User and communication

- The user is a new CS master's student working on ADSL summer-project Task 1A, a Literature Review Agent.
- Reply primarily in Traditional Chinese. Keep explanations short and implementation-focused unless the user asks to learn a concept.
- The user works in Windows PowerShell (Anaconda `uv`) and OpenCode may run in a WSL clone. Give PowerShell commands.
- Windows and WSL are independent Git working copies; sync through a shared remote (GitHub recommended) and never edit the same feature in both before committing.
- The user prefers progress on the assignment over broad tutorials. Explain only new decisions, errors, and commands they must run.
- Never ask the user to paste API keys. Keep secrets (Gemini, Langfuse) in a local `.env`, which is ignored by Git.

## Engineering workflow

- Read `HANDOFF.md` before changing this project. Update it when a material milestone or architectural decision changes.
- Inspect `git status` before editing. Preserve existing user changes and never add `Summer_Project.pdf` or `data/papers/` to Git.
- Make one bounded milestone at a time. Do not combine unrelated refactors, dependencies, or features in one change. Add or update tests, run them, then state the exact `git add`, `git commit`, and `git status` commands.
- Use Pydantic models as the interfaces between stages. Outputs must preserve source provenance, such as provider, paper ID, file path, and page range.
- Avoid claiming that an abstract- or metadata-based score is a full-text scholarly assessment; a chunk-based synthesis is likewise not a whole-paper review.
- Prefer deterministic, testable baselines before adding LLM/API behavior. Later model-based components must use the same data contracts where practical. For LLM JSON, send the Pydantic JSON schema when the provider supports it and still validate the returned text with `model_validate_json()`.

## Commands

```powershell
uv sync
uv run python -m unittest discover -s tests -v
uv run python -m literature_review.search "literature review agent" --limit 10 --year-from 2024 --rank
uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 8 --dry-run
uv run python -m literature_review.pipeline data/papers "literature review agent" --top-k 8 --model gemini-3.6-flash
uv run python -m literature_review.retrieval_eval data/papers "literature review agent" --top-k 8 --judge-model gemini-3.6-flash
uv run python -m literature_review.pairwise_eval data/papers --top-k 16 --judge-model gemini-3.6-flash
```

Langfuse observability: confirm the self-hosted server is up with `curl http://localhost:3000/api/public/health` before a full run; traces appear in the dashboard at `http://localhost:3000`.

If Git reports a dubious-ownership error, the user previously resolved it with:

```powershell
git config --global --add safe.directory C:/Users/User/Desktop/Literature_Review_Agent
```

## Current architecture

```text
ResearchIdea -> SearchPlan -> OpenAlex retrieval -> metadata filter/rank/select
-> local PDF extraction (multi-PDF) -> EvidenceChunk retrieval -> LLM contextual summary/re-rank
-> evidence-backed paper assessment -> per-paper coverage notes (include/consider)
-> synthesis report with inline citations / future directions
```

The pipeline now retrieves evidence with semantic (embedding) ranking by default — `literature_review.embedding_retriever.retrieve_evidence_embedding` replaced the lexical baseline for `pipeline.py`; the lexical `evidence_ranking.retrieve_evidence` is retained as compare/legacy only. The LLM contextual-summary/re-ranking interface and Langfuse observability (tree tracing on every LLM call, flushed before CLI exit) are implemented.

The `SearchPlan` contract is now query-only: `SearchPlan.idea` is optional and `planning.create_rule_based_plan(query)` is the deterministic fallback, while `planning.create_llm_plan(query, client)` produces a validated plan (`generated_by="llm"`) using the shared `llm_evidence.generate_validated` call-once-parse-repair helper. See `HANDOFF.md` for the next integration scope and decisions.

## Terminology

- Chunk（證據段落）: one overlapping text span extracted from a local PDF, identified as `{paper_id}-p{start}-{end}-c{n}` and carrying its inclusive page range.
- 證據摘要 / evidence summary: the RCS output for one chunk — a contextual summary plus relevance/quality scores plus page references, validated against Pydantic schemas.
- 覆蓋包 / coverage pack: the bounded set of chunks assembled for a single paper as input to per-paper note generation.
- RCS: the LLM contextual-summary/re-ranking stage implemented by `summarize_and_rerank()`; shorthand for its summaries-and-scores output.
- 筆記主張 / PaperSummaryClaim: one claim inside a single paper's note, citing a `ChunkReference` back to source chunks.
- 逐篇筆記 / PaperSummary（per-paper notes）: structured contribution/method/experiments/results/limitations claims for one paper.
- 綜合報告 / synthesis report: direct prose with inline `[chunk_id]` citation markers plus structured future directions.
- 未來方向 / FutureDirection: an evidence-anchored research direction carrying its own provenance.
- 收縮平均 / shrunk mean: aggregate score blending the sample mean toward a prior when few chunks support a paper (`floor((n*mean + m*prior)/(n+m) + 0.5)`).
- include/consider/exclude（推薦等級）: recommendation levels; only include/consider papers receive per-paper notes and enter the synthesis report.
- bounded evidence（有限證據集）: every generated claim may rely only on the supplied chunk set, never on external knowledge.
