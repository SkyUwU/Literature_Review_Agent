# Collaboration Guide

## User and communication

- The user is a new CS master's student working on ADSL summer-project Task 1A, a Literature Review Agent.
- Reply primarily in Traditional Chinese. Keep explanations short and implementation-focused unless the user asks to learn a concept.
- The user uses Windows PowerShell and an Anaconda environment in which `uv` is available. Give PowerShell commands.
- OpenCode may run in WSL. Treat the Windows project and a WSL-native clone as separate Git working copies; synchronize through a shared Git remote (GitHub is recommended but not required), and never edit the same feature in both copies before committing.
- The user prefers progress on the assignment over broad tutorials. Explain only new decisions, errors, and commands they must run.
- Do not ask the user to paste API keys. Keep secrets in a local `.env`, which is ignored by Git.

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
```

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

The LLM contextual-summary/re-ranking interface is implemented and its Gemini PDF smoke test succeeded. See `HANDOFF.md` for the next integration scope and decisions.

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
