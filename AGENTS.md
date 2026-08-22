# Collaboration Guide

## User and communication

- The user is a new CS master's student working on ADSL summer-project Task 1A, a Literature Review Agent.
- Reply primarily in Traditional Chinese. Keep explanations short and implementation-focused unless the user asks to learn a concept.
- The user uses Windows PowerShell and an Anaconda environment in which `uv` is available. Give PowerShell commands.
- OpenCode may run in WSL. Treat the Windows project and a WSL-native clone as separate Git working copies; synchronize through a shared remote, and never edit the same feature in both copies before committing.
- The user prefers progress on the assignment over broad tutorials. Explain only new decisions, errors, and commands they must run.
- Do not ask the user to paste API keys. Keep secrets in a local `.env`, which is ignored by Git.

## Engineering workflow

- Read `HANDOFF.md` before changing this project. Update it when a material milestone or architectural decision changes.
- Inspect `git status` before editing. Preserve existing user changes and never add `Summer_Project.pdf` or `data/papers/` to Git.
- Make one bounded milestone at a time. Do not combine unrelated refactors, dependencies, or features in one change. Add or update tests, run them, then state the exact `git add`, `git commit`, and `git status` commands.
- Use Pydantic models as the interfaces between stages. Outputs must preserve source provenance, such as provider, paper ID, file path, and page range.
- Avoid claiming that an abstract- or metadata-based score is a full-text scholarly assessment.
- Prefer deterministic, testable baselines before adding LLM/API behavior. Later model-based components must use the same data contracts where practical. For LLM JSON, send the Pydantic JSON schema when the provider supports it and still validate the returned text with `model_validate_json()`.

## Commands

```powershell
uv sync
uv run python -m unittest discover -s tests -v
uv run python -m literature_review.search "literature review agent" --limit 10 --year-from 2024 --rank
uv run python -m literature_review.pipeline data/papers/example.pdf "literature review agent" --top-k 3 --dry-run
```

If Git reports a dubious-ownership error, the user previously resolved it with:

```powershell
git config --global --add safe.directory C:/Users/User/Desktop/Literature_Review_Agent
```

## Current architecture

```text
ResearchIdea -> SearchPlan -> OpenAlex retrieval -> metadata filter/rank/select
-> local PDF extraction -> EvidenceChunk retrieval -> LLM contextual summary/re-rank
-> evidence-backed paper assessment -> synthesis/future directions
```

The LLM contextual-summary/re-ranking interface is implemented but has not made a live API call. See `HANDOFF.md` for the next integration scope and decisions.
