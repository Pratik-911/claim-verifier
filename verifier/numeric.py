"""Deterministic numeric rules. All arithmetic in the pipeline happens here, in Decimal."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, getcontext

from verifier.enums import Outcome, Relation

getcontext().prec = 34

FOLD_TOLERANCE = Decimal("0.10")
FOLD_ABSTAIN_BAND = Decimal("0.02")
HBA1C_SLOPE = Decimal("10.929")
HBA1C_OFFSET = Decimal("2.15")


def decimals(s: str) -> int:
    s = s.strip().lstrip("+-−")
    return len(s.split(".")[1]) if "." in s else 0


def half_width(s: str) -> Decimal:
    return Decimal(1).scaleb(-decimals(s)) / 2


def _round(x: Decimal, places: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Comparison:
    outcome: Outcome
    flag: str | None = None


def compare_reported(source: str, claim: str) -> Comparison:
    """Compare a claimed number with a reported source number using rounding intervals."""
    s, c = Decimal(source.replace("−", "-")), Decimal(claim.replace("−", "-"))
    ds, dc = decimals(source), decimals(claim)
    h = half_width(source)
    lo, hi = s - h, s + h  # the source stands for [lo, hi)
    if dc < ds:
        eps = Decimal(1).scaleb(-(ds + 12))
        r_lo, r_hi = _round(lo, dc), _round(hi - eps, dc)
        if r_lo == r_hi == c:
            return Comparison(Outcome.MATCH)
        if r_lo <= c <= r_hi:
            return Comparison(Outcome.UNCLEAR, "rounding_boundary")
        return Comparison(Outcome.MISMATCH)
    if dc == ds:
        return Comparison(Outcome.MATCH if c == s else Outcome.MISMATCH)
    if c == s:
        return Comparison(Outcome.MATCH, "presentational_precision")
    if lo <= c < hi:
        return Comparison(Outcome.UNCLEAR, "unsupported_precision")
    return Comparison(Outcome.MISMATCH)


@dataclass(frozen=True)
class FoldResult:
    outcome: Outcome
    relative_error: Decimal


def fold_check(computed: Decimal, claimed: Decimal, tau: Decimal = FOLD_TOLERANCE,
               band: Decimal = FOLD_ABSTAIN_BAND) -> FoldResult:
    """Approximate fold claim ("~N x"). The abstention band around the tolerance edge wins."""
    r = abs(Decimal(computed) / Decimal(claimed) - 1)
    if tau - band <= r <= tau + band:
        return FoldResult(Outcome.UNCLEAR, r)
    return FoldResult(Outcome.MATCH if r < tau - band else Outcome.MISMATCH, r)


def ratio(numerator: str, denominator: str) -> Decimal | None:
    """Descriptive ratio of magnitudes; None when undefined (denominator within rounding of zero,
    or the two values have opposite signs, so a fold comparison is meaningless)."""
    a, b = Decimal(numerator.replace("−", "-")), Decimal(denominator.replace("−", "-"))
    if abs(b) <= half_width(denominator):
        return None
    if a != 0 and (a < 0) != (b < 0):
        return None
    return abs(a) / abs(b)


# --- inequalities --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Constraint:
    op: str  # one of "<", "<=", "=", ">=", ">"
    value: Decimal
    raw_value: str


_OPS = {"<": "<", "<=": "<=", "≤": "<=", "=": "=", ">=": ">=", "≥": ">=", ">": ">"}
_CONSTRAINT = re.compile(r"(<=|>=|≤|≥|<|>|=)\s*([0-9]*\.?[0-9]+)")


def parse_constraint(text: str) -> Constraint:
    m = _CONSTRAINT.search(text.replace("−", "-"))
    if not m:
        raise ValueError(f"no inequality found in {text!r}")
    return Constraint(_OPS[m.group(1)], Decimal(m.group(2)), m.group(2))


@dataclass(frozen=True)
class _Interval:
    lo: Decimal | None  # None = -infinity
    lo_closed: bool
    hi: Decimal | None  # None = +infinity
    hi_closed: bool


def _interval(c: Constraint) -> _Interval:
    v = c.value
    if c.op == "<":
        return _Interval(None, False, v, False)
    if c.op == "<=":
        return _Interval(None, False, v, True)
    if c.op == ">":
        return _Interval(v, False, None, False)
    if c.op == ">=":
        return _Interval(v, True, None, False)
    h = half_width(c.raw_value)  # a reported "=" value stands for its rounding interval
    return _Interval(v - h, True, v + h, False)


def _lo_ge(a: _Interval, b: _Interval) -> bool:
    """a's lower end is at or above b's lower end (a starts inside b from below)."""
    if b.lo is None:
        return True
    if a.lo is None:
        return False
    return a.lo > b.lo or (a.lo == b.lo and (b.lo_closed or not a.lo_closed))


def _hi_le(a: _Interval, b: _Interval) -> bool:
    if b.hi is None:
        return True
    if a.hi is None:
        return False
    return a.hi < b.hi or (a.hi == b.hi and (b.hi_closed or not a.hi_closed))


def _disjoint(a: _Interval, b: _Interval) -> bool:
    def before(x: _Interval, y: _Interval) -> bool:  # x entirely below y
        if x.hi is None or y.lo is None:
            return False
        return x.hi < y.lo or (x.hi == y.lo and not (x.hi_closed and y.lo_closed))
    return before(a, b) or before(b, a)


def inequality_relation(source: Constraint, claim: Constraint) -> Relation:
    """Entailment of a claimed constraint by the source constraint, computed on intervals."""
    s, c = _interval(source), _interval(claim)
    if _lo_ge(s, c) and _hi_le(s, c):
        return Relation.ENTAILED
    if _disjoint(s, c):
        return Relation.CONTRADICTED
    return Relation.UNKNOWN


# --- counts and units ------------------------------------------------------------------------------

def proportion_pct(n: int, total: int) -> Decimal:
    """Unadjusted proportion in percent. Callers must first confirm the denominator and estimator."""
    return Decimal(n) * 100 / Decimal(total)


def hba1c_pct_to_mmol(pct: Decimal) -> Decimal:
    """Absolute HbA1c NGSP % -> IFCC mmol/mol (master equation)."""
    return (Decimal(pct) - HBA1C_OFFSET) * HBA1C_SLOPE


def hba1c_change_pct_to_mmol(delta_pct: Decimal) -> Decimal:
    """Changes, differences and SEs scale by the slope only (no offset)."""
    return Decimal(delta_pct) * HBA1C_SLOPE
