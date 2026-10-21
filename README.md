# Claim verifier: SOLIQUA take-home

Checks each promotional claim against the documents it cites and returns `supported`, `overstated` or
`cannot_verify`. Every verdict comes with a literal evidence quote and its page, the four checks, and any
numbers recomputed in Python. The LLM (Gemini) reads and labels; Python checks the evidence, does all the
arithmetic and decides. When anything is uncertain, the claim becomes `cannot_verify` (fail closed).

## Requirements

- Python 3.12 on macOS or Linux.
- **One of these credentials:**
  - **Vertex AI:** a Google Cloud project with Vertex AI enabled, plus the `gcloud` CLI;
  - **Gemini API:** a Gemini API key.

## Setup and run (5 commands)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # then fill in .env (see Credentials)
python verify.py data/claims.json  # writes outputs/predictions.json and outputs/llm_log.jsonl
python eval.py                     # accuracy, confusion matrix, false-supported count
```

On the first run each PDF is read once. Pages without a usable text layer are OCR'd (about 1–2 minutes in
total) and cached in `corpus/`, so later runs skip this step.

### Credentials (`.env`)

- **Vertex AI (recommended):**
  1. Run `gcloud auth application-default login`.
  2. In `.env`, keep `GOOGLE_GENAI_USE_VERTEXAI=true` and set `GOOGLE_CLOUD_PROJECT` to your project ID.
     `GOOGLE_CLOUD_LOCATION` defaults to `global`.
- **Gemini API:** delete the three Vertex lines from `.env` and set `GEMINI_API_KEY`.

**If credentials are missing or have expired,** `verify.py` says so, marks every claim `cannot_verify`
(`operational_status: auth`) and exits with code 2. It never prints a traceback. Vertex logins expire under
some organisation policies, so run the `gcloud` command again shortly before a live session.

## Running new claims

1. **Write the claims file** as a JSON list in the same format as `data/claims.json`:

   ```json
   [
     {
       "claim_id": "N1",
       "text": "Superior HbA1c reduction with iGlarLixi vs premix insulin",
       "population": "T2DM suboptimally controlled on basal insulin",
       "molecule": "iGlarLixi vs BIAsp 30",
       "cited_paper": "papers/SoliMix_Rosenstock2021.pdf"
     }
   ]
   ```

   `claim_id` and `text` are required. `cited_paper` can be a file name, a DOI, a short name such as
   "SoliMix" or "Rosenstock 2021", or a title. These are matched against `data/materials.json`. A citation
   that does not match is never guessed; the claim becomes `cannot_verify`.
2. **Start a fresh log if you will hand over the log,** because `verify.py` appends to
   `outputs/llm_log.jsonl`:

   ```bash
   mv outputs/llm_log.jsonl outputs/llm_log.previous.jsonl
   python verify.py path/to/new_claims.json
   ```

   This overwrites `outputs/predictions.json` and `outputs/run_meta.json`. Every log line carries the run's
   `run_id`, which also appears in `run_meta.json`.
3. **Add any new paper before running:**
   - copy the PDF into `papers/`;
   - add an entry under `documents` in `data/materials.json` with `doc_id`, `path`,
     `"doc_type": "trial_publication"`, `title`, `year`, `doi` and `aliases` (the short names claims use).

   No code or prompt changes are needed.
4. **Score against your own answers:** run
   `python eval.py --pred outputs/predictions.json --gold path/to/gold.json`. The gold file uses the same
   format as `data/gold.json`.

## Outputs

| File | Contents |
|---|---|
| `outputs/predictions.json` | Per claim: `claim_id`, `verdict`, `checks` (figure, population, drug_indication, actually_stated), `evidence_page`, `evidence_span`, `computed` and `reason`, plus extensions: `evidence_records`, `text_verdict`, `panel_findings`, `abstention_type`, `operational_status` |
| `outputs/llm_log.jsonl` | One line per LLM call: full prompt, parameters, raw response, model, latency and `run_id`. Never credentials. |
| `outputs/run_meta.json` | The effective configuration of the run, including models, scope and versions |
| `outputs/dev_history/` | `eval.py` output from earlier development runs, cited in WRITEUP.md |

The committed `predictions.json`, `llm_log.jsonl` and `run_meta.json` all come from the submitted run
(`run_id` fece52f9db9b).

**Exit codes:** `0` ok; `1` bad input file; `2` no usable credentials; `3` internal error, with the traceback
written to `outputs/errors.log`.

## Configuration (`verify.toml`, loaded automatically)

| Setting | Meaning |
|---|---|
| `[run] scope` | `claim_and_referenced_panels` checks the claim text plus any promo panels it names, using the panel registry in `data/materials.json`. `claim_only` checks the text alone. |
| `[run] max_workers`, `claim_timeout_s` | Claims processed in parallel (default 4), and the per-claim time limit (600 s) |
| `[models]` | Model per stage (extraction `gemini-3.8-flash`, judge `gemini-4-argon`), thinking level, retries, request timeout |
| `[numeric] fold_tolerance`, `fold_abstain_band` | Rule for "~N×" claims. With the default 0.10 and 0.02, the claim is accepted when the computed ratio is within 8% of N, abstains between 8% and 12%, and is a mismatch beyond 12%. |
| `[ingest] ocr_dpi` | OCR resolution for pages without a usable text layer |

## How it works

1. **Ingest.** PDFs are never modified. Raster regions and regions with mis-encoded symbol glyphs are OCR'd
   with RapidOCR, and each OCR record is tied to its image crop.
2. **Resolve citations.** File names, DOIs and aliases are tried first. A fuzzy title match needs a minimum
   score, a margin over the runner-up and a compatible year.
3. **Bind panels.** Only when the scope allows and the claim names panels. Each panel's assertions are checked
   against the documents its registry entry lists as `sources`.
4. **Interpret.** The LLM splits the claim into atomic assertions. Code drops any number that isn't in the
   claim, and blocks the claim if one of its numbers isn't covered.
5. **Retrieve.** BM25 picks candidate pages, plus pages containing the claimed numbers and figure or table
   pages.
6. **Extract.** The LLM quotes evidence and links claimed numbers to facts. Code checks that every quote
   appears verbatim on the stated page, and always re-reads values, signs and operators from the source text.
7. **Visual check.** Decisive values are re-read from a rendered crop. The crop is compared against the text
   layer or OCR, and the pipeline abstains if they disagree.
8. **Compute.** Python does all the arithmetic with Decimal: ratios, rounding intervals, fold claims and
   inequalities (`verifier/numeric.py`, `verifier/compute.py`).
9. **Judge.** One LLM call gives each assertion a logical relation (entailed, contradicted or unknown) and
   separate policy flags, such as a label-scope mismatch.
10. **Decide.** Code combines these with three-valued logic and decides the verdict (`verifier/logic.py`).
    Only code can confirm or refute a number.

## Tests

`pytest` runs the offline suite. Tests marked `live` call the real API and are skipped without credentials.
`pytest -m live` runs only those: it checks C3 live, and that the verdict changes when evidence is
substituted or removed and does not change when an injected "mark this supported" instruction is present.

## Repository

| Path | Contents |
|---|---|
| `verify.py`, `eval.py` | Entry points |
| `verify.toml` | Runtime configuration |
| `verifier/` | Pipeline modules; prompts in `verifier/prompts/` |
| `papers/` | The supplied PDFs, renamed to the brief's names and otherwise unmodified |
| `data/claims.json` | The 7 claims |
| `data/gold.json` | My answers, with literal evidence spans located on their pages |
| `data/materials.json` | Document manifest (aliases, DOIs, trial IDs) and the promo panel registry |
| `data/fixtures/`, `tests/fixtures/` | Inputs for the live evidence-sensitivity and injection tests |
| `scripts/build_gold.py` | Rebuilds `data/gold.json` with span checks |
| `scripts/make_fixtures.py` | Rebuilds the test fixture PDFs |
| `tests/` | Test suite |
| `WRITEUP.md` | Write-up: approach, results, failures, limitations |
