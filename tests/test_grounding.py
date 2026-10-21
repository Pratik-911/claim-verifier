"""Grounding a fact's value in the source text: direction wording, repeated tokens, visual merge, arm identity."""
from types import SimpleNamespace

from verifier.pipeline import _arm_agrees, _ground_fact, _visual_value
from verifier.sources import Location

WEIGHT = "Mean body weight decreased by 0.7 kg with iGlarLixi and increased by\n0.7 kg with iGlar (1.4 kg difference, P < 0.0001)."


def ground(quote, raw, context=None):
    loc = Location(1, "ocr", 0, len(quote), "r1", None, quote)
    return _ground_fact(raw, loc, SimpleNamespace(pages=[None]), context=context)


def test_decrease_wording_gives_a_negative_value():
    assert ground(WEIGHT, "0.7", context="decreased by 0.7 kg with iGlarLixi") == ("-0.7", "ocr")


def test_increase_wording_gives_a_positive_value():
    assert ground(WEIGHT, "0.7", context="increased by\n0.7 kg with iGlar") == ("+0.7", "ocr")


def test_repeated_token_with_different_meanings_and_no_context_is_ambiguous():
    assert ground(WEIGHT, "0.7") == (None, "ambiguous_occurrence")


def test_context_not_in_quote_does_not_pick_an_occurrence():
    assert ground(WEIGHT, "0.7", context="fell by 0.7 kg") == (None, "ambiguous_occurrence")


def test_wording_with_intervening_words():
    assert ground("a decrease in HbA1c of 1.3% at week 26", "1.3") == ("-1.3", "ocr")
    assert ground("reduced the risk by 55% versus placebo", "55") == ("-55", "ocr")


def test_from_to_wording_is_not_a_signed_change():
    assert ground("HbA1c decreased from 8.1 to 6.8%", "8.1") == ("8.1", "ocr")
    assert ground("HbA1c decreased from 8.1 to 6.8%", "6.8") == ("6.8", "ocr")


def test_explicit_sign_and_operator_win_over_wording():
    assert ground("weight decreased by −0.7 kg", "0.7") == ("-0.7", "ocr")
    assert ground("difference 1.4 kg, P < 0.0001", "0.0001") == ("<0.0001", "ocr")


def test_number_between_direction_word_and_token_blocks_the_wording():
    assert ground("decreased by 0.7 ± 0.2 kg", "0.2") == ("0.2", "ocr")


def test_repeated_token_with_the_same_meaning_is_not_ambiguous():
    assert ground("-1.3 ± 0.1 % vs -1.1 ± 0.1 %", "0.1") == ("0.1", "ocr")


def test_visual_value_keeps_a_text_derived_sign_when_the_printed_token_has_none():
    assert _visual_value("0.7", "-0.7") == "-0.7"
    assert _visual_value("0.7", "+0.7") == "+0.7"


def test_visual_value_prefers_the_printed_sign_and_digits():
    assert _visual_value("−0.2", "0.2") == "-0.2"  # sign lost in the text layer, visible in the image
    assert _visual_value("P<0.001", "<0.001") == "<0.001"
    assert _visual_value("0.8", "-0.7") == "-0.8"  # OCR digits replaced by the image reading, sign kept


def test_arm_identity_ignores_counts_and_qualifier_words():
    assert _arm_agrees("premix BIAsp 30", "BIAsp 30 (n=444)")
    assert _arm_agrees("iGlarLixi", "iGlarLixi (n=443)")


def test_different_arms_disagree():
    assert not _arm_agrees("iGlar", "iGlarLixi (n=443)")
    assert not _arm_agrees("BIAsp 30", "iGlarLixi")


TABAK = ("weight decreased with iGlarLixi by an LS mean (SE) of 0.62 kg (0.24) and increased with basal bolus by "
         "an LS mean (SE) of 0.70 kg (0.24)")


def test_direction_wording_reaches_across_a_short_estimator_phrase():
    assert ground(TABAK, "0.62") == ("-0.62", "ocr")
    assert ground(TABAK, "0.70") == ("+0.70", "ocr")


def test_standard_error_in_brackets_is_not_signed():
    assert ground(TABAK, "0.24") == ("0.24", "ocr")


def test_numbers_printed_in_bound_panels_count_as_claim_content():
    from verifier.pipeline import _ungrounded_numbers
    from verifier.schemas import Claim
    claim = Claim(claim_id="C", text="Weight benefit across three panels", population=None, molecule=None,
                  cited_paper="x.pdf")
    summary = "The panel reports n=366 for SOLIQUA, which the abstract does not state."
    assert _ungrounded_numbers(summary, claim, {}, {}) == ["366"]
    assert _ungrounded_numbers(summary, claim, {}, {}, panel_texts="SOLIQUA (n=366) -0.7 kg") == []


def test_judge_cannot_contradict_a_claimed_quantity_that_code_never_compared():
    from verifier.enums import Relation
    from verifier.llm_schemas import AssertionJudgment, Assertion, ClaimedNumber
    from verifier.pipeline import _final_relation
    a = Assertion(id="A1", origin="text", origin_ref="~2x", claim_type="fold", statement="~2x greater", subject="s",
                  predicate="p", check_category="figure", numbers=[ClaimedNumber(raw="~2x", role="fold")])
    judged = AssertionJudgment(assertion_id="A1", relation="contradicted", policy_flags=[], reason_code="r", rationale="r",
                               evidence_ids=[])
    assert _final_relation(a, judged, None, 0) is Relation.UNKNOWN
    assert _final_relation(a, judged, Relation.CONTRADICTED, 1) is Relation.CONTRADICTED


def test_judge_cannot_turn_a_numeric_unclear_into_a_contradiction():
    from verifier.enums import Relation
    from verifier.llm_schemas import AssertionJudgment, Assertion, ClaimedNumber
    from verifier.pipeline import _final_relation
    a = Assertion(id="A9", origin="panel", origin_ref="P", claim_type="comparison", statement="p<0.0001", subject="s",
                  predicate="p", check_category="figure", numbers=[ClaimedNumber(raw="p<0.0001", role="p_value")])
    judged = AssertionJudgment(assertion_id="A9", relation="contradicted", reason_code="r", rationale="r")
    assert _final_relation(a, judged, Relation.UNKNOWN, 1) is Relation.UNKNOWN  # source P<0.001: not established
