"""Inter-annotator agreement on INDEPENDENT, pre-adjudication labels.

Usage: python scripts/agreement_report.py annotator_a.jsonl annotator_b.jsonl
Reports, per agreement stratum (representative vs difficult, never pooled): n, class counts, raw
agreement, per-class disagreements, Cohen's kappa and Krippendorff's alpha (kappa is prevalence-
sensitive; read it together with raw agreement and class counts).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verifier.annotation import agreement  # noqa: E402


def load(path: str) -> dict:
    out = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("independent") and not r.get("model_prelabels_shown"):
                out[r["claim"]["claim_id"]] = r
    return out


def main(a_path: str, b_path: str) -> int:
    a, b = load(a_path), load(b_path)
    shared = sorted(set(a) & set(b))
    if not shared:
        print("no claims labelled independently by both annotators")
        return 1
    for stratum in ("representative", "difficult"):
        ids = [c for c in shared if a[c].get("agreement_stratum") == stratum]
        if not ids:
            continue
        rep = agreement([a[c]["corpus_verdict"] for c in ids], [b[c]["corpus_verdict"] for c in ids])
        secs = [a[c]["seconds_spent"] for c in ids] + [b[c]["seconds_spent"] for c in ids]
        print(f"== {stratum}: n={rep['n']}")
        print(f"   class counts A={rep['class_counts']['a']} B={rep['class_counts']['b']}")
        print(f"   raw agreement {rep['raw_agreement']:.3f}; Cohen's kappa {rep['cohen_kappa']:.3f}; "
              f"Krippendorff's alpha {rep['krippendorff_alpha']:.3f}")
        print(f"   disagreements {rep['disagreements'] or 'none'}")
        print(f"   median seconds per claim {sorted(secs)[len(secs) // 2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
