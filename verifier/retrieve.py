"""Candidate-page retrieval: lexical BM25 by meaning, numeric anchors, and figure/table pages.

BM25 is lexical (not semantic). BM25Plus is used because Okapi's IDF is zero for a term found on
half the pages of a two-page document (e.g. an abstract), which would return nothing. It is combined with numeric anchors (pages containing a claimed
number) and caption pages that share query terms, because an invented number may appear nowhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rank_bm25 import BM25Plus

from verifier.sources import Document, Page
from verifier.textnorm import contains_number, normalize

TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
CAPTION = re.compile(r"\b(fig(?:ure)?\.?\s*\d|table\s*\d)", re.I)
STOP = frozenset("a an and are as at be by for from in is it of on or that the to vs was were with".split())


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN.findall(normalize(text).lower()) if t not in STOP]


def page_text(page: Page) -> str:
    return page.raw_text + "\n" + page.ocr_text()


@dataclass
class Candidates:
    pages: list[int]
    reasons: dict[int, list[str]] = field(default_factory=dict)
    searched: int = 0


def candidate_pages(doc: Document, query: str, anchors: list[str] | None = None, *, k: int = 4,
                    page_hint: int | None = None, exclude: list[int] | None = None) -> Candidates:
    exclude = set(exclude or [])
    texts = [page_text(p) for p in doc.pages]
    corpus = [tokens(t) or ["_"] for t in texts]
    bm25 = BM25Plus(corpus)
    q = tokens(query)
    scores = bm25.get_scores(q) if q else [0.0] * len(corpus)
    reasons: dict[int, list[str]] = {}

    def add(num: int, why: str) -> None:
        if num not in exclude:
            reasons.setdefault(num, []).append(why)

    if page_hint and 1 <= page_hint <= len(doc.pages):
        add(page_hint, "page_hint")
    for anchor in anchors or []:
        for p, t in zip(doc.pages, texts):
            if contains_number(t, anchor):
                add(p.number, f"anchor:{anchor}")
    ranked = sorted(range(len(scores)), key=lambda i: -scores[i])
    taken = 0
    for i in ranked:
        if taken >= k:
            break
        if not set(q) & set(corpus[i]):  # BM25Plus gives every page a baseline; require term overlap
            continue
        if doc.pages[i].number not in exclude:
            add(doc.pages[i].number, f"bm25:{scores[i]:.2f}")
            taken += 1
    qset = set(q)
    captions = [p for p, t, toks in zip(doc.pages, texts, corpus)
                if CAPTION.search(t) and len(qset & set(toks)) >= max(2, len(qset) // 3)]
    for p in captions[:2]:
        add(p.number, "figure_or_table")
    order = sorted(reasons, key=lambda n: (0 if "page_hint" in reasons[n] else 1,
                                           0 if any(r.startswith("anchor") for r in reasons[n]) else 1,
                                           -scores[n - 1]))
    return Candidates(order, reasons, len(doc.pages))


def candidate_pages_for_assertions(doc: Document, queries: list[str], anchors: list[str] | None = None, *,
                                   per_query_k: int = 2, page_hint: int | None = None, cap: int = 7,
                                   exclude: list[int] | None = None) -> Candidates:
    """Union of per-assertion candidates, so a population or design assertion is not starved by the
    numeric assertion's pages. Page 1 (title, abstract: population and design summary) is always offered."""
    reasons: dict[int, list[str]] = {}
    best: dict[int, float] = {}
    exclude = set(exclude or [])
    if 1 not in exclude:
        reasons.setdefault(1, []).append("first_page")
    for i, q in enumerate(queries):
        c = candidate_pages(doc, q, anchors if i == 0 else None, k=per_query_k, page_hint=page_hint,
                            exclude=list(exclude))
        for n in c.pages:
            reasons.setdefault(n, []).extend(f"q{i}:{w}" for w in c.reasons[n])
            score = max((float(w.split(":")[1]) for w in c.reasons[n] if w.startswith("bm25:")), default=0.0)
            best[n] = max(best.get(n, 0.0), score)
    order = sorted(reasons, key=lambda n: (0 if any("page_hint" in w for w in reasons[n]) else 1,
                                           0 if any("anchor" in w for w in reasons[n]) else 1,
                                           -best.get(n, 0.0)))
    if 1 in order:  # keep page 1 but never let it push out a hinted or anchored page
        order.remove(1)
        order.insert(min(len(order), 2), 1)
    return Candidates(order[:cap], reasons, len(doc.pages))
