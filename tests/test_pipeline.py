"""End-to-end plumbing with a scripted stand-in for the LLM (real ingestion, grounding, compute, policy)."""
from pathlib import Path

import pytest

from verifier.config import load_config
from verifier.llm import LLMError
from verifier.llm_schemas import (Assertion, AssertionJudgment, ClaimedNumber, EvidenceItem, Extraction, Fact,
                                  Interpretation, Judgment, NumberLink, VisualReading)
from verifier.pipeline import run_claims
from verifier.schemas import Claim

ROOT = Path(__file__).resolve().parents[1]
C3 = Claim(claim_id="C3", text="With SOLIQUA, ~2x greater HbA1c reduction vs basal insulin (iGlar 100 U/mL), "
                               "p<0.0001, from baseline to Week 30",
           population="T2DM inadequately controlled on oral agents", molecule="iGlarLixi vs iGlar (basal insulin)",
           cited_paper="papers/LixiLan-O_Rosenstock2016.pdf")


def interp_c3():
    return Interpretation(interpretation_ok=True, has_propositional_or=False, assertions=[
        Assertion(id="A1", origin="text", origin_ref="~2x greater HbA1c reduction vs basal insulin (iGlar 100 U/mL)",
                  claim_type="fold", statement="HbA1c reduction with iGlarLixi is ~2x that with iGlar 100 U/mL",
                  subject="iGlarLixi", predicate="greater HbA1c reduction", comparator="iGlar", endpoint="HbA1c",
                  check_category="figure", quantifier="approximate",
                  numbers=[ClaimedNumber(raw="~2x", role="fold"), ClaimedNumber(raw="100 U/mL", role="dose")],
                  search_terms=["HbA1c", "change from baseline", "iGlar"]),
        Assertion(id="A2", origin="text", origin_ref="p<0.0001", claim_type="comparison",
                  statement="The difference vs iGlar has p<0.0001", subject="iGlarLixi", predicate="differs",
                  check_category="figure", numbers=[ClaimedNumber(raw="p<0.0001", role="p_value")]),
        Assertion(id="A3", origin="text", origin_ref="from baseline to Week 30", claim_type="numeric_value",
                  statement="Measured from baseline to Week 30", subject="iGlarLixi", predicate="measured at",
                  check_category="drug_indication", numbers=[ClaimedNumber(raw="30", role="timepoint")]),
        Assertion(id="A4", origin="population", origin_ref="T2DM inadequately controlled on oral agents",
                  claim_type="qualitative", statement="Population: T2DM inadequately controlled on oral agents",
                  subject="study population", predicate="is", check_category="population"),
    ])


def extract_c3():
    return Extraction(evidence=[
        EvidenceItem(id="E1", page=13, kind="result", assertion_ids=["A1", "A3"],
                     quote="changes from baseline to Week 30 in HbA1c were −1.63% for iGlarLixi, −1.34% for",
                     facts=[Fact(id="E1.F1", raw="−1.63%", statistic="LS mean change", arm="iGlarLixi"),
                            Fact(id="E1.F2", raw="−1.34%", statistic="LS mean change", arm="iGlar"),
                            Fact(id="E1.F3", raw="30", statistic="time point")]),
        EvidenceItem(id="E2", page=13, kind="result", assertion_ids=["A2"],
                     quote="between iGlarLixi and iGlar (−0.3% [−3.2 mmol/mol] [95% CI: −0.4, −0.2 "
                           "(−4.2 mmol/mol, −2.1 mmol/mol); p<0.0001])",
                     facts=[Fact(id="E2.F1", raw="p<0.0001", statistic="p-value", arm="iGlarLixi vs iGlar")]),
        EvidenceItem(id="E3", page=13, kind="other", assertion_ids=["A1"], quote="this sentence is not in the paper",
                     facts=[]),
    ], links=[
        NumberLink(assertion_id="A1", claimed_raw="~2x", fact_ids=["E1.F1", "E1.F2"], operation="ratio"),
        NumberLink(assertion_id="A1", claimed_raw="100 U/mL", fact_ids=[], operation="none"),
        NumberLink(assertion_id="A2", claimed_raw="p<0.0001", fact_ids=["E2.F1"], operation="inequality"),
        NumberLink(assertion_id="A3", claimed_raw="30", fact_ids=["E1.F3"], operation="compare"),
    ])


def judge_c3():
    return Judgment(summary="Ratio of reductions is 1.22x, not ~2x.", assertions=[
        AssertionJudgment(assertion_id="A1", relation="contradicted", reason_code="fold_mismatch",
                          evidence_ids=["E1"], rationale="1.63/1.34 is not ~2."),
        AssertionJudgment(assertion_id="A2", relation="entailed", reason_code="p_matches", evidence_ids=["E2"],
                          rationale="p<0.0001 stated."),
        AssertionJudgment(assertion_id="A3", relation="entailed", reason_code="timepoint", evidence_ids=["E1"],
                          rationale="Week 30."),
        AssertionJudgment(assertion_id="A4", relation="entailed", reason_code="population_matches",
                          evidence_ids=[], rationale="LixiLan-O enrolled patients on oral agents."),
    ])


class ScriptedLLM:
    def __init__(self, script):
        self.script = script
        self.calls = []

    def generate_json(self, *, stage, schema, **kw):
        self.calls.append(stage)
        out = self.script[stage]
        if isinstance(out, Exception):
            raise out
        return out() if callable(out) else out


@pytest.fixture
def cfg(tmp_path):
    c = load_config(ROOT / "verify.toml")
    c["run"]["corpus_dir"] = str(ROOT / "corpus")
    c["run"]["max_workers"] = 1
    return c


def test_c3_fold_claim_is_overstated_with_computed_ratio(cfg):
    llm = ScriptedLLM({"interpret": interp_c3, "extract": extract_c3, "judge": judge_c3,
                       "visual": VisualReading(values=[])})
    (pred,) = run_claims([C3], cfg, backend="test", run_id="t", llm=llm)
    assert pred.verdict == "overstated"
    assert pred.checks.figure == "mismatch"
    assert pred.computed["result"] == "1.22x"
    assert pred.computed["from_source"] == ["-1.63", "-1.34"]
    assert pred.evidence_page == 13
    assert pred.evidence_span == "changes from baseline to Week 30 in HbA1c were −1.63% for iGlarLixi, −1.34% for"
    assert all(r.literal_quote != "this sentence is not in the paper" for r in pred.evidence_records)
    assert pred.operational_status == "ok"


def test_unresolved_citation_abstains_without_llm_calls(cfg):
    llm = ScriptedLLM({})
    claim = C3.model_copy(update={"cited_paper": "papers/Unknown_Trial_2019.pdf"})
    (pred,) = run_claims([claim], cfg, backend="test", run_id="t", llm=llm)
    assert (pred.verdict, pred.abstention_type) == ("cannot_verify", "citation_unresolved")
    assert llm.calls == []


def test_llm_auth_failure_is_operational_not_semantic(cfg):
    llm = ScriptedLLM({"interpret": LLMError("auth", "denied")})
    (pred,) = run_claims([C3], cfg, backend="test", run_id="t", llm=llm)
    assert (pred.verdict, pred.operational_status) == ("cannot_verify", "auth")
    assert pred.abstention_type is None


def test_uncovered_claim_number_blocks_support(cfg):
    def interp():
        i = interp_c3()
        i.assertions[0].numbers = [ClaimedNumber(raw="~2x", role="fold")]  # "100" no longer covered
        return i
    def judge_all_entailed():
        j = judge_c3()
        for a in j.assertions:
            a.relation = "entailed"
        return j
    def extract_supportive():
        e = extract_c3()
        e.links = [ln for ln in e.links if ln.assertion_id != "A1"]
        return e
    llm = ScriptedLLM({"interpret": interp, "extract": extract_supportive, "judge": judge_all_entailed,
                       "visual": VisualReading(values=[])})
    (pred,) = run_claims([C3], cfg, backend="test", run_id="t", llm=llm)
    assert pred.verdict == "cannot_verify"
    assert pred.abstention_type == "coverage_gap"


def test_hallucinated_claim_number_is_dropped(cfg):
    def interp():
        i = interp_c3()
        i.assertions[1].numbers.append(ClaimedNumber(raw="55%", role="percent"))  # not in the claim
        return i
    llm = ScriptedLLM({"interpret": interp, "extract": extract_c3, "judge": judge_c3,
                       "visual": VisualReading(values=[])})
    (pred,) = run_claims([C3], cfg, backend="test", run_id="t", llm=llm)
    assert any("55%" in u for u in pred.unresolved)


def _interp_with_flagged_population():
    i = interp_c3()
    i.assertions[3].numbers = [ClaimedNumber(raw="2", role="other")]  # e.g. "type 2" in a population field
    return i


def _extract_with_name_number():
    e = extract_c3()
    e.evidence[0].facts.append(Fact(id="E1.F9", raw="30", statistic="name number"))
    e.links.append(NumberLink(assertion_id="A4", claimed_raw="2", fact_ids=["E1.F9"], operation="compare"))
    return e


def _judge_flagging_population():
    j = judge_c3()
    j.assertions[3].policy_flags = ["unsupported_generalization"]
    return j


def test_computed_comes_from_the_contradicted_figure_assertion(cfg):
    llm = ScriptedLLM({"interpret": _interp_with_flagged_population, "extract": _extract_with_name_number,
                       "judge": _judge_flagging_population, "visual": VisualReading(values=[])})
    (pred,) = run_claims([C3], cfg, backend="test", run_id="t", llm=llm)
    assert pred.computed["result"] == "1.22x"


def test_numbers_that_are_part_of_names_are_never_compared(cfg):
    llm = ScriptedLLM({"interpret": _interp_with_flagged_population, "extract": _extract_with_name_number,
                       "judge": judge_c3, "visual": VisualReading(values=[])})
    (pred,) = run_claims([C3], cfg, backend="test", run_id="t", llm=llm)
    assert pred.checks.population == "match"


def test_judge_text_with_an_underived_number_is_replaced(cfg):
    def judge_computing():
        j = judge_c3()
        j.summary = "The ~2x figure reflects the comparison against lixisenatide (ratio 1.92)."
        return j
    llm = ScriptedLLM({"interpret": interp_c3, "extract": extract_c3, "judge": judge_computing,
                       "visual": VisualReading(values=[])})
    (pred,) = run_claims([C3], cfg, backend="test", run_id="t", llm=llm)
    assert "1.92" not in pred.reason
    assert any("judge_numbers_not_grounded" in u for u in pred.unresolved)


def test_computed_prefers_the_effect_estimate_over_ci_bounds(cfg):
    claim = Claim(claim_id="CX", text="iGlarLixi vs iGlar: difference −0.3% (95% CI −0.4, −0.2), p<0.0001",
                  population=None, molecule=None, cited_paper="papers/LixiLan-O_Rosenstock2016.pdf")

    def interp():
        return Interpretation(interpretation_ok=True, has_propositional_or=False, assertions=[
            Assertion(id="A1", origin="text", origin_ref=claim.text, claim_type="comparison",
                      statement=claim.text, subject="iGlarLixi", predicate="differs", check_category="figure",
                      numbers=[ClaimedNumber(raw="−0.4", role="ci_low"), ClaimedNumber(raw="−0.2", role="ci_high"),
                               ClaimedNumber(raw="−0.3%", role="effect_estimate"),
                               ClaimedNumber(raw="p<0.0001", role="p_value"),
                               ClaimedNumber(raw="95", role="other")])])

    def extract():
        return Extraction(evidence=[EvidenceItem(
            id="E2", page=13, kind="result", assertion_ids=["A1"],
            quote="between iGlarLixi and iGlar (−0.3% [−3.2 mmol/mol] [95% CI: −0.4, −0.2 "
                  "(−4.2 mmol/mol, −2.1 mmol/mol); p<0.0001])",
            facts=[Fact(id="E2.F1", raw="−0.3%", statistic="LS mean difference"),
                   Fact(id="E2.F2", raw="−0.4", statistic="CI low"), Fact(id="E2.F3", raw="−0.2", statistic="CI high"),
                   Fact(id="E2.F4", raw="p<0.0001", statistic="p")])],
            links=[NumberLink(assertion_id="A1", claimed_raw="−0.4", fact_ids=["E2.F2"], operation="compare"),
                   NumberLink(assertion_id="A1", claimed_raw="−0.2", fact_ids=["E2.F3"], operation="compare"),
                   NumberLink(assertion_id="A1", claimed_raw="−0.3%", fact_ids=["E2.F1"], operation="compare"),
                   NumberLink(assertion_id="A1", claimed_raw="p<0.0001", fact_ids=["E2.F4"], operation="inequality")])

    def judge():
        return Judgment(summary="Matches.", assertions=[AssertionJudgment(
            assertion_id="A1", relation="entailed", reason_code="ok", evidence_ids=["E2"], rationale="ok")])

    llm = ScriptedLLM({"interpret": interp, "extract": extract, "judge": judge, "visual": VisualReading(values=[])})
    (pred,) = run_claims([claim], cfg, backend="test", run_id="t", llm=llm)
    assert pred.verdict == "supported"
    assert pred.computed["claimed"] == "−0.3%"


def test_numbers_inside_words_are_not_called_hallucinations(cfg):
    # claim text has no standalone "2", so the only 2 is inside "T2DM"
    claim = C3.model_copy(update={"text": C3.text.replace("~2x", "~3x"),
                                  "population": "T2DM inadequately controlled on oral agents"})

    def interp():
        i = interp_c3()
        i.assertions[0].numbers = [ClaimedNumber(raw="~3x", role="fold"), ClaimedNumber(raw="100 U/mL", role="dose")]
        i.assertions[3].numbers = [ClaimedNumber(raw="2", role="other")]  # the 2 in "T2DM"
        return i
    llm = ScriptedLLM({"interpret": interp, "extract": extract_c3, "judge": judge_c3,
                       "visual": VisualReading(values=[])})
    (pred,) = run_claims([claim], cfg, backend="test", run_id="t", llm=llm)
    assert not any("not in claim" in u for u in pred.unresolved)


def test_computed_never_shows_a_dose_or_timepoint_comparison(cfg):
    claim = Claim(claim_id="CD", text="Delivered by a 100 U/mL pen", population=None, molecule=None,
                  cited_paper="papers/LixiLan-O_Rosenstock2016.pdf")

    def interp():
        return Interpretation(interpretation_ok=True, has_propositional_or=False, assertions=[
            Assertion(id="A1", origin="text", origin_ref=claim.text, claim_type="numeric_value",
                      statement=claim.text, subject="pen", predicate="delivers", check_category="figure",
                      numbers=[ClaimedNumber(raw="100", role="dose")])])

    def extract():
        return Extraction(evidence=[EvidenceItem(
            id="E1", page=9, kind="design", assertion_ids=["A1"], quote="pen injector (100 U/mL)",
            facts=[Fact(id="E1.F1", raw="100", statistic="concentration")])],
            links=[NumberLink(assertion_id="A1", claimed_raw="100", fact_ids=["E1.F1"], operation="compare")])

    def judge():
        return Judgment(summary="Matches.", assertions=[AssertionJudgment(
            assertion_id="A1", relation="entailed", reason_code="ok", evidence_ids=["E1"], rationale="ok")])

    llm = ScriptedLLM({"interpret": interp, "extract": extract, "judge": judge, "visual": VisualReading(values=[])})
    (pred,) = run_claims([claim], cfg, backend="test", run_id="t", llm=llm)
    assert pred.computed is None
