from verifier.compute import GroundedFact, assertion_numeric_relation, evaluate_link
from verifier.enums import Outcome, Relation
from verifier.llm_schemas import Assertion, ClaimedNumber, NumberLink


def A(predicate="reduces", numbers=(), claim_type="numeric_value"):
    return Assertion(id="A1", origin="text", origin_ref="x", claim_type=claim_type, statement="s", subject="drug",
                     predicate=predicate, check_category="figure",
                     numbers=[ClaimedNumber(raw=r, role=role) for r, role in numbers])


def F(fid, value, status="ok"):
    return GroundedFact(fact_id=fid, value=value, status=status, evidence_id="E1")


def test_fold_claim_against_two_reductions():
    a = A(numbers=[("~2x", "fold")], claim_type="fold")
    r = evaluate_link(a, NumberLink(assertion_id="A1", claimed_raw="~2x", fact_ids=["F1", "F2"], operation="ratio"),
                      {"F1": F("F1", "-1.63"), "F2": F("F2", "-1.34")})
    assert r.outcome is Outcome.MISMATCH
    assert r.computed == {"claimed": "~2x", "from_source": ["-1.63", "-1.34"], "result": "1.22x"}


def test_reduction_wording_compares_magnitude_of_signed_change():
    a = A(numbers=[("1.3", "arm_value")])
    r = evaluate_link(a, NumberLink(assertion_id="A1", claimed_raw="1.3", fact_ids=["F1"], operation="compare"),
                      {"F1": F("F1", "-1.3")})
    assert r.outcome is Outcome.MATCH


def test_signed_value_without_reduction_wording_keeps_sign():
    a = A(predicate="changes", numbers=[("0.7", "arm_value")])
    r = evaluate_link(a, NumberLink(assertion_id="A1", claimed_raw="0.7", fact_ids=["F1"], operation="compare"),
                      {"F1": F("F1", "-0.7")})
    assert r.outcome is Outcome.MISMATCH


def test_stronger_p_bound_is_unclear_not_contradicted():
    a = A(numbers=[("p<0.0001", "p_value")])
    r = evaluate_link(a, NumberLink(assertion_id="A1", claimed_raw="p<0.0001", fact_ids=["F1"], operation="inequality"),
                      {"F1": F("F1", "P < 0.001")})
    assert r.outcome is Outcome.UNCLEAR


def test_equal_p_bound_matches():
    a = A(numbers=[("p<0.001", "p_value")])
    r = evaluate_link(a, NumberLink(assertion_id="A1", claimed_raw="p<0.001", fact_ids=["F1"], operation="inequality"),
                      {"F1": F("F1", "<0.001")})
    assert r.outcome is Outcome.MATCH


def test_trailing_zero_percent_matches_with_flag():
    a = A(predicate="achieved", numbers=[("42.20%", "percent")])
    r = evaluate_link(a, NumberLink(assertion_id="A1", claimed_raw="42.20%", fact_ids=["F1"], operation="compare"),
                      {"F1": F("F1", "42.2")})
    assert r.outcome is Outcome.MATCH and r.flag == "presentational_precision"


def test_unreliable_fact_value_is_unclear():
    a = A(numbers=[("-0.2", "effect_estimate")])
    r = evaluate_link(a, NumberLink(assertion_id="A1", claimed_raw="-0.2", fact_ids=["F1"], operation="compare"),
                      {"F1": F("F1", "0.2", status="sign_unknown")})
    assert r.outcome is Outcome.UNCLEAR and r.reason == "extraction_unreliable"


def test_missing_fact_is_unclear_evidence_missing():
    a = A(numbers=[("1.65", "effect_estimate")])
    r = evaluate_link(a, NumberLink(assertion_id="A1", claimed_raw="1.65", fact_ids=["F9"], operation="compare"), {})
    assert r.outcome is Outcome.UNCLEAR and r.reason == "evidence_missing"


def test_two_times_greater_is_evaluated_under_both_readings():
    a = A(predicate="is two times greater", numbers=[("two times greater", "fold")], claim_type="fold")
    r = evaluate_link(a, NumberLink(assertion_id="A1", claimed_raw="two times greater", fact_ids=["F1", "F2"],
                                    operation="ratio"), {"F1": F("F1", "2.0"), "F2": F("F2", "1.0")})
    assert r.outcome is Outcome.UNCLEAR and r.reason == "ambiguous_interpretation"


def test_ci_bound_compares_signed():
    a = A(predicate="differs", numbers=[("-0.4", "ci_low")])
    r = evaluate_link(a, NumberLink(assertion_id="A1", claimed_raw="-0.4", fact_ids=["F1"], operation="compare"),
                      {"F1": F("F1", "-0.4")})
    assert r.outcome is Outcome.MATCH


def test_assertion_relation_combines_outcomes():
    from verifier.compute import LinkResult
    mk = lambda o: LinkResult(outcome=o)
    assert assertion_numeric_relation([mk(Outcome.MATCH), mk(Outcome.MISMATCH)]) is Relation.CONTRADICTED
    assert assertion_numeric_relation([mk(Outcome.MATCH), mk(Outcome.UNCLEAR)]) is Relation.UNKNOWN
    assert assertion_numeric_relation([mk(Outcome.MATCH)]) is Relation.ENTAILED
    assert assertion_numeric_relation([]) is None


def test_signed_claim_against_an_unsigned_source_magnitude_is_unclear_not_contradicted():
    # "weight decreased ... of 0.62 kg" phrased in a way no wording rule caught: never a contradiction
    a = A(predicate="changes", numbers=[("-0.62", "arm_value")])
    link = NumberLink(assertion_id="A1", claimed_raw="-0.62", fact_ids=["F1"], operation="compare")
    assert evaluate_link(a, link, {"F1": F("F1", "0.62")}).outcome is Outcome.UNCLEAR
    assert evaluate_link(a, link, {"F1": F("F1", "+0.62")}).outcome is Outcome.MISMATCH
    assert evaluate_link(a, link, {"F1": F("F1", "-0.62")}).outcome is Outcome.MATCH


def test_unsigned_source_with_a_different_magnitude_still_contradicts():
    a = A(predicate="changes", numbers=[("-0.62", "arm_value")])
    link = NumberLink(assertion_id="A1", claimed_raw="-0.62", fact_ids=["F1"], operation="compare")
    assert evaluate_link(a, link, {"F1": F("F1", "0.70")}).outcome is Outcome.MISMATCH


def test_extraction_without_a_links_key_is_rejected():
    import pytest
    from pydantic import ValidationError
    from verifier.llm_schemas import Extraction
    with pytest.raises(ValidationError):
        Extraction.model_validate_json('{"evidence": []}')
    assert Extraction.model_validate_json('{"evidence": [], "links": []}').links == []


def test_reduction_claim_against_a_stated_increase_is_a_contradiction():
    # "reduces weight by 0.7 kg" vs the source's "increased by 0.7 kg" (grounded as +0.7): never a match
    a = A(predicate="reduces body weight", numbers=[("0.7", "arm_value")])
    link = NumberLink(assertion_id="A1", claimed_raw="0.7", fact_ids=["F1"], operation="compare")
    assert evaluate_link(a, link, {"F1": F("F1", "+0.7")}).outcome is Outcome.MISMATCH
    assert evaluate_link(a, link, {"F1": F("F1", "-0.7")}).outcome is Outcome.MATCH
