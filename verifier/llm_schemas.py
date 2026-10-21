"""Structured outputs requested from the LLM stages. Kept flat: Gemini JSON-schema mode rejects
deeply nested or highly optional schemas, and flat records are easier to validate in code."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

NumberRole = Literal["arm_value", "effect_estimate", "ci_low", "ci_high", "p_value", "threshold", "fold",
                     "percent", "count", "dose", "timepoint", "other"]
ClaimType = Literal["numeric_value", "comparison", "fold", "indication", "qualitative", "attribution"]
Check = Literal["figure", "population", "drug_indication", "actually_stated"]


# ---------------------------------------------------------------- interpret
class ClaimedNumber(BaseModel):
    raw: str = Field(description="number exactly as written in the claim or panel text, e.g. '1.3', '<0.001', '~2x', '42.20%'")
    role: NumberRole
    arm: Optional[str] = Field(None, description="treatment arm the number belongs to, if any")
    statistic: Optional[str] = Field(None, description="e.g. 'LS mean change', 'odds ratio', 'proportion', 'p-value'")


class Assertion(BaseModel):
    id: str = Field(description="short id, e.g. A1")
    origin: Literal["text", "population", "molecule", "panel"]
    origin_ref: str = Field(description="verbatim span of the claim field, or the panel_id for panel-origin assertions")
    claim_type: ClaimType
    statement: str = Field(description="one self-contained proposition, faithful to the claim wording")
    subject: str
    predicate: str
    comparator: Optional[str] = None
    endpoint: Optional[str] = None
    population: Optional[str] = None
    timepoint: Optional[str] = None
    negated: bool = False
    quantifier: Literal["none", "all", "approximate", "exact"] = "none"
    enumeration_group: Optional[str] = Field(None, description="same value for members of one enumerated qualifier slot ('A or B' listing subgroups)")
    check_category: Check
    numbers: list[ClaimedNumber] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list, description="endpoint/intervention/comparator/population/time terms for retrieval")


class Interpretation(BaseModel):
    assertions: list[Assertion]
    has_propositional_or: bool = Field(description="true if the claim asserts 'X or Y' between independent propositions")
    interpretation_ok: bool
    ambiguity: Optional[str] = None


# ---------------------------------------------------------------- extract
class Fact(BaseModel):
    id: str = Field(description="e.g. E1.F1")
    raw: str = Field(description="the number token exactly as it appears inside the quote (keep any sign as printed)")
    context: Optional[str] = Field(None, description="3-8 words around the number copied verbatim from the quote, so a number printed more than once can be located")
    statistic: str
    arm: Optional[str] = None
    comparator: Optional[str] = None
    endpoint: Optional[str] = None
    timepoint: Optional[str] = None
    analysis_population: Optional[str] = None
    adjusted: Optional[bool] = None
    ci_level: Optional[str] = None
    endpoint_role: Optional[str] = Field(None, description="primary / key secondary / secondary / exploratory / post hoc / safety, only if stated")


class EvidenceItem(BaseModel):
    id: str = Field(description="e.g. E1")
    page: int
    quote: str = Field(description="verbatim text copied from the page text shown, no added ellipses or paraphrase")
    assertion_ids: list[str]
    kind: Literal["result", "population", "design", "label", "other"]
    facts: list[Fact] = Field(default_factory=list)


class NumberLink(BaseModel):
    assertion_id: str
    claimed_raw: str
    fact_ids: list[str] = Field(description="fact(s) that the claimed number should be compared with; two operands for a fold/ratio claim (numerator first)")
    operation: Literal["compare", "ratio", "inequality", "none"]


class Extraction(BaseModel):
    evidence: list[EvidenceItem]
    links: list[NumberLink] = Field(description="required, possibly empty: a truncated answer must not pass as complete")
    not_found: list[str] = Field(default_factory=list, description="assertion ids with no relevant evidence on these pages")


# ---------------------------------------------------------------- visual
class VisualValue(BaseModel):
    fact_id: str
    value_as_printed: Optional[str] = Field(description="value read from the image including its sign, or null if unreadable")
    arm_label_as_printed: Optional[str] = None
    row_or_series_label: Optional[str] = None
    readable: bool


class VisualReading(BaseModel):
    values: list[VisualValue]


# ---------------------------------------------------------------- judge
class AssertionJudgment(BaseModel):
    assertion_id: str
    relation: Literal["entailed", "contradicted", "unknown"]
    policy_flags: list[Literal["label_scope_mismatch", "unsupported_generalization", "design_strengthening",
                               "omitted_context", "conflicting_evidence"]] = Field(default_factory=list)
    reason_code: str = Field(description="short code from the rubric, e.g. 'value_matches', 'population_broadened'")
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str


class Judgment(BaseModel):
    assertions: list[AssertionJudgment]
    summary: str = Field(description="one or two sentences a human reviewer can act on")
