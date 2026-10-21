"""Offset-preserving text normalisation, literal-quote location and sign-aware number tokens.

Matching is tolerant to whitespace, ligatures, quote styles and dash variants, but never to
corrupted glyphs: a control character in the source (e.g. SoliMix's ``\\x01`` minus) stays a
distinct symbol, so a quote that assumes a sign there cannot match.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹣－"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}
_CORRUPT = "�"


def _norm_char(ch: str) -> str:
    """Normalise one source character to zero or more matching characters."""
    if ch == "­":  # soft hyphen
        return ""
    if ch.isspace():
        return " "
    if unicodedata.category(ch) in ("Cc", "Co", "Cs"):
        return _CORRUPT
    out = unicodedata.normalize("NFKC", ch)
    return out.translate(_DASHES).translate(_QUOTES)


@dataclass(frozen=True)
class Normalized:
    text: str
    to_raw: tuple[int, ...]  # to_raw[i] = index in the raw string of normalised char i


def normalize_with_map(raw: str, *, join_hyphenation: bool | str = False) -> Normalized:
    """join_hyphenation: True drops a line-break hyphen ("partic- ipants" -> "participants"); "keep" keeps
    the hyphen of a compound broken at it ("Get- Goal" -> "Get-Goal")."""
    chars: list[str] = []
    to_raw: list[int] = []
    for i, ch in enumerate(raw):
        for out in _norm_char(ch):
            if out == " " and chars and chars[-1] == " ":
                continue  # collapse whitespace runs
            chars.append(out)
            to_raw.append(i)
    if join_hyphenation:
        # "noninferior- ity" (a hyphen at a line break between letters) -> "noninferiority"
        k = 0
        merged_chars: list[str] = []
        merged_map: list[int] = []
        while k < len(chars):
            if (chars[k] == "-" and k + 1 < len(chars) and chars[k + 1] == " "
                    and k > 0 and chars[k - 1].isalpha() and k + 2 < len(chars) and chars[k + 2].isalpha()):
                if join_hyphenation == "keep":
                    merged_chars.append("-")
                    merged_map.append(to_raw[k])
                k += 2
                continue
            merged_chars.append(chars[k])
            merged_map.append(to_raw[k])
            k += 1
        chars, to_raw = merged_chars, merged_map
    return Normalized("".join(chars), tuple(to_raw))


def normalize(text: str) -> str:
    return normalize_with_map(text).text.strip()


UNREADABLE_MARK = "⟦?⟧"  # how a corrupted or flagged glyph is shown to the model


def find_quote(quote: str, raw: str) -> tuple[int, int] | None:
    """Return (start, end) offsets in ``raw`` whose normalised form equals the normalised quote.

    The unreadable marker in a quote matches exactly one corrupted glyph in the source and nothing
    else, so a model that copies the marker still locates the raw text (and no sign is assumed)."""
    quote = quote.replace(UNREADABLE_MARK, "\x01")
    for join in (False, True, "keep"):
        page = normalize_with_map(raw, join_hyphenation=join)
        q = normalize_with_map(quote, join_hyphenation=join).text.strip()
        if not q:
            return None
        i = page.text.find(q)
        if i >= 0:
            return page.to_raw[i], page.to_raw[i + len(q) - 1] + 1
    return None


_NUM = re.compile(r"(?<![\w.])([+\-−–]?)(\d{1,3}(?:,\d{3})+(?!\d)|\d+(?:\.\d+)?|\.\d+)(?![\d])")


@dataclass(frozen=True)
class NumberToken:
    value: Decimal
    raw: str
    start: int
    end: int
    sign_explicit: bool
    sign_unknown: bool = False  # preceded by a corrupted glyph: the sign may have been lost


def number_tokens(text: str) -> list[NumberToken]:
    toks = []
    for m in _NUM.finditer(text):
        sign, digits = m.group(1), m.group(2).replace(",", "")
        value = Decimal(digits)
        if sign in ("-", "−", "–"):
            value = -value
        sign_unknown = not sign and m.start() > 0 and text[m.start() - 1] == _CORRUPT
        toks.append(NumberToken(value, m.group(0), m.start(), m.end(), bool(sign), sign_unknown))
    return toks


def contains_number(text: str, value: str) -> bool:
    """True if ``value`` occurs as a whole number token with the same sign and digits.

    Tokens whose sign is unknown (corrupted glyph before them) never match: fail closed.
    """
    try:
        target = Decimal(value.replace("−", "-"))
    except InvalidOperation:
        return False  # worded numbers ("three") are never anchors
    want_digits = value.lstrip("+-−")
    for t in number_tokens(normalize(text)):
        if t.sign_unknown:
            continue
        if t.value == target and t.raw.lstrip("+-−–").replace(",", "") == want_digits:
            return True
    return False
