"""Two-layer claim logic.

Layer 1: the *logical relation* of each assertion to the evidence (entailed / contradicted /
unknown), combined with Kleene strong three-valued logic.
Layer 2: *policy flags* from the materiality rubric. NOT never acts on a policy flag, so the
negation of an unsupported generalisation is unknown, never supported.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from verifier.enums import Relation

MATERIAL_FLAGS = frozenset({"label_scope_mismatch", "unsupported_generalization", "design_strengthening"})
DISCLOSURE_FLAGS = frozenset({"omitted_context", "presentational_precision", "unsupported_precision"})
_SCORE = {Relation.CONTRADICTED: 0, Relation.UNKNOWN: 1, Relation.ENTAILED: 2}
_BY_SCORE = {v: k for k, v in _SCORE.items()}


@dataclass
class Leaf:
    id: str
    relation: Relation
    flags: set[str] = field(default_factory=set)
    required: bool = True


@dataclass
class And:
    children: list["Expr"]
    kind: str = "conjunction"  # or "enumeration" (members of one qualifier slot)


@dataclass
class Or:
    children: list["Expr"]
    kind: str = "propositional"  # "enumeration" is the only OR evaluated in v1


@dataclass
class Not:
    child: "Expr"


Expr = Union[Leaf, And, Or, Not]


def _leaves(expr: Expr):
    if isinstance(expr, Leaf):
        yield expr
    elif isinstance(expr, Not):
        yield from _leaves(expr.child)
    else:
        for c in expr.children:
            yield from _leaves(c)


def _has_material_flag(expr: Expr) -> bool:
    return any(leaf.flags & MATERIAL_FLAGS for leaf in _leaves(expr))


def evaluate(expr: Expr) -> Relation:
    if isinstance(expr, Leaf):
        return expr.relation
    if isinstance(expr, Not):
        if _has_material_flag(expr.child):
            return Relation.UNKNOWN  # a policy finding is not evidence of falsity
        inner = evaluate(expr.child)
        return {Relation.ENTAILED: Relation.CONTRADICTED,
                Relation.CONTRADICTED: Relation.ENTAILED}.get(inner, Relation.UNKNOWN)
    scores = [_SCORE[evaluate(c)] for c in expr.children]
    return _BY_SCORE[min(scores) if isinstance(expr, And) else max(scores)]


def _asserted_leaves(expr: Expr, negated: bool = False):
    """Leaves asserted positively (not under an odd number of NOTs)."""
    if isinstance(expr, Leaf):
        if not negated:
            yield expr
    elif isinstance(expr, Not):
        yield from _asserted_leaves(expr.child, not negated)
    else:
        for c in expr.children:
            yield from _asserted_leaves(c, negated)


def _has_propositional_or(expr: Expr) -> bool:
    if isinstance(expr, Or) and expr.kind != "enumeration":
        return True
    if isinstance(expr, Not):
        return _has_propositional_or(expr.child)
    if isinstance(expr, (And, Or)):
        return any(_has_propositional_or(c) for c in expr.children)
    return False


@dataclass
class Decision:
    verdict: str  # supported | overstated | cannot_verify
    root: Relation
    abstention_type: str | None = None
    flagged: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    disclosures: list[str] = field(default_factory=list)


def decide(expr: Expr, *, gate_failures: list[str] | None = None, interpretation_ok: bool = True) -> Decision:
    """Map the assertion graph to a claim verdict. Policy, not logic, decides `overstated` for flags."""
    gate_failures = gate_failures or []
    root = evaluate(expr)
    leaves = list(_leaves(expr))
    unresolved = [lf.id for lf in leaves if lf.relation is Relation.UNKNOWN]
    disclosures = [f"{lf.id}: {f}" for lf in leaves for f in sorted(lf.flags & DISCLOSURE_FLAGS)]
    if not interpretation_ok:
        return Decision("cannot_verify", root, "ambiguous_interpretation", unresolved=unresolved)
    if _has_propositional_or(expr):
        return Decision("cannot_verify", root, "unsupported_logic", unresolved=unresolved)
    flagged = [lf.id for lf in _asserted_leaves(expr) if lf.required and lf.flags & MATERIAL_FLAGS]
    if root is Relation.CONTRADICTED or flagged:
        return Decision("overstated", root, flagged=flagged, unresolved=unresolved, disclosures=disclosures)
    if root is Relation.ENTAILED and not gate_failures:
        return Decision("supported", root, disclosures=disclosures)
    if gate_failures:
        reason = gate_failures[0]
    elif any("conflicting_evidence" in lf.flags for lf in leaves):
        reason = "conflicting_evidence"
    else:
        reason = "evidence_missing"
    return Decision("cannot_verify", root, reason, unresolved=unresolved, disclosures=disclosures)
