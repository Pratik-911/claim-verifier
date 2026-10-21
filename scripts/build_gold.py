"""Build data/gold.json from the adjudication table, verifying every literal span against the sources.

Gold is *proposed* until the user and their reviewer adjudicate (plan §3). A span that the provenance
code cannot locate verbatim aborts the build.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from verifier.sources import ingest, locate_quote  # noqa: E402

SCOPE = "claim_and_referenced_panels"
PATHS = {d: ROOT / "papers" / f"{d}.pdf" for d in
         ("SoliMix_Rosenstock2021", "LixiLan-O_Rosenstock2016", "LixiLan-L_Aroda2016_abstract", "Tabak2020_PSM",
          "SOLIQUA_LBL")}

GOLD = [
    {"claim_id": "C1", "verdict": "overstated", "text_only_verdict": "overstated",
     "checks": {"figure": None, "population": "mismatch", "drug_indication": "mismatch", "actually_stated": None},
     "evidence": [("SOLIQUA_LBL", 4, "indicated for the treatment of adults patients with Obesity with"),
                  ("SOLIQUA_LBL", 4, "in addition to metformin with or without SGLT2 inhibitors")],
     "why": "The supplied abridged India PI (LBL p4; 'Updated: Jan 2023', CCDS v6 29 Sep 2022) restricts the "
            "indication to adults with obesity and to use in addition to metformin; the claim ('adults with T2DM "
            "insufficiently controlled on basal insulin or oral agents') omits both label restrictions. The 'or' is "
            "an enumerated qualifier, so each listed subgroup carries label_scope_mismatch (rubric Q1). Scope: this "
            "reproduced label only; OCR spans (6 pt print).",
     "rubric_rules": ["0.5 Q1 label_scope_mismatch", "0.2 enumerated disjunction"], "initial_label": "overstated",
     "alternatives": []},
    {"claim_id": "C2", "verdict": "supported", "text_only_verdict": "supported",
     "checks": {"figure": "match", "population": "match", "drug_indication": "match", "actually_stated": "match"},
     "evidence": [("SoliMix_Rosenstock2021", 5, "Superiority: LS mean difference iGlarLixi vs BIAsp 30 (95% CI); "
                                                 "p-value -0.2 (-0.4, -0.1) %; <0.001"),
                  ("SoliMix_Rosenstock2021", 1, "baseline HbA1c (8.6%) was reduced by 1.3% with iGlarLixi and 1.1% "
                                                 "with BIAsp 30")],
     "why": "SoliMix Fig 1B (p5) gives the superiority LS mean difference -0.2 (95% CI -0.4, -0.1), p<0.001 — "
            "distinct from the 97.5% noninferiority CI — and the abstract (p1) states reductions of 1.3% and 1.1%; "
            "Fig 1B shows them as LS mean ± SE (-1.3 ± 0.1, -1.1 ± 0.1). 'Reduction 1.3' maps to the signed change "
            "-1.3 by rule. Background metformin ± SGLT2i is shared by both arms: omitted_context (disclosed).",
     "rubric_rules": ["0.5 omitted context (ICH E9(R1) add-on to SoC)"], "initial_label": "supported",
     "alternatives": ["overstated if reviewer treats omitted background metformin as unsupported broadening"]},
    {"claim_id": "C3", "verdict": "overstated", "text_only_verdict": "overstated",
     "checks": {"figure": "mismatch", "population": "match", "drug_indication": "match", "actually_stated": "match"},
     "evidence": [("LixiLan-O_Rosenstock2016", 13,
                   "changes from baseline to Week 30 in HbA1c were −1.63% for iGlarLixi, −1.34% for")],
     "computed": {"claimed": "~2x", "from_source": ["-1.63", "-1.34"], "result": "1.22x"},
     "why": "LixiLan-O p13: LS mean HbA1c changes -1.63% (iGlarLixi) vs -1.34% (iGlar); ratio 1.2164, far outside "
            "any reading of ~2x. Table 2 (p29) agrees only at lower precision. The LBL superscript points to "
            "LixiLan-L (1.1 vs 0.6 = 1.83x); recorded, not used to rescue the claim.",
     "rubric_rules": ["5.3 fold rule"], "initial_label": "overstated", "alternatives": []},
    {"claim_id": "C4", "verdict": "supported", "text_only_verdict": "supported",
     "checks": {"figure": "match", "population": "match", "drug_indication": "match", "actually_stated": "match"},
     "evidence": [("SoliMix_Rosenstock2021", 6, "42.2 (n=187) 1.65 (1.25, 2.19)"),
                  ("SoliMix_Rosenstock2021", 6, "31.8 (n=141)"),
                  ("SoliMix_Rosenstock2021", 6, "the BIAsp 30 group (Fig. 2) (exploratory end point)")],
     "why": "SoliMix Fig 2 (p6), column 'OR (95% CI)': 42.2% (n=187) vs 31.8% (n=141), adjusted OR 1.65 "
            "(1.25, 2.19); the text says the proportion was higher with iGlarLixi. Caveats: exploratory endpoint; "
            "adjusted OR (crude 1.5697 is not a substitute); '42.20/31.80' equal the source (presentational flag).",
     "rubric_rules": ["5.3 trailing zeros equal", "model-based estimate kept"], "initial_label": "overstated (v1, wrong)",
     "alternatives": ["cannot_verify if the reviewer requires a confirmatory endpoint"]},
    {"claim_id": "C5", "verdict": "cannot_verify", "text_only_verdict": "cannot_verify",
     "checks": {"figure": None, "population": "match", "drug_indication": "match", "actually_stated": "unclear"},
     "evidence": [("LixiLan-L_Aroda2016_abstract", 2,
                   "Mean body weight decreased by 0.7 kg with iGlarLixi and increased by")],
     "why": "The abstract (raster, OCR) reports a 0.7 kg decrease vs a 0.7 kg increase with iGlar. 'Weight neutral' "
            "is a substantive no-meaningful-change claim that needs a prespecified margin or equivalence analysis; "
            "none is given (and no author statement). Weight loss, no gain, non-significance and equivalence are "
            "different assertions.",
     "rubric_rules": ["0.2 substantive neutrality needs a margin"], "initial_label": "cannot_verify",
     "alternatives": ["overstated if the reviewer reads the front panel as generalising beyond LixiLan-L"]},
    {"claim_id": "C6", "verdict": "supported", "text_only_verdict": "supported",
     "checks": {"figure": "match", "population": "match", "drug_indication": "match", "actually_stated": "match"},
     "evidence": [("SoliMix_Rosenstock2021", 7, "6.3 (n=28) 0.45 (0.28, 0.73)"),
                  ("SoliMix_Rosenstock2021", 7, "12.9 (n=57)")],
     "why": "SoliMix Fig 3A (p7): level 2 hypoglycaemia 6.3% (n=28) iGlarLixi vs 12.9% (n=57) BIAsp 30 (arms from "
            "layout: n=442 legend row above n=441), OR 0.45 (95% CI 0.28, 0.73). Non-scored panel finding: the LBL's "
            "'55% lower risk' is a relative odds reduction; count-based RR is 0.4901.",
     "rubric_rules": ["arms from layout"], "initial_label": "supported", "alternatives": []},
    {"claim_id": "C7", "verdict": "cannot_verify", "text_only_verdict": "supported",
     "checks": {"figure": "unclear", "population": "match", "drug_indication": "match", "actually_stated": "match"},
     "evidence": [("SoliMix_Rosenstock2021", 5, "-1.9 (-2.3, -1.4) kg; <0.001"),
                  ("Tabak2020_PSM", 5, "All P values are reported as nominal P values"),
                  ("LixiLan-L_Aroda2016_abstract", 2, "0.7 kg with iGlar (1.4 kg difference, P < 0.0001)")],
     "why": "The claim names three LBL panels, so their numbers are in scope. Each analysis shows a weight benefit "
            "(LixiLan-L -0.7 vs +0.7 kg at 30 wk; SoliMix -0.7 vs +1.2 kg at 26 wk; Tabák -0.62 vs +0.70 kg at "
            "24 vs 26 wk), but the SoliMix panel's p<0.0001 is not established by the paper's P<0.001, and the "
            "LixiLan-L panel's n=365/366 and ±0.2 kg are not in the abstract. No contradiction. Caveats: post hoc PSM "
            "with nominal P (disclosed); shared participants (not three independent replications).",
     "rubric_rules": ["0.4 panel scope", "5.3 inequality entailment"], "initial_label": "overstated (v1)",
     "alternatives": ["supported under claim_only scope", "overstated under a strict design reading"]},
]


def main() -> int:
    docs = {d: ingest(p, cache_dir=ROOT / "corpus", doc_id=d) for d, p in PATHS.items()}
    out, failures = [], []
    for g in GOLD:
        records = []
        for doc_id, page, quote in g["evidence"]:
            loc = locate_quote(docs[doc_id], quote, page_hint=page)
            if loc is None or loc.page != page:
                failures.append(f"{g['claim_id']}: {doc_id} p{page}: {quote!r}")
                continue
            records.append({"doc_id": doc_id, "page": loc.page, "source_kind": loc.source_kind,
                            "literal_quote": loc.text, "bbox": loc.bbox, "region_id": loc.region_id})
        first = records[0] if records else None
        out.append({"claim_id": g["claim_id"], "verdict": g["verdict"], "scope": SCOPE,
                    "text_only_verdict": g["text_only_verdict"], "answerable": g["verdict"] != "cannot_verify",
                    "evidence_page": first["page"] if first else None,
                    "evidence_span": first["literal_quote"] if first else None,
                    "evidence_source": f"{first['doc_id']} ({first['source_kind']})" if first else None,
                    "evidence_records": records, "checks": g["checks"], "computed": g.get("computed"),
                    "why": g["why"], "rubric_rules": g["rubric_rules"], "initial_label": g["initial_label"],
                    "alternatives": g["alternatives"],
                    "adjudication": {"status": "proposed", "adjudicator": None, "reviewer_expertise": None,
                                     "needs_review": g["claim_id"] in ("C2", "C4", "C5", "C7")}})
    if failures:
        print("span verification failed:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1
    (ROOT / "data" / "gold.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"wrote data/gold.json ({len(out)} claims, every span located verbatim)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
