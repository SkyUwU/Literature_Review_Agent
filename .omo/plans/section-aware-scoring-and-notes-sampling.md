# Section-aware scoring and per-paper notes sampling

## Summary

Use section classification before both functional scoring and per-paper notes. For each paper, embedding selects one Method chunk and one Results chunk for functional scoring. Notes receive a broader but bounded evidence set: one Abstract/context chunk plus up to two chunks from each of Method, Evaluation Setup, Results, and Limitations/Future Work (maximum nine chunks per paper).

The goal is to retain evidence from the important parts of each paper while reducing irrelevant input and Groq note tokens. Chunk IDs, original headings, and page ranges remain the provenance source.

## Decisions and behavior

- Canonical section groups and heading aliases (case-insensitive, trim optional numeric prefixes):
  - `context`: Abstract, Introduction, Background, Related Work, Literature Review, Motivation, and Problem Statement. Notes select exactly one Abstract chunk when available; otherwise they select the most relevant context chunk.
  - `method`: Method(s), Methodology/Methodologies, Approach, Proposed Method/Approach, Framework, Architecture, System, and Model.
  - `evaluation_setup`: Experimental Setup, Evaluation Setup, Dataset(s), Data, Benchmark(s), Baseline(s), Metric(s), Evaluation Protocol, and Implementation Details.
  - `results`: Result(s), Finding(s), Experiment(s), Evaluation, Ablation, Error Analysis, Discussion, and Conclusion. A generic `Experiments` heading maps here, as requested; explicit setup headings map to `evaluation_setup`.
  - `limitations_future`: Limitation(s), Threats to Validity, Future Work, and Future Directions.
- When a broad `Experiments` or `Evaluation` heading is present, classify a chunk as `evaluation_setup` only if its text contains a setup cue (`dataset`, `baseline`, `metric`, `protocol`, `hyperparameter`, `implementation detail`, or `training setup`); otherwise keep it in `results`. Heading aliases take precedence over text cues. Unknown headings map to `other`.
- Remove references and acknowledgments before classification and selection. Keep Appendix chunks only when their subsection heading can be mapped to one of the canonical groups; drop unclassified appendix material.
- For functional scoring, embed and rank candidates within Method and Results separately; select the top one from each. If either class is absent, fill the unused slot with the highest-ranked eligible non-noise chunk. Never send the same chunk twice.
- For per-paper notes, select one Abstract/context chunk, then up to two chunks from each of Method, Evaluation Setup, Results, and Limitations/Future Work. If an important category lacks candidates, use the remaining eligible `other` body chunks by embedding rank to fill unused slots, without exceeding nine total. If no Abstract chunk exists, use the highest-ranked context chunk for its slot.
- Use the paper's originating search query for embedding rank, falling back to the main research idea as the current sampler does. Encode each paper's chunks once and reuse those vectors for scoring and notes selection; category filtering and ranking add no extra embedding-model pass.
- Preserve the selected original `EvidenceChunk` objects and their IDs, section paths, and page ranges. Do not change report schemas or citation assembly.
- At the default 250-word chunk size, the notes input is capped at nine chunks (about 2,150–2,300 words including a typical Abstract). A rough prompt estimate is 3,000–4,500 tokens per paper before completion tokens; at the existing 2,500-token Groq notes batch budget this will usually take about two requests. Record actual provider token counts in run logs rather than treating the estimate as a guarantee.

## Implementation and acceptance

1. Extend the shared section canonicalization/classification used by the formal pipeline. Match the aliases listed above, tolerate numeric prefixes and Appendix letter/number subsection prefixes (for example `Appendix > A.1 Additional Results`), and use only the listed bounded setup cues for broad headings; do not add an LLM classification call.
2. Refactor per-paper embedding preparation so it retains chunk vectors and ranking data for both selection stages. Keep the existing functional scoring prompt and scoring policy unchanged; only its sampled chunk set changes.
3. Build a bounded notes-input set using the rules above and pass that set through the existing notes batching/checkpoint path. Notes claims must continue to cite only supplied chunk IDs, and report material links must resolve to the original paper and page ranges.
4. Add focused unit coverage for heading aliases and precedence, references/acknowledgments exclusion, eligible versus unclassified Appendix chunks, per-paper selection counts, missing-category fallback, no duplicate chunks, vector reuse, and citation provenance. Then run the relevant tests and full suite; do not run a real external-provider pipeline as part of this milestone.
5. Update `AGENTS.md`, `HANDOFF.md`, and `README.md` to distinguish scoring's two selected chunks from notes' up-to-nine chunks, and document the 1 + 4×2 policy and its token estimate.

## Implementation status

- Implemented section classification and a shared per-paper embedding pass for scoring and notes selection.
- Updated the formal pipeline and project handoff documents.
- Focused verification passed (78 tests across section classification, functional sampling, and pipeline integration).
- The full suite was stopped after detecting that `test_main` loads local `.env` and starts real Semantic Scholar/Groq requests; no further full-suite retry was made. A real provider run is outside this milestone.

## Constraints and review note

- This is one bounded milestone. Do not modify retrieval policy, paper-level ranking, report schemas, model/provider configuration, or legacy comparison paths.
- Preserve existing user changes in the working tree, including the current Groq model configurability edits and the untracked `Summer_Project.pdf`.
- The `plan-review` skill is not available in this environment. This plan was checked against the current pipeline and existing section-classification/notes behavior, but no skill-based review is claimed.
