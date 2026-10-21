"""Citation resolution against the runtime materials manifest.

Order: explicit filenames, DOIs, exact aliases (word-bounded), and only then a fuzzy title match
that needs an absolute score, a margin over the runner-up and a compatible year. Never forced.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

FILENAME = re.compile(r"[\w.\-]+\.pdf\b", re.I)
DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"<>,;)]+")
PAGE_HINT = re.compile(r"\b(?:page|p\.?)\s*(\d{1,4})\b", re.I)
YEAR = re.compile(r"\b(19|20)\d{2}\b")
FUZZY_MIN_SCORE = 80
FUZZY_MIN_MARGIN = 10


@dataclass
class Materials:
    documents: list[dict]
    promotional_material: str | None = None
    panel_registry: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def doc(self, doc_id: str) -> dict:
        return next(d for d in self.documents if d["doc_id"] == doc_id)


def load_materials(path: str | Path) -> Materials:
    data = json.loads(Path(path).read_text())
    return Materials(documents=data["documents"], promotional_material=data.get("promotional_material"),
                     panel_registry=data.get("panel_registry", {}), raw=data)


@dataclass
class Resolution:
    doc_ids: list[str] = field(default_factory=list)
    page_hints: dict[str, int] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    method: dict[str, str] = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return bool(self.doc_ids) and not self.unresolved and not self.ambiguous


def _add(res: Resolution, doc_id: str, pos: int, hits: list, method: str) -> None:
    if doc_id not in [d for _, d in hits]:
        hits.append((pos, doc_id))
        res.method[doc_id] = method


def resolve_citation(citation: str, materials: Materials) -> Resolution:
    res = Resolution()
    text = citation or ""
    hits: list[tuple[int, str]] = []
    by_name = {Path(d["path"]).name.lower(): d["doc_id"] for d in materials.documents}

    for m in FILENAME.finditer(text):
        doc_id = by_name.get(Path(m.group(0)).name.lower())
        if doc_id:
            _add(res, doc_id, m.start(), hits, "filename")
        else:
            res.unresolved.append(Path(m.group(0)).name)
    for m in DOI.finditer(text):
        found = [d["doc_id"] for d in materials.documents if d.get("doi") and d["doi"].lower() == m.group(0).lower()]
        if found:
            _add(res, found[0], m.start(), hits, "doi")
        else:
            res.unresolved.append(m.group(0))

    remainder = FILENAME.sub(" ", DOI.sub(" ", text))
    alias_hits: dict[str, list[tuple[int, str]]] = {}
    for d in materials.documents:
        for alias in d.get("aliases", []):
            for m in re.finditer(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", remainder, re.I):
                alias_hits.setdefault(alias.lower(), []).append((m.start(), d["doc_id"]))
    for alias, found in alias_hits.items():
        owners = sorted({doc_id for _, doc_id in found})
        if len(owners) > 1:
            res.ambiguous.extend(o for o in owners if o not in res.ambiguous)
        else:
            _add(res, owners[0], min(p for p, _ in found), hits, "alias")

    if not hits and not res.unresolved and not res.ambiguous and text.strip():
        _fuzzy(text, materials, res, hits)

    hits.sort()
    res.doc_ids = [doc_id for _, doc_id in hits]
    page = PAGE_HINT.search(text)
    if page and res.doc_ids:
        res.page_hints[res.doc_ids[0]] = int(page.group(1))
    return res


def _fuzzy(text: str, materials: Materials, res: Resolution, hits: list) -> None:
    years = {int(m.group(0)) for m in YEAR.finditer(text)}
    scored = sorted(((fuzz.token_set_ratio(text.lower(), (d.get("title") or "").lower()), d)
                     for d in materials.documents if d.get("title")), key=lambda x: -x[0])
    if not scored:
        res.unresolved.append(text)
        return
    best_score, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0
    year_ok = not years or not best.get("year") or best["year"] in years
    if best_score >= FUZZY_MIN_SCORE and best_score - runner_up >= FUZZY_MIN_MARGIN and year_ok:
        _add(res, best["doc_id"], 0, hits, f"fuzzy_title({best_score:.0f})")
    else:
        res.unresolved.append(text)
