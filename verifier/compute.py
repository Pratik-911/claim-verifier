"""Deterministic comparison of claimed numbers with grounded source facts.

The LLM only *links* a claimed number to fact ids and names an operation from a fixed enum; every
value, ratio and comparison is computed here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from verifier.enums import Outcome, Relation
from verifier.llm_schemas import Assertion, NumberLink
from verifier.numeric import (FOLD_ABSTAIN_BAND, FOLD_TOLERANCE, compare_reported, fold_check, inequality_relation,
                              parse_constraint, ratio)

SIGNED = re.compile(r"[+\-−–]?\d+(?:\.\d+)?")
REDUCTION = re.compile(r"reduc|decreas|lower|loss|lost|fell|drop|declin", re.I)
INEQUALITY = re.compile(r"[<>≤≥]")
FOLD_WORDS = {"twice": "2", "two": "2", "three": "3", "double": "2", "doubled": "2", "triple": "3"}
AMBIGUOUS_TIMES = re.compile(r"\b(\w+|\d+(?:\.\d+)?)\s*(?:times|x|×)\s+(?:greater|more|higher|larger)\b", re.I)


@dataclass
class GroundedFact:
    fact_id: str
    value: str | None  # numeric token as grounded (signed); None if unreadable
    status: str  # ok | sign_unknown | not_in_quote | visual_disagrees
    evidence_id: str


@dataclass
class LinkResult:
    outcome: Outcome
    reason: str | None = None
    flag: str | None = None
    computed: dict | None = None
    notes: list[str] = field(default_factory=list)


def _first_number(raw: str) -> str | None:
    m = SIGNED.search(raw.replace("−", "-").replace("–", "-"))
    return m.group(0) if m else None


def _fold_targets(raw: str) -> list[str]:
    """Claimed fold value(s). "two times greater/more" is ambiguous: ratio 2 or 3."""
    low = raw.lower()
    m = AMBIGUOUS_TIMES.search(low)
    if m:
        n = FOLD_WORDS.get(m.group(1), m.group(1))
        try:
            base = Decimal(n)
            return [str(base), str(base + 1)]
        except Exception:
            pass
    for word, n in FOLD_WORDS.items():
        if re.search(rf"\b{word}\b", low):
            return [n]
    num = _first_number(raw)
    return [num.lstrip("+")] if num else []


def _usable(facts: dict, fact_ids: list[str]) -> tuple[list[GroundedFact] | None, LinkResult | None]:
    found = [facts.get(f) for f in fact_ids]
    if not fact_ids or any(f is None for f in found):
        return None, LinkResult(Outcome.UNCLEAR, "evidence_missing")
    if any(f.status != "ok" or f.value is None for f in found):
        return None, LinkResult(Outcome.UNCLEAR, "extraction_unreliable")
    return found, None


def fold_settings(cfg: dict) -> tuple[Decimal, Decimal]:
    """(tolerance, abstention band) for "~N x" claims from verify.toml [numeric]."""
    num = cfg.get("numeric", {})
    return (Decimal(str(num.get("fold_tolerance", FOLD_TOLERANCE))),
            Decimal(str(num.get("fold_abstain_band", FOLD_ABSTAIN_BAND))))


def evaluate_link(assertion: Assertion, link: NumberLink, facts: dict[str, GroundedFact],
                  fold: tuple[Decimal, Decimal] = (FOLD_TOLERANCE, FOLD_ABSTAIN_BAND)) -> LinkResult:
    found, problem = _usable(facts, link.fact_ids)
    if problem:
        return problem
    claimed = link.claimed_raw
    if link.operation == "ratio":
        if len(found) != 2:
            return LinkResult(Outcome.UNCLEAR, "evidence_missing")
        a, b = (_first_number(f.value) for f in found)
        r = ratio(a, b) if a and b else None
        if r is None:
            return LinkResult(Outcome.UNCLEAR, "ratio_undefined")
        computed = {"claimed": claimed, "from_source": [a, b], "result": f"{r:.2f}x"}
        targets = _fold_targets(claimed)
        if not targets:
            return LinkResult(Outcome.UNCLEAR, "ambiguous_interpretation", computed=computed)
        outcomes = {fold_check(r, Decimal(t), *fold).outcome for t in targets}
        if len(outcomes) > 1:
            return LinkResult(Outcome.UNCLEAR, "ambiguous_interpretation", computed=computed,
                              notes=[f"readings {targets} disagree"])
        return LinkResult(outcomes.pop(), computed=computed)
    if link.operation == "inequality" or INEQUALITY.search(claimed):
        source_raw = found[0].value
        try:
            rel = inequality_relation(parse_constraint(source_raw if INEQUALITY.search(source_raw)
                                                       else "=" + _first_number(source_raw)),
                                      parse_constraint(claimed))
        except ValueError:
            return LinkResult(Outcome.UNCLEAR, "unparseable_inequality")
        outcome = {Relation.ENTAILED: Outcome.MATCH, Relation.CONTRADICTED: Outcome.MISMATCH}.get(rel, Outcome.UNCLEAR)
        return LinkResult(outcome, None if outcome is not Outcome.UNCLEAR else "bound_not_established",
                          computed={"claimed": claimed, "from_source": [source_raw], "result": rel.value})
    # plain comparison of one claimed value with one source value
    c, s = _first_number(claimed), _first_number(found[0].value)
    if c is None or s is None:
        return LinkResult(Outcome.UNCLEAR, "unparseable_number")
    c_signed = c[0] in "+-"
    if c.startswith("-") and s[0] not in "+-" and compare_reported(s, c[1:]).outcome is not Outcome.MISMATCH:
        # the source prints a bare magnitude whose direction no rule read: never a contradiction on sign alone
        return LinkResult(Outcome.UNCLEAR, "sign_not_stated",
                          computed={"claimed": claimed, "from_source": [found[0].value], "result": "unclear"})
    if not c_signed and REDUCTION.search(assertion.predicate or ""):
        if s.startswith("+"):  # "reduces by 0.7" against a stated increase of 0.7
            return LinkResult(Outcome.MISMATCH, computed={"claimed": claimed, "from_source": [found[0].value],
                                                          "result": "mismatch"})
        if s.startswith("-"):
            s = s[1:]  # "reduction of 1.3" <-> change of -1.3: compare magnitudes by explicit rule
    cmp = compare_reported(s, c)
    return LinkResult(cmp.outcome, None if cmp.outcome is not Outcome.UNCLEAR else cmp.flag, flag=cmp.flag,
                      computed={"claimed": claimed, "from_source": [found[0].value], "result": cmp.outcome.value})


def assertion_numeric_relation(results: list[LinkResult]) -> Relation | None:
    if not results:
        return None
    outcomes = {r.outcome for r in results}
    if Outcome.MISMATCH in outcomes:
        return Relation.CONTRADICTED
    if Outcome.UNCLEAR in outcomes:
        return Relation.UNKNOWN
    return Relation.ENTAILED
