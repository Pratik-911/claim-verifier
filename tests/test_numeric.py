from decimal import Decimal as D

import pytest

from verifier.enums import Outcome, Relation
from verifier.numeric import (
    Constraint, compare_reported, fold_check, hba1c_change_pct_to_mmol,
    hba1c_pct_to_mmol, inequality_relation, parse_constraint, proportion_pct, ratio,
)


# --- reported-value precision (both directions) ---------------------------------------------

@pytest.mark.parametrize("source,claim,expected", [
    ("1.234", "1.23", Outcome.MATCH),      # fewer claimed digits, whole interval rounds to claim
    ("1.235", "1.23", Outcome.UNCLEAR),    # source interval straddles the rounding boundary
    ("1.234", "1.24", Outcome.MISMATCH),
    ("42.2", "42.2", Outcome.MATCH),
    ("42.2", "42.3", Outcome.MISMATCH),
    ("42.2", "42.24", Outcome.UNCLEAR),    # extra precision merely compatible
    ("42.2", "42.29", Outcome.MISMATCH),   # outside the source rounding interval
    ("-1.3", "-1.3", Outcome.MATCH),
    ("-1.3", "1.3", Outcome.MISMATCH),     # signs are never stripped
])
def test_compare_reported(source, claim, expected):
    assert compare_reported(source, claim).outcome is expected


def test_trailing_zeros_are_equal_but_flagged_presentational():
    r = compare_reported("42.2", "42.20")
    assert r.outcome is Outcome.MATCH
    assert r.flag == "presentational_precision"


# --- approximate fold claims: abstention band takes precedence --------------------------------

@pytest.mark.parametrize("computed,claimed,expected", [
    (D("1.63") / D("1.34"), D(2), Outcome.MISMATCH),   # C3: 1.2164x vs ~2x
    (D("1.83"), D(2), Outcome.UNCLEAR),                # r = 0.085 is inside [0.08, 0.12]
    (D("1.90"), D(2), Outcome.MATCH),
    (D("2.24"), D(2), Outcome.UNCLEAR),                # r = 0.12 is on the band edge
])
def test_fold_check(computed, claimed, expected):
    assert fold_check(computed, claimed).outcome is expected


def test_ratio_undefined_when_denominator_within_rounding_of_zero():
    assert ratio("1.2", "0.0") is None
    assert ratio("1.2", "0.04") is not None


def test_ratio_uses_magnitudes_of_signed_changes():
    assert ratio("-1.63", "-1.34").quantize(D("0.0001")) == D("1.2164")


# --- inequalities as constraints --------------------------------------------------------------

@pytest.mark.parametrize("source,claim,expected", [
    ("p<0.001", "p<0.01", Relation.ENTAILED),
    ("p<0.001", "p<0.001", Relation.ENTAILED),
    ("p<0.001", "p<0.0001", Relation.UNKNOWN),       # stronger bound not established
    ("p=0.00001", "p<0.0001", Relation.ENTAILED),
    ("p=0.005", "p<0.001", Relation.CONTRADICTED),
    ("p=0.005", "p<0.01", Relation.ENTAILED),
    ("p≤0.001", "p<0.001", Relation.UNKNOWN),        # < and ≤ kept distinct
    ("P < 0.0001", "p<0.0001", Relation.ENTAILED),
])
def test_inequality_relation(source, claim, expected):
    assert inequality_relation(parse_constraint(source), parse_constraint(claim)) is expected


def test_parse_constraint_reads_operator_and_value():
    c = parse_constraint("P ≤ 0.05")
    assert c == Constraint("<=", D("0.05"), "0.05")


# --- counts and units -------------------------------------------------------------------------

def test_proportion_from_counts_full_precision():
    assert proportion_pct(187, 443).quantize(D("0.0001")) == D("42.2122")


def test_hba1c_absolute_uses_offset_but_change_uses_slope_only():
    assert hba1c_pct_to_mmol(D("8.6")).quantize(D("0.1")) == D("70.5")
    assert hba1c_change_pct_to_mmol(D("-1.3")).quantize(D("0.1")) == D("-14.2")
