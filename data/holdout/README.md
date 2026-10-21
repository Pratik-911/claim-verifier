# Sealed holdouts (written by the user or reviewer, not by the builder)

Write these BEFORE looking at pipeline outputs, then seal them:

1. `e1_holdout.json`: 8 new claims against the SAME supplied papers (claim-disjoint). Same format
   as data/claims.json. Mix supported, overstated and cannot_verify; include at least one label claim,
   one fold/ratio claim, one p-value claim, one claim on a raster source (LBL or LixiLan-L abstract).
2. `e1_holdout_gold.json`: verdict, evidence_page and evidence_span for each claim (same format as
   data/gold.json). Run `python scripts/build_gold.py`-style span checks, or leave spans for later.
3. `e2_smoke/`: one new open-access RCT PDF plus `claims.json` (4 claims) and `gold.json`. Add the PDF
   to data/materials.json (documents + families) — no code or prompt changes.

E2-smoke candidates (CC BY per Europe PMC, frequentist phase 3, a different indication from the
supplied papers); pick one:

| PMCID | Study | Journal | DOI |
|---|---|---|---|
| PMC9330971 | Galcanezumab in episodic migraine, PERSIST (placebo-controlled) | J Headache Pain 2022 | 10.1186/s10194-022-01458-0 |
| PMC9676838 | Erenumab in chronic migraine, DRAGON (placebo-controlled) | J Headache Pain 2022 | 10.1186/s10194-022-01514-9 |
| PMC11374750 | Esaxerenone vs trichlormethiazide, EXCITE-HT (noninferiority, 3.9/2.1 mmHg margin) | Hypertens Res 2024 | 10.1038/s41440-024-01762-z |

Seal: `shasum -a 256 data/holdout/* > data/holdout/SEALED.sha256` and do not edit afterwards.
The pipeline is run on them once, at the end; results go into WRITEUP.md as they come out.
