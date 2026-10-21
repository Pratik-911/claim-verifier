"""Validate annotation JSONL files against protocol v1 (schema + protocol rules).

Usage: python scripts/validate_annotations.py annotations.jsonl [more.jsonl ...]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verifier.annotation import dependency_components, validate_record  # noqa: E402


def main(paths: list[str]) -> int:
    records, bad = [], 0
    for path in paths:
        for i, line in enumerate(Path(path).read_text().splitlines(), 1):
            if not line.strip():
                continue
            rec = json.loads(line)
            problems = validate_record(rec)
            if problems:
                bad += 1
                print(f"{path}:{i} {rec.get('claim', {}).get('claim_id')}: " + "; ".join(problems))
            records.append(rec)
    comps = dependency_components(records)
    splits: dict[str, set] = {}
    for r in records:
        splits.setdefault(comps[r["claim"]["claim_id"]], set()).add(r.get("split", "unassigned"))
    leaks = {c: s - {"unassigned"} for c, s in splits.items() if len(s - {"unassigned"}) > 1}
    for comp, s in leaks.items():
        print(f"split leak: dependency component {comp} spans splits {sorted(s)}")
    print(f"{len(records)} records, {bad} invalid, {len(set(comps.values()))} dependency components, "
          f"{len(leaks)} split leaks")
    return 1 if bad or leaks else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
