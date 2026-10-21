# Claim verifier: SOLIQUA case (untuned demonstrator)

Checks each promotional claim against the documents it cites and returns `supported`, `overstated` or
`cannot_verify`. Every verdict comes with a literal evidence quote and page, the four checks, and any
numbers recomputed in Python. Two rules hold throughout: the LLM reads and labels, and code verifies and
decides. When the pipeline is unsure, it abstains (fails closed).

## Setup and run (5 commands)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then set GOOGLE_CLOUD_PROJECT (Vertex, after `gcloud auth application-default login`) or GEMINI_API_KEY
python verify.py data/claims.json
python eval.py
```

- **First run:** each PDF is ingested once. Pages without a usable text layer go through OCR (about 1–2 minutes in total), and the results are cached in `corpus/`.
- **Runtime configuration:** `verify.toml` holds the pinned models, the submission scope (`claim_and_referenced_panels`), the paths and the versions. It is loaded automatically, and the effective values are written to `outputs/run_meta.json`.
- **Outputs:**
  - `outputs/predictions.json`: the assignment fields plus documented extensions;
  - `outputs/llm_log.jsonl`: every LLM call, with full prompts, parameters and raw responses;
  - `outputs/run_meta.json`.

  The committed `predictions.json`, `llm_log.jsonl` and `run_meta.json` all come from one run (the same `run_id`). Earlier development runs, including the failures described in WRITEUP.md, are kept in `outputs/dev_history/`.
- **Exit codes:**
  - `0`: ok;
  - `1`: bad input file;
  - `2`: no usable credentials (every claim is then `cannot_verify`, `operational_status=auth`);
  - `3`: internal error (traceback in `outputs/errors.log`, never printed).
- **Tests:** run `pytest`. Tests marked `live` call the real API and are skipped when there are no credentials.

## How it works (docs/plan.md §5)

1. **Ingest.** PDFs are never modified. Unusable regions are OCR'd with RapidOCR, with each region linked to its crop. A region counts as unusable if it is a raster without a text layer, or if it contains glyphs from mis-encoded symbol fonts.
2. **Resolve citations.** Exact file names, DOIs and aliases are tried first. A fuzzy match is accepted only above a score threshold, with a margin over the runner-up, and with a compatible year.
3. **Bind panels** (only when the scope allows it and the claim names panels). Panels are bound through the document-level panel registry in `data/materials.json`. A panel's assertions are checked against the documents its registry entry lists as `sources`.
4. **Interpret.** The LLM turns the claim into atomic assertions. Code drops any number that isn't in the claim, and blocks the claim if one of its numbers isn't covered by an assertion.
5. **Retrieve.** BM25 by meaning, plus pages containing the claimed numbers, plus figure and table pages.
6. **Extract.** The LLM quotes evidence and links claimed numbers to facts. Code checks that every quote appears verbatim on the stated page. Values, signs and operators are always re-read from the source text.
7. **Visual reading.** Decisive values are read from rendered crops, via three paths: a reliable text layer, an OCR crop, or abstain.
8. **Compute.** Python does all the arithmetic: rounding intervals in both directions, fold claims, inequality entailment, and unit rules.
9. **Judge.** One LLM call gives each assertion a logical relation (entailed / contradicted / unknown) and separate policy flags.
10. **Policy.** Code applies two-layer Kleene logic and the gates, and decides the verdict.

## Repository

| Path | Contents |
|---|---|
| `verify.py`, `eval.py` | Assignment entry points |
| `verifier/` | Pipeline modules and versioned prompts (`verifier/prompts/`) |
| `papers/` | Supplied sources, renamed to the brief's names, unmodified |
| `data/claims.json`, `data/gold.json` | Claims; proposed gold with literal, located spans (built by `scripts/build_gold.py`) |
| `data/materials.json` | Runtime manifest: documents, evidence families, LBL panel registry |
| `data/holdout/` | Template and new-paper candidates for the sealed holdout (not yet written; the user or reviewer writes them, then the pipeline runs on them once) |
| `annotation/` | Annotation protocol v1 and schema for the learning pilot |
| `scripts/` | Annotation validation and agreement tools, Label Studio smoke test, model smoke test, fixtures |
| `docs/plan.md`, `NOTES.md` | Design and decisions (cited in code as "plan §…"); development journal |
