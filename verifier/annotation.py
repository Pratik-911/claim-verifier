"""Annotation protocol v1: record model (source of annotation/schema_v1.json), validation, dependency
components for splits, and inter-annotator agreement (computed on independent labels only)."""
from __future__ import annotations

from collections import Counter
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from verifier.enums import Relation
from verifier.logic import And, Leaf, Not, decide

PolicyFlag = Literal["label_scope_mismatch", "unsupported_generalization", "design_strengthening",
                     "omitted_context", "unsupported_precision", "conflicting_evidence"]
RelationLabel = Literal["entailed", "contradicted", "unknown"]
VerdictLabel = Literal["supported", "overstated", "cannot_verify"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnnClaim(_Strict):
    claim_id: str
    text: str
    population: Optional[str] = None
    molecule: Optional[str] = None
    cited_paper: Optional[str] = None
    panel_refs: Optional[list[str]] = None


class Materiality(_Strict):
    qualification: str
    category: Literal["explicit_contradiction", "unsupported_broadening", "omitted_context"]
    decision_question: Literal["Q1_label", "Q2_defining_dimension", "Q3_design", "none"]
    reason: str


class AnnAssertion(_Strict):
    assertion_id: str
    origin: Literal["text", "population", "molecule", "panel"]
    origin_ref: str
    statement: str
    check_category: Literal["figure", "population", "drug_indication", "actually_stated"]
    negated: bool = False
    enumeration_group: Optional[str] = None
    corpus_relation: RelationLabel
    corpus_flags: list[PolicyFlag] = Field(default_factory=list)
    materiality: list[Materiality] = Field(default_factory=list)


class AnnEvidence(_Strict):
    evidence_id: str
    doc_id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page: int = Field(ge=1)
    source_kind: Literal["text_layer", "ocr"]
    literal_quote: str
    bbox: Optional[tuple[float, float, float, float]] = None
    region_id: Optional[str] = None


class BundleLabel(_Strict):
    assertion_id: str
    relation: RelationLabel
    flags: list[PolicyFlag] = Field(default_factory=list)


class AnnBundle(_Strict):
    bundle_id: str
    kind: Literal["gold", "alternative", "pipeline", "perturbed"]
    evidence_ids: list[str]
    labels: list[BundleLabel]


class Attribution(_Strict):
    evidence_id: str
    number_raw: str
    arm: Optional[str] = None
    endpoint: Optional[str] = None
    estimand_note: Optional[str] = None


class Adjudication(_Strict):
    status: Literal["independent", "adjudicated", "disputed"] = "independent"
    final_verdict: Optional[VerdictLabel] = None
    deciding_rule: Optional[str] = None
    notes: Optional[str] = None


class AnnotationRecord(_Strict):
    schema_version: Literal["annotation-v1"]
    claim: AnnClaim
    scope: Literal["claim_only", "claim_and_referenced_panels"]
    source_stratum: Literal["natural", "enforcement", "assignment_style", "synthetic"]
    family_ids: list[str] = Field(min_length=1)
    variant_of: Optional[str] = Field(None, description="claim_id this is a paraphrase/synthetic variant of")
    split: Literal["dev", "claim_disjoint", "test", "unassigned"] = "unassigned"
    agreement_stratum: Literal["representative", "difficult", "none"] = "none"
    assertions: list[AnnAssertion] = Field(min_length=1)
    evidence: list[AnnEvidence] = Field(default_factory=list)
    sufficient_evidence_sets: list[list[str]] = Field(default_factory=list)
    bundles: list[AnnBundle] = Field(default_factory=list)
    corpus_verdict: VerdictLabel
    attribution: list[Attribution] = Field(default_factory=list)
    annotator: str
    annotator_role: Literal["annotator", "reviewer"]
    reviewer_expertise: Optional[str] = None
    independent: bool
    model_prelabels_shown: bool
    seconds_spent: int = Field(ge=0)
    uncertainty: Optional[str] = None
    adjudication: Adjudication = Field(default_factory=Adjudication)


def _implied_verdict(rec: AnnotationRecord) -> str:
    leaves = []
    for a in rec.assertions:
        leaf = Leaf(a.assertion_id, Relation(a.corpus_relation), set(a.corpus_flags))
        leaves.append(Not(leaf) if a.negated else leaf)
    return decide(And(leaves)).verdict


def validate_record(data: dict) -> list[str]:
    """Schema validation plus protocol rules. Returns a list of problems (empty = valid)."""
    try:
        rec = AnnotationRecord.model_validate(data)
    except ValidationError as e:
        return [f"schema: {err['loc']}: {err['msg']}" for err in e.errors()]
    problems = []
    ev_ids = {e.evidence_id for e in rec.evidence}
    a_ids = {a.assertion_id for a in rec.assertions}
    for s in rec.sufficient_evidence_sets:
        problems += [f"sufficient_evidence_sets references unknown evidence {x}" for x in s if x not in ev_ids]
    for b in rec.bundles:
        problems += [f"bundle {b.bundle_id} references unknown evidence {x}" for x in b.evidence_ids if x not in ev_ids]
        labelled = {lab.assertion_id for lab in b.labels}
        problems += [f"bundle {b.bundle_id} has no label for assertion {a}" for a in sorted(a_ids - labelled)]
    implied = _implied_verdict(rec)
    if implied != rec.corpus_verdict and rec.corpus_verdict != "cannot_verify":
        problems.append(f"corpus_verdict {rec.corpus_verdict!r} does not follow from the assertion layers "
                        f"(two-layer logic gives {implied!r})")
    if rec.independent and rec.model_prelabels_shown:
        problems.append("an independent label must not have seen model prelabels")
    if rec.annotator_role == "reviewer" and not rec.reviewer_expertise:
        problems.append("reviewer_expertise is required for reviewer records")
    return problems


def dependency_components(records: list[dict]) -> dict[str, str]:
    """Union-find over shared families and claim variants. Returns claim_id -> component id."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for r in records:
        cid = "claim:" + r["claim"]["claim_id"]
        for fam in r.get("family_ids", []):
            union(cid, "family:" + fam)
        if r.get("variant_of"):
            union(cid, "claim:" + r["variant_of"])
    return {r["claim"]["claim_id"]: find("claim:" + r["claim"]["claim_id"]) for r in records}


def cohen_kappa(a: list, b: list) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def krippendorff_alpha_nominal(a: list, b: list) -> float:
    """Two coders, nominal data, no missing values (coincidence-matrix form)."""
    pairs = list(zip(a, b))
    n = 2 * len(pairs)
    totals = Counter(a) + Counter(b)
    disagree = 2 * sum(x != y for x, y in pairs)  # each unit contributes o_ck and o_kc
    expected = sum(totals[c] * totals[k] for c in totals for k in totals if c != k)
    return 1.0 if expected == 0 else 1 - (n - 1) * disagree / expected


def agreement(a: list[str], b: list[str]) -> dict:
    """Agreement on independent, pre-adjudication labels. Report counts with kappa/alpha (prevalence caveat)."""
    if len(a) != len(b) or not a:
        raise ValueError("need two equal-length, non-empty label lists")
    dis = Counter(f"{x}->{y}" for x, y in zip(a, b) if x != y)
    return {"n": len(a), "raw_agreement": sum(x == y for x, y in zip(a, b)) / len(a),
            "class_counts": {"a": dict(Counter(a)), "b": dict(Counter(b))},
            "disagreements": dict(dis), "cohen_kappa": cohen_kappa(a, b),
            "krippendorff_alpha": krippendorff_alpha_nominal(a, b)}
