"""Verbatim evidence-span checks for predictions and gold (used by eval.py).

A span passes if the provenance code locates it on the stated page of the stated document. Text-layer
spans are additionally checked against a second extractor (pypdf); OCR spans are labelled as OCR and
are checked against the crop-linked OCR text only (passing our own OCR check says nothing about any
external checker).
"""
from __future__ import annotations

import functools
import logging
import re
from pathlib import Path

from pypdf import PdfReader

from verifier.config import REPO_ROOT
from verifier.sources import ingest, locate_quote
from verifier.textnorm import normalize

logging.getLogger("pypdf").setLevel(logging.ERROR)  # font-encoding warnings are not span failures
SOURCE = re.compile(r"^(?P<doc>[^\s(]+)(?:\s*\((?P<kind>[a-z_]+)\))?")


@functools.lru_cache(maxsize=None)
def _doc(doc_id: str):
    return ingest(REPO_ROOT / "papers" / f"{doc_id}.pdf", cache_dir=REPO_ROOT / "corpus", doc_id=doc_id)


@functools.lru_cache(maxsize=None)
def _pypdf_page(doc_id: str, page: int) -> str:
    return PdfReader(str(REPO_ROOT / "papers" / f"{doc_id}.pdf")).pages[page - 1].extract_text() or ""


def _whitespace_free(text: str) -> str:
    return re.sub(r"\s+", "", normalize(text))


def _in_second_extractor(span: str, page_text: str) -> bool:
    """Whitespace-insensitive containment, like the brief's checker: pypdf splits some words at font
    changes ("HbA 1c"), which is an extraction artefact, not a different quote."""
    return _whitespace_free(span) in _whitespace_free(page_text)


def span_results(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        span, src = it.get("evidence_span"), it.get("evidence_source")
        row = {"claim_id": it.get("claim_id"), "located": None, "source_kind": None, "page_ok": None, "pypdf": None}
        if not span or not src:
            out.append(row)
            continue
        doc_id = SOURCE.match(src).group("doc")
        if not (REPO_ROOT / "papers" / f"{doc_id}.pdf").exists():
            row["located"] = False
            out.append(row)
            continue
        loc = locate_quote(_doc(doc_id), span, page_hint=it.get("evidence_page"))
        row["located"] = loc is not None
        if loc:
            row["source_kind"] = loc.source_kind
            row["page_ok"] = loc.page == it.get("evidence_page")
            if loc.source_kind == "text_layer":
                row["pypdf"] = _in_second_extractor(span, _pypdf_page(doc_id, loc.page))
        out.append(row)
    return out


def check_spans(preds: list[dict], gold: list[dict]) -> str:
    lines = []
    for name, items in (("predictions", preds), ("gold", gold)):
        rows = [r for r in span_results(items) if r["located"] is not None]
        ok = [r for r in rows if r["located"] and r["page_ok"]]
        text = [r for r in ok if r["source_kind"] == "text_layer"]
        lines.append(f"{name}: {len(ok)}/{len(rows)} evidence spans located verbatim on the stated page "
                     f"({len(text)} text layer, {len(ok) - len(text)} OCR-labelled); "
                     f"pypdf confirms {sum(bool(r['pypdf']) for r in text)}/{len(text)} text-layer spans")
        bad = [r["claim_id"] for r in rows if not (r["located"] and r["page_ok"])]
        if bad:
            lines.append(f"  not located: {', '.join(bad)}")
    return "\n".join(lines)
