from decimal import Decimal

from verifier.textnorm import contains_number, find_quote, normalize, number_tokens


def test_quote_found_across_line_breaks_returns_raw_slice():
    raw = "weight decreased with iGlarLixi by an LS mean (SE) of\n0.62 kg (0.24) and increased"
    span = find_quote("of 0.62 kg (0.24)", raw)
    assert span is not None
    assert raw[span[0]:span[1]] == "of\n0.62 kg (0.24)"


def test_quote_matches_ligature_in_source():
    raw = "Primary eﬃcacy end points were noninferiority"
    span = find_quote("efficacy end points", raw)
    assert raw[span[0]:span[1]] == "eﬃcacy end points"


def test_unicode_minus_matches_ascii_hyphen():
    raw = "changes from baseline to Week 30 in HbA1c were −1.63% for iGlarLixi"
    assert find_quote("were -1.63% for iGlarLixi", raw) is not None


def test_corrupted_minus_glyph_never_matches_a_sign():
    raw = "LS mean difference \x010.2% [97.5% CI \x010.4, \x010.1]"
    assert find_quote("difference -0.2%", raw) is None


def test_inserted_ellipsis_is_not_a_literal_quote():
    raw = "Mean body weight decreased by 0.7 kg with iGlarLixi and increased by 0.7 kg with iGlar"
    assert find_quote("decreased by 0.7 kg … increased by 0.7 kg", raw) is None


def test_ellipsis_really_present_in_source_is_kept():
    raw = "as previously described … see Methods"
    assert find_quote("described … see", raw) is not None


def test_line_break_hyphenation_fallback():
    raw = "meeting noninferior-\nity (least squares [LS] mean"
    span = find_quote("meeting noninferiority (least squares", raw)
    assert span is not None
    assert raw[span[0]:span[1]].startswith("meeting noninferior-")


def test_absent_quote_returns_none():
    assert find_quote("weight neutral", "body weight decreased by 0.7 kg") is None


def test_number_tokens_keep_sign_and_value():
    toks = number_tokens("−1.63% vs +0.7 kg and 11.3, OR 1.65 (1.25, 2.19); 1,761 mg")
    assert [t.value for t in toks] == [Decimal("-1.63"), Decimal("0.7"), Decimal("11.3"),
                                      Decimal("1.65"), Decimal("1.25"), Decimal("2.19"), Decimal("1761")]
    assert toks[1].sign_explicit is True
    assert toks[2].sign_explicit is False


def test_contains_number_respects_token_boundaries():
    assert contains_number("vs 11.3 and 1.34", "1.3") is False
    assert contains_number("decreased by 0.7 kg", "0.7") is True


def test_contains_number_respects_sign():
    assert contains_number("LS mean -1.3 ± 0.1", "-1.3") is True
    assert contains_number("decreased by 0.62 kg", "-0.62") is False


def test_number_after_corrupted_glyph_has_unknown_sign():
    toks = number_tokens(normalize("LS mean difference \x010.2% [97.5% CI \x010.4, \x010.1]"))
    assert [t.sign_unknown for t in toks if t.value in (Decimal("0.2"), Decimal("0.4"))] == [True, True]


def test_contains_number_fails_closed_when_sign_unknown():
    assert contains_number("difference \x010.2%", "0.2") is False
    assert contains_number("difference \x010.2%", "-0.2") is False


def test_contains_number_with_a_word_value_is_false_not_a_crash():
    assert contains_number("shown across three trial panels", "three") is False


def test_unreadable_marker_in_a_quote_matches_exactly_one_corrupted_glyph():
    raw = "type 2 diabetes (HbA1c \x017.5% and #10%) were randomized"
    span = find_quote("diabetes (HbA1c ⟦?⟧7.5% and #10%)", raw)
    assert span and raw[span[0]:span[1]] == "diabetes (HbA1c \x017.5% and #10%)"


def test_unreadable_marker_never_matches_a_real_character():
    assert find_quote("HbA1c ⟦?⟧7.5%", "HbA1c ≥7.5%") is None
    assert find_quote("HbA1c ≥7.5%", "HbA1c \x017.5%") is None  # still no matching across corrupted glyphs


def test_compound_word_broken_at_its_own_hyphen_matches():
    raw = "were switched to a BB regimen in the Get-\nGoal Duo-2 trial."
    span = find_quote("in the Get-Goal Duo-2 trial.", raw)
    assert span and raw[span[0]:span[1]] == "in the Get-\nGoal Duo-2 trial."
