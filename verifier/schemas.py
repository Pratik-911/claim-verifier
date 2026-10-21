"""Input claims and the output prediction contract (assignment fields + documented extensions)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Verdict = Literal["supported", "overstated", "cannot_verify"]
CheckValue = Optional[Literal["match", "mismatch", "unclear"]]


class Claim(BaseModel):
    model_config = ConfigDict(extra="allow")
    claim_id: str
    text: str
    population: Optional[str] = None
    molecule: Optional[str] = None
    cited_paper: Optional[str] = None
    panel_refs: Optional[list[str]] = None


class Checks(BaseModel):
    figure: CheckValue = None
    population: CheckValue = None
    drug_indication: CheckValue = None
    actually_stated: CheckValue = None


class EvidenceRecord(BaseModel):
    doc_id: str
    sha256: str
    page: int
    source_kind: Literal["text_layer", "ocr"]
    literal_quote: str
    bbox: Optional[tuple[float, float, float, float]] = None
    region_id: Optional[str] = None
    supports: list[str] = Field(default_factory=list)  # assertion ids this record bears on


class Prediction(BaseModel):
    # --- assignment contract ---
    claim_id: str
    verdict: Verdict
    checks: Checks
    evidence_page: Optional[int] = None
    evidence_span: Optional[str] = None
    computed: Optional[dict] = None
    reason: str
    # --- extensions ---
    scope: Optional[str] = None
    text_verdict: Optional[Verdict] = None
    evidence_source: Optional[str] = None
    evidence_records: list[EvidenceRecord] = Field(default_factory=list)
    panel_findings: list[dict] = Field(default_factory=list)
    abstention_type: Optional[str] = None  # semantic reason for cannot_verify
    operational_status: str = "ok"  # ok | auth | quota | timeout | invalid_output | internal_error
    disclosures: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


_CLAIMS = TypeAdapter(list[Claim])


def load_claims(path: str | Path) -> list[Claim]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and "claims" in data:
        data = data["claims"]
    return _CLAIMS.validate_python(data)


def operational_abstention(claim: Claim, status: str, detail: str, scope: str | None) -> Prediction:
    return Prediction(claim_id=claim.claim_id, verdict="cannot_verify", checks=Checks(),
                      reason=f"Not verified (operational failure: {status}): {detail}",
                      scope=scope, operational_status=status)
