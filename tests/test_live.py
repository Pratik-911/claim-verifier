"""Live tests against the real models (skipped without credentials). Each states its evidence precondition."""
from pathlib import Path

import pytest
from dotenv import load_dotenv

from verifier.config import load_config
from verifier.credentials import credential_status
from verifier.pipeline import run_claims
from verifier.resolve import load_materials
from verifier.schemas import Claim

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)
BACKEND, DETAIL = credential_status()
pytestmark = [pytest.mark.live, pytest.mark.skipif(BACKEND is None, reason=f"no LLM credentials: {DETAIL}")]

C3_TEXT = ("With SOLIQUA, ~2x greater HbA1c reduction vs basal insulin (iGlar 100 U/mL), p<0.0001, "
           "from baseline to Week 30")


@pytest.fixture
def cfg(tmp_path):
    c = load_config(ROOT / "verify.toml")
    c["run"]["llm_log"] = str(tmp_path / "llm_log.jsonl")
    c["run"]["max_workers"] = 1
    return c


def run_fixture(cfg, doc_name):
    claim = Claim(claim_id="LIVE", text=C3_TEXT, population="T2DM inadequately controlled on oral agents",
                  molecule="iGlarLixi vs iGlar (basal insulin)", cited_paper=f"{doc_name}.pdf")
    materials = load_materials(ROOT / "data" / "fixtures" / "materials_live.json")
    (pred,) = run_claims([claim], cfg, backend=BACKEND, run_id="live", materials=materials)
    return pred


def test_c3_is_overstated_with_computed_ratio(cfg):
    claim = Claim(claim_id="C3", text=C3_TEXT, population="T2DM inadequately controlled on oral agents",
                  molecule="iGlarLixi vs iGlar (basal insulin)", cited_paper="papers/LixiLan-O_Rosenstock2016.pdf")
    (pred,) = run_claims([claim], cfg, backend=BACKEND, run_id="live")
    assert pred.verdict == "overstated" and pred.computed and pred.computed["result"] == "1.22x"


def test_substituted_evidence_changes_the_fold_relation(cfg):
    # precondition: -1.63 -> -2.70 makes the ratio 2.01, so "~2x" is entailed by this bundle
    pred = run_fixture(cfg, "Fixture_LixiLanO_substituted")
    assert pred.verdict != "overstated"
    assert pred.computed and pred.computed["result"] == "2.01x"


def test_removed_decisive_evidence_is_not_overstated_or_supported(cfg):
    # precondition: no alternative sufficient evidence for the fold assertion remains on the page
    pred = run_fixture(cfg, "Fixture_LixiLanO_removed")
    assert pred.verdict == "cannot_verify"


def test_injected_instruction_does_not_change_the_verdict(cfg):
    pred = run_fixture(cfg, "Fixture_LixiLanO_injection")
    assert pred.verdict == "overstated"
