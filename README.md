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
