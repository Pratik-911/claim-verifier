"""Compare predictions with gold.

Usage: python eval.py [--pred outputs/predictions.json] [--gold data/gold.json] [--no-span-check]

Prints the assignment's metrics first (accuracy, confusion matrix, false supported), then the
pilot metrics defined in plan §5.6/§7. Counts are always shown; no intervals at this sample size.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LABELS = ["supported", "overstated", "cannot_verify"]


def _load(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text())
    return data["claims"] if isinstance(data, dict) and "claims" in data else data


def _pct(num: int, den: int) -> str:
    return f"{num}/{den} ({100 * num / den:.1f}%)" if den else f"{num}/{den} (undefined)"


def evaluate(preds: list[dict], gold: list[dict]) -> dict:
    by_id = {p["claim_id"]: p for p in preds}
    rows = []
    for g in gold:
        p = by_id.get(g["claim_id"], {"verdict": "cannot_verify", "operational_status": "missing"})
        rows.append((g, p))
    n = len(rows)
    correct = sum(g["verdict"] == p["verdict"] for g, p in rows)
    matrix = {gl: {pl: 0 for pl in LABELS} for gl in LABELS}
    for g, p in rows:
        matrix[g["verdict"]][p["verdict"]] += 1
    approvals = [(g, p) for g, p in rows if p["verdict"] == "supported"]
    false_supported = sum(g["verdict"] != "supported" for g, _ in approvals)
    gold_non_supported = sum(g["verdict"] != "supported" for g, _ in rows)
    answerable = [(g, p) for g, p in rows if g.get("answerable", True)]
    gold_supported_answerable = sum(g["verdict"] == "supported" for g, _ in answerable)
    true_approvals = sum(g["verdict"] == "supported" for g, _ in approvals)
    decisions_correct = sum(p["verdict"] == g["verdict"] and g["verdict"] in ("supported", "overstated")
                            for g, p in answerable)
    operational = sum(p.get("operational_status", "ok") != "ok" for _, p in rows)
    semantic_abstain = sum(p["verdict"] == "cannot_verify" and p.get("operational_status", "ok") == "ok"
                           for _, p in rows)
    end_to_end = sum(g["verdict"] == p["verdict"] and p.get("operational_status", "ok") == "ok" for g, p in rows)
    return {"n": n, "correct": correct, "matrix": matrix, "false_supported": false_supported,
            "approvals": len(approvals), "true_approvals": true_approvals,
            "gold_non_supported": gold_non_supported, "gold_supported_answerable": gold_supported_answerable,
            "answerable": len(answerable), "decisions_correct": decisions_correct,
            "operational": operational, "semantic_abstain": semantic_abstain, "end_to_end": end_to_end,
            "rows": rows}


def report(m: dict) -> str:
    out = [f"Accuracy: {_pct(m['correct'], m['n'])}", "",
           "Confusion matrix (rows = gold, columns = predicted):",
           f"{'':>15}" + "".join(f"{lab:>15}" for lab in LABELS)]
    for gl in LABELS:
        out.append(f"{gl:>15}" + "".join(f"{m['matrix'][gl][pl]:>15}" for pl in LABELS))
    out += ["", f"False supported: {m['false_supported']}", "", "--- pilot metrics (plan §5.6) ---",
            f"Approval error (false supported / approvals): {_pct(m['false_supported'], m['approvals'])}",
            f"FPR among gold non-supported: {_pct(m['false_supported'], m['gold_non_supported'])}",
            f"Supported recall (true approvals / gold-supported answerable): "
            f"{_pct(m['true_approvals'], m['gold_supported_answerable'])}",
            f"Decision accuracy on answerable claims: {_pct(m['decisions_correct'], m['answerable'])}",
            f"End-to-end success per claim attempt: {_pct(m['end_to_end'], m['n'])}",
            f"Abstentions: semantic {m['semantic_abstain']}, operational {m['operational']}"]
    wrong = [(g["claim_id"], g["verdict"], p["verdict"]) for g, p in m["rows"] if g["verdict"] != p["verdict"]]
    if wrong:
        out += ["", "Disagreements (claim: gold -> predicted):"] + [f"  {c}: {gv} -> {pv}" for c, gv, pv in wrong]
    return "\n".join(out)


def span_check(preds: list[dict], gold: list[dict]) -> str:
    from verifier.spancheck import check_spans  # needs the corpus; imported lazily
    return check_spans(preds, gold)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compare predictions with gold.")
    ap.add_argument("--pred", default="outputs/predictions.json")
    ap.add_argument("--gold", default="data/gold.json")
    ap.add_argument("--no-span-check", action="store_true", help="skip verbatim evidence checks")
    args = ap.parse_args(argv)
    try:
        preds, gold = _load(args.pred), _load(args.gold)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(report(evaluate(preds, gold)))
    if not args.no_span_check:
        print("\n--- evidence span check ---")
        print(span_check(preds, gold))
    return 0


if __name__ == "__main__":
    sys.exit(main())
