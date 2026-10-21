# WRITEUP: SOLIQUA claim verification

## Approach

The LLM reads and labels; Python checks and decides.

1. **Ingest.** Text layer; RapidOCR only for raster or corrupted regions, always labelled as OCR.
2. **Interpret.** Gemini 3.8 Flash splits the claim, plus any promo panels it names (the scope in
   `verify.toml`; this makes C7 a three-panel claim), into atomic assertions.
3. **Extract.** BM25 picks pages; Flash quotes evidence and links claimed numbers to facts. Code
   requires verbatim quotes and re-reads values and signs from the source text.
4. **Visual check.** A rendered crop re-reads each decisive value and its arm; disagreement abstains.
5. **Compute.** Python does all arithmetic with Decimal: ratios, rounding intervals, inequalities.
   "~2×" accepts a ratio within 8% of 2, abstains between 8% and 12%, and rejects beyond that.
6. **Judge and decide.** Gemini 4 Argon gives each assertion a relation (entailed / contradicted /
   unknown) and policy flags such as `label_scope_mismatch`. Code combines them with three-valued
   logic, and only code can confirm or refute a number. Anything uncertain becomes `cannot_verify`.

## Results (`python eval.py`; submitted run 424 s, fresh clone with cold cache 514 s)

```
Accuracy: 6/7 (85.7%)

Confusion matrix (rows = gold, columns = predicted):
                     supported     overstated  cannot_verify
      supported              2              0              1
     overstated              0              2              0
  cannot_verify              0              0              2

False supported: 0

--- additional metrics ---
Approval error (false supported / approvals): 0/2 (0.0%)
FPR among gold non-supported: 0/4 (0.0%)
Supported recall (true approvals / gold-supported answerable): 2/3 (66.7%)
Decision accuracy on answerable claims: 4/5 (80.0%)
End-to-end success per claim attempt: 6/7 (85.7%)
Abstentions: semantic 3, operational 0

Disagreements (claim: gold -> predicted):
  C6: supported -> cannot_verify

--- evidence span check ---
predictions: 7/7 evidence spans located verbatim on the stated page (4 text layer, 3 OCR-labelled); pypdf confirms 4/4 text-layer spans
gold: 7/7 evidence spans located verbatim on the stated page (5 text layer, 2 OCR-labelled); pypdf confirms 5/5 text-layer spans
```

**C6 is the miss.** The judge sees text only and couldn't tie OR 0.45 to Fig 3's level-2 row, so it
abstained (fail-closed). The two previous runs, with the same verdict logic, scored 7/7.

**Gold:** C1 overstated (PI limited to adults with obesity, on metformin); C3 overstated (1.22×);
C5 cannot_verify (no equivalence margin for "weight neutral"); C7 cannot_verify (p<0.0001 vs
P<0.001). C2, C4, C5 and C7 await a second reviewer.

## The hardest table

**SoliMix minus signs.**
- **The problem.** SoliMix's minus signs are a mis-encoded glyph. PyMuPDF returns `\x01`; pdftotext
  drops it, so "−0.2% [97.5% CI −0.4, −0.1]" becomes "0.2% [97.5% CI 0.4, 0.1]", a silent sign flip.
- **How I found it.** Two extractors disagreed on one sentence while I was building gold.
- **What I did.** A per-region glyph check flags control and symbol-font characters (Tabák prints "<"
  as "\"). A number right after a flagged glyph gets `sign_unknown` and can never match. The flagged
  region is OCR'd and its sign re-read from the image.

**Figure 2 (run 6).** Its text layer prints row labels apart from the values. The model quoted label
and values in an order that doesn't exist there, so C4 abstained. Now each line must be verbatim on
the page and becomes its own record. Which *row* a value belongs to remains a model judgement from
the image; the visual check confirms only the value and the arm.

## One real failure

**C7** (gold `cannot_verify`) came out **overstated** in run 3. The abstract says weight "*decreased
by* 0.7 kg with iGlarLixi and *increased by* 0.7 kg with iGlar"; the panel says "−0.7 kg".
- **Causes.** My sign rule read reduction wording only on the claim side, so the bare "0.7"
  contradicted "−0.7". Source-side wording then missed Tabák's "decreased … by an LS mean (SE) of 0.62".
- **What fixed it.** A fail-closed rule rather than a better regex: a negative claim against a bare
  source magnitude of the same size is `unclear`, never `mismatch`.
- **Latent bug.** Grounding took the *first* "0.7"; repeated tokens now need verbatim context.

## Where AI misled me

1. **A runaway output that parsed as valid.** On C3, Gemini's extraction looped for 27,000
   characters inside one field yet closed as valid JSON, with facts cut short and `links` defaulting
   to empty. The judge then called "~2×" contradicted by itself (LLM arithmetic, `computed: null`).
   I traced the missing `computed` in `llm_log.jsonl`. **Now:** strings over 2,000 characters are
   invalid, `links` is required, and a number code never compared stays `unknown`.
2. **The arm matcher.** The AI-written arm check used substring matching, so "iGlar" matched
   "iGlarLixi (n=443)", wrongly accepting the comparator's value for the treatment arm. A test written
   for a different bug exposed it. It now matches whole words.

## What not to trust

- **OCR quotes**, labelled `(ocr)`, keep OCR errors ("SOuQuA"). **Model judgements on meaning**:
  "weight neutral", population fit, which figure row a value is in.
- **Run-to-run variance.** Quote choice and judge caution vary between runs (runs 3–8: 4/7 to 7/7).
- **Hand-authored inputs:** `data/materials.json` and the gold are mine and not yet reviewed.
- **A known gap.** SoliMix prints "n 5 443" ("=" encoded as "5"); the glyph check misses it.

## Next step

**The change:** check figure-row attribution in code. The visual check would read each value's row
label too, code would require the claimed endpoint in it, and the judge would get the confirmed
binding. This is the gap behind C6's miss.

**How I'd know it helped:** run C1–C7 plus the 8 sealed holdout claims 5 times each. The verdict flip
rate and abstentions on figure claims should fall, with false supported staying at 0.
