# Dev journal (raw material for WRITEUP.md)

## Extraction / OCR findings
- SoliMix (Diabetes Care, Arbortext): body-text minus signs are `\x01` in PyMuPDF (font AdvP4C4E74) and vanish entirely in pdftotext ("LS mean difference 0.2% [97.5% CI 0.4, 0.1]"). A pipeline reading pdftotext output would see a *positive* difference. Handled: glyph check flags them; numbers after a flagged glyph get `sign_unknown` and never match (fail closed); flagged blocks are OCR'd.
- Tabák (Springer): "<" is encoded as "\" (font AdvPSMP4), "≥" as "C" (AdvTir_symb): "P \ 0.0001". Same handling via symbol-font rule.
- SoliMix "n 5 443" ("=" encoded as "5") uses a normal text font — NOT caught by the glyph rule. Known gap.
- LixiLan-L abstract: page 1 and 2 have a text-layer header/footer but the abstract body is a raster screenshot — a page-level "has text?" test would skip OCR. Region-level rule catches it.
- LBL: four photographed pages, no text layer. RapidOCR at 300 DPI reads the large print well but the ~6 pt PI makes character errors ("controlied", "gtycemic", "ezercise", "SOLQu"); 400 DPI was *worse* than 300. OCR quotes are therefore labelled OCR and cross-checked visually.
- Tabák DOI and an NCT id are wrapped across lines ("s13300-\n019…", "NCT020\n58160"); identifier search re-joins wraps.

## Where AI misled me (candidates)
- v1 plan (AI-drafted) called C4 "overstated" because the CI was "not stated as 95%" — the Fig 2 column header literally says "OR (95% CI)". Caught by an independent review re-reading the figure.
- v1 plan proposed identifying arms by n/N; fails on C6 where 28/441 and 28/442 both round to 6.3%.
- The assignment brief's example puts C3 evidence on "page 9"; our LixiLan-O file is a 38-page author manuscript where the sentence is on p13.
- v4 plan's rubric mapped "unsupported broadening" to "contradicted", so NOT(·) could approve a negated over-generalisation. Fixed by separating logical relations from policy flags.

## Failures found in live runs on 2026-10-20

### Signs written as words
**Symptom.** The source said "weight *decreased by* 0.7 kg" and the claim said "−0.7 kg". This gave a
false contradiction, which made C7 overstated.

**What changed.**
- **First fix:** direction wording gives the source value its sign.
- **Second failure, after that fix:** Tabák's "decreased with iGlarLixi by an LS mean (SE) of 0.62 kg"
  slipped past the 3-word window.
- **Final fix:** a widened window, plus a net. A negative claim against a bare source magnitude of
  equal size is `unclear`, never `mismatch`.

**Lesson.** Wording rules always miss some phrasing, so the fallback must fail closed.

### Arm identity by substring
**Symptom.** "premix BIAsp 30" did not match "BIAsp 30 (n=444)", so C4 abstained.

**Also.** The same check let "iGlar" match "iGlarLixi", which is fail-open.

**Fix.** Whole-word subset matching.

### The model copied the `⟦?⟧` marker into a quote
**Fix.** The marker now matches exactly one flagged glyph, and no real character.

### Judge latency at `thinking=high`
**Symptom.** The judge timed out at 180 s on 5 of 5 attempts for C5. At medium it answered in 11 s
with the same relations.

**Fix.** The judge now runs at medium.

### Multi-panel claims and the per-claim deadline
**Symptom.** C7 sent all 11 assertions to every document. That took 10–19k thinking tokens per
extraction, and the claim hit the 600 s per-claim deadline.

**Fix.** The panel registry's `sources` routes each panel's assertions to its own document. C7 went
from more than 600 s to 241 s.

## Failure found in live testing
- First live run hung for 3.5 hours inside the judge call (gemini-4-argon). Root cause: google-genai's
  HttpOptions.timeout defaults to None and the SDK passes timeout=None to httpx — a stalled response
  blocks forever — and my wrapper logged a call only after its response, so the hung request was
  invisible. Fixed with a request timeout, retryable timeout classification (failed requests logged with
  the full prompt) and a per-claim deadline. Lesson: "bounded retries" in the plan meant nothing without
  a bounded *wait*.
