"""Bind explicitly named promotional panels through the document-level panel registry (plan §0.4).

No automatic localisation: panels are bound only when the declared scope includes panels AND the
claim either lists panel ids (`panel_refs`) or refers to panels/charts and names them by registry
alias. Anything that cannot be bound exactly is reported as unresolved (=> panel_unresolved).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from verifier.resolve import Materials

PANEL_WORDS = re.compile(r"\b(panels?|charts?|graphs?|figures?|graphics?)\b", re.I)
COUNT = re.compile(r"\b(two|three|four|five|2|3|4|5)\s+(?:\w+\s+)?(?:panels|charts|graphs|figures)\b", re.I)
NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "2": 2, "3": 3, "4": 4, "5": 5}


@dataclass
class PanelBinding:
    triggered: bool = False
    panels: list[dict] = field(default_factory=list)
    context: list[dict] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def _registry(materials: Materials) -> list[dict]:
    if not materials.promotional_material:
        return []
    return materials.panel_registry.get(materials.promotional_material, [])


def bind_panels(claim_text: str, panel_refs: list[str] | None, materials: Materials, scope: str) -> PanelBinding:
    b = PanelBinding()
    if scope != "claim_and_referenced_panels":
        return b
    registry = _registry(materials)
    by_id = {p["panel_id"]: p for p in registry}
    if panel_refs:
        b.triggered = True
        for ref in panel_refs:
            (b.panels.append(by_id[ref]) if ref in by_id else b.unresolved.append(ref))
    elif PANEL_WORDS.search(claim_text or ""):
        b.triggered = True
        hits: list[tuple[int, dict]] = []
        for p in registry:
            if p.get("kind") != "claim_panel":
                continue
            positions = [m.start() for a in p.get("aliases", [])
                         for m in re.finditer(r"(?<!\w)" + re.escape(a) + r"(?!\w)", claim_text, re.I)]
            if positions:
                hits.append((min(positions), p))
        b.panels = [p for _, p in sorted(hits, key=lambda h: h[0])]
        if not b.panels:
            b.unresolved.append("panel reference without a registry-named panel")
        count = COUNT.search(claim_text)
        if count and NUMBERS[count.group(1).lower()] != len(b.panels):
            b.unresolved.append(f"claim names {count.group(1)} panels but {len(b.panels)} were bound")
    ctx_ids = [c for p in b.panels for c in p.get("context", [])]
    b.context = [by_id[c] for c in dict.fromkeys(ctx_ids) if c in by_id]
    return b
