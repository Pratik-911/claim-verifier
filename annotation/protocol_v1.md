# Annotation protocol v1 (pilot)

Schema: `annotation/schema_v1.json`, generated from `verifier/annotation.py`. Validate with
`python scripts/validate_annotations.py file.jsonl`. One JSON object per line, one claim per object.

## 1. What is being labelled

Source-relative verification: does the claim, within its declared **scope**, follow from the
**authorised evidence** (the cited documents, plus a declared label for label claims)? You are not
judging literature-wide truth or regulatory compliance. Use only the supplied documents.

## 2. Two separate layers for every assertion

1. **Logical relation** to the evidence: `entailed`, `contradicted` or `unknown`.
   - Absence of significance is never evidence of no effect ("not significant" ≠ "no difference").
2. **Policy flags** from the materiality rubric. A flag is not a contradiction:
   - `label_scope_mismatch`: a label/indication claim omits or broadens a label restriction (Q1).
   - `unsupported_generalization`: the ordinary prescriber reading extends beyond a defining studied
     dimension (stage/line, severity, age band, renal/hepatic impairment, pregnancy, biomarker,
     monotherapy vs add-on, dose, regimen, time point, subgroup presented as whole) (Q2).
   - `design_strengthening`: post hoc as confirmatory, pooled dissimilar studies, nominal p as
     confirmatory (Q3).
   - `omitted_context`: non-material and disclosure only (e.g. background therapy shared by both arms).
   - `unsupported_precision`: the claim states more digits than the source supports (disclosure).
   - `conflicting_evidence`: aligned sources disagree (same endpoint, population, estimand, time point).

NOT applies only to logical relations. The negation of a flagged generalisation is `unknown`.

Record each omitted or changed qualification under `materiality`, with its category, the decision
question that settled it (Q1/Q2/Q3/none) and *why* it does or does not change meaning or applicability.
A caveat never narrows the claim: label the claim as written.

## 3. Claim verdict (must follow from the layers; the validator checks this)

- `overstated`: the root relation is contradicted, or a required, non-negated assertion carries a
  material flag (label_scope_mismatch, unsupported_generalization, design_strengthening).
- `supported`: every required assertion is entailed with no material flag.
- `cannot_verify`: everything else. Operational failures (API errors, timeouts) are **never**
  annotation labels.

## 4. Evidence

- Quote evidence **verbatim** from the document text layer, or from OCR text marked `source_kind: ocr`.
  Never add "…" or paraphrase; keep punctuation that is really in the source.
- `sufficient_evidence_sets`: list every alternative set of evidence ids that would on its own
  establish all required qualifications (for example the abstract sentence *or* the results table).
- `bundles`: label what each specific evidence bundle establishes (`bundle_label`), per assertion,
  with both layers. When the decisive passage is removed, re-check: another sufficient set may remain.
- `attribution`: for each decisive number, record the arm, endpoint and estimand note that you read
  from the layout (legend, column headers, row labels), not from n/N arithmetic.

## 5. Independence and adjudication

1. Both annotators label the **agreement sample** independently, before any discussion, with
   `independent: true` and `model_prelabels_shown: false`. No model pre-labels are shown for this sample.
2. Run `python scripts/agreement_report.py a.jsonl b.jsonl`. It reports class counts, raw
   agreement, per-class disagreements and Cohen's κ / Krippendorff's α, with the representative and
   difficult strata kept apart.
3. The reviewer adjudicates disagreements and every `needs_review` case, writing a separate record
   (`annotator_role: reviewer`, with `reviewer_expertise`, e.g. "clinical pharmacist",
   "biostatistician") and `adjudication.status: adjudicated`, `deciding_rule`, `notes`.
4. Only adjudicated records become training targets.

## 6. Families, dependency components and splits

- `family_ids`: the trial(s) behind the evidence (registry ids or trial names). Secondary analyses
  list every trial whose participants they reuse (e.g. Tabák PSM → LixiLan-L and GetGoal Duo-2).
- `variant_of`: set for paraphrases and synthetic variants of another claim.
- Components (shared families, variants, multi-source claims) are assigned whole to one split. The
  validator reports any component that spans splits.
- Pilot allocation (plan §6 B3): about 16 development families, about 8 untouched test families
  (≥3 held-out drugs/indications, ≥2 held-out layouts or source types including ≥1 scan), and a
  separate claim-disjoint check on development documents.

## 7. Strata

`source_stratum`: `natural` (public HCP or promotional pages, press releases), `enforcement` (PMCPA
cases or FDA OPDP letters, re-labelled under this rubric; they over-represent disputed claims),
`assignment_style`, or `synthetic` (human-reviewed mutations; matched S/C/I variants of one template).

## 8. Time

Record `seconds_spent` per claim. The pilot measures time per example before the corpus schedule is
committed.
