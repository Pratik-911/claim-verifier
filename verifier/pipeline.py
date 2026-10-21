"""Per-claim verification pipeline: resolve -> panels -> interpret -> retrieve -> extract ->
ground -> visual read -> compute -> judge -> policy. Every arithmetic step lives in verifier.compute."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from verifier.compute import GroundedFact, LinkResult, assertion_numeric_relation, evaluate_link, fold_settings
from verifier.config import resolve
from verifier.enums import Outcome, Relation
from verifier.llm import CLAIM_DEADLINE, LLM, ImagePart, LLMError, make_client
from verifier.llm_schemas import Assertion, Extraction, Interpretation, Judgment, VisualReading
from verifier.logic import MATERIAL_FLAGS, And, Leaf, Not, decide
from verifier.panels import bind_panels
from verifier.resolve import Materials, load_materials, resolve_citation
from verifier.retrieve import CAPTION, candidate_pages, candidate_pages_for_assertions
from verifier.schemas import Checks, Claim, EvidenceRecord, Prediction, operational_abstention
from verifier.sources import Document, Location, _masked, ingest, locate_quote
from verifier.textnorm import UNREADABLE_MARK, find_quote, normalize, number_tokens

PROMPTS = Path(__file__).parent / "prompts"
QUANT_ROLES = {"arm_value", "effect_estimate", "ci_low", "ci_high", "p_value", "percent", "count", "fold", "threshold"}
# which claimed number best represents a figure assertion in the `computed` field
ROLE_PRIORITY = {"fold": 0, "effect_estimate": 1, "arm_value": 2, "percent": 3, "count": 4, "p_value": 5,
                 "ci_low": 6, "ci_high": 6}
ANCHOR_ROLES = {"arm_value", "effect_estimate", "ci_low", "ci_high", "p_value", "percent", "count", "threshold"}
CHECK_NAMES = ("figure", "population", "drug_indication", "actually_stated")
MAX_IMAGES_PER_DOC = 3
OP_BEFORE = re.compile(r"([<>≤≥=])\s*$")
# direction word right before an unsigned number ("decreased 0.7"), or followed by up to eight words or short
# bracketed labels and then "by"/"of" ("decreased with iGlarLixi by an LS mean (SE) of 0.62"); the window
# stops at numbers and punctuation, and "from"/"to"/"vs"/"than" never sign a level ("from 8.1 to 6.8")
DIRECTION = re.compile(
    r"\b(?:(?P<neg>decreas\w*|reduc\w*|fell|fall\w*|drop\w*|declin\w*|lower\w*|loss|lost|los(?:e|ing))"
    r"|(?P<pos>increas\w*|rose|ris(?:e|es|ing)|gain\w*|rais\w*|grew))"
    r"(?:\s+|(?:\s+(?!(?:from|to|vs|versus|than)\b)(?:[A-Za-z][\w-]*|\([A-Za-z ]{1,12}\))){0,8}\s+(?:by|of)\s+)$",
    re.I)


def prompt(name: str) -> tuple[str, str]:
    text = (PROMPTS / f"{name}.md").read_text()
    return text, hashlib.sha256(text.encode()).hexdigest()[:12]


@dataclass
class Context:
    cfg: dict
    materials: Materials
    llm: object
    scope: str
    corpus_dir: Path
    _docs: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def doc(self, doc_id: str) -> Document:
        with self._lock:
            if doc_id not in self._docs:
                meta = self.materials.doc(doc_id)
                self._docs[doc_id] = ingest(resolve(meta["path"]), cache_dir=self.corpus_dir, doc_id=doc_id,
                                            dpi=self.cfg["ingest"]["ocr_dpi"])
            return self._docs[doc_id]


def run_claims(claims: list[Claim], cfg: dict, *, backend: str, run_id: str, llm=None,
               materials: Materials | None = None) -> list[Prediction]:
    materials = materials or load_materials(resolve(cfg["run"]["materials"]))
    if llm is None:
        client = make_client(backend, cfg.get("auth", {}).get("location", "global"),
                             timeout_s=float(cfg["models"].get("request_timeout_s", 180)))
        llm = LLM(client, resolve(cfg["run"]["llm_log"]), run_id, cfg["models"].get("max_retries", 3))
    ctx = Context(cfg, materials, llm, cfg["run"]["scope"], resolve(cfg["run"]["corpus_dir"]))
    with ThreadPoolExecutor(max_workers=int(cfg["run"].get("max_workers", 4))) as pool:
        return list(pool.map(lambda c: _safe_verify(c, ctx), claims))


def _safe_verify(claim: Claim, ctx: Context) -> Prediction:
    import time
    token = CLAIM_DEADLINE.set(time.monotonic() + float(ctx.cfg["run"].get("claim_timeout_s", 600)))
    try:
        return verify_claim(claim, ctx)
    except LLMError as e:
        return operational_abstention(claim, e.kind, str(e), ctx.scope)
    except Exception as e:  # never crash the batch; keep the traceback for debugging
        log = resolve("outputs/errors.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as f:
            f.write(f"--- {dt.datetime.now(dt.timezone.utc).isoformat()} {claim.claim_id}\n{traceback.format_exc()}\n")
        return operational_abstention(claim, "internal_error", f"{type(e).__name__}: {e}", ctx.scope)
    finally:
        CLAIM_DEADLINE.reset(token)


# ------------------------------------------------------------------------------------------ helpers
def _semantic_abstention(claim: Claim, scope: str, kind: str, reason: str, **extra) -> Prediction:
    return Prediction(claim_id=claim.claim_id, verdict="cannot_verify", checks=Checks(), reason=reason,
                      scope=scope, abstention_type=kind, **extra)


def _digits(raw: str) -> str | None:
    toks = number_tokens(normalize(raw))
    return toks[0].raw.lstrip("+-−–").replace(",", "") if toks else None


def _number_present(text: str, digits: str) -> bool:
    return any(t.raw.lstrip("+-−–").replace(",", "") == digits for t in number_tokens(normalize(text)))


def _panel_text(doc: Document, panel: dict) -> str:
    page = doc.pages[panel["page"] - 1]
    x0, y0, x1, y1 = panel["bbox"]
    lines = [ln for r in page.ocr_regions for ln in r.lines
             if x0 <= (ln.bbox[0] + ln.bbox[2]) / 2 <= x1 and y0 <= (ln.bbox[1] + ln.bbox[3]) / 2 <= y1]
    return "\n".join(ln.text for ln in lines)


def _page_block(doc: Document, number: int) -> str:
    page = doc.pages[number - 1]
    text = _masked(page).replace("\x01", UNREADABLE_MARK)
    parts = []
    if text.strip():
        parts.append(f"=== Page {number} (text layer) ===\n{text}")
    for r in page.ocr_regions:
        parts.append(f"=== Page {number} [OCR transcription of raster region {r.id}] ===\n{r.text}")
    return "\n".join(parts)


def _render(doc: Document, page_no: int, clip: tuple | None, dpi: int, corpus_dir: Path) -> ImagePart:
    with pymupdf.open(doc.path) as pdf:
        page = pdf[page_no - 1]
        rect = pymupdf.Rect(clip) & page.rect if clip else page.rect
        png = page.get_pixmap(dpi=dpi, clip=rect).tobytes("png")
    sha = hashlib.sha256(png).hexdigest()
    path = corpus_dir / doc.sha256 / "renders" / f"p{page_no}-{sha[:16]}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(png)
    return ImagePart(png, "image/png", str(path))


# ------------------------------------------------------------------------------------------ stages
def _interpret(claim: Claim, ctx: Context, panels_payload: list[dict]) -> Interpretation:
    system, _ = prompt("interpret")
    payload = {"claim_text": claim.text, "population": claim.population, "molecule": claim.molecule,
               "scope": ctx.scope, "panels": panels_payload}
    return ctx.llm.generate_json(stage="interpret", model=ctx.cfg["models"]["extract"], system=system,
                                 parts=[json.dumps(payload, ensure_ascii=False, indent=1)], schema=Interpretation,
                                 thinking=ctx.cfg["models"].get("interpret_thinking", ctx.cfg["models"]["extract_thinking"]),
                                 claim_id=claim.claim_id)


def _check_interpretation(claim: Claim, interp: Interpretation, panel_texts: str) -> tuple[list[str], list[str]]:
    """Drop numbers absent from the claim (anti-hallucination) and find claim numbers left uncovered."""
    allowed = " ".join(x for x in (claim.text, claim.population, claim.molecule, panel_texts) if x)
    unresolved, gates = [], []
    covered: set[str] = set()
    for a in interp.assertions:
        kept = []
        for n in a.numbers:
            d = _digits(n.raw)
            if d is None or _number_present(allowed, d) or (n.role == "other" and n.raw in allowed):
                kept.append(n)
                if d:
                    covered.add(d)
            else:
                unresolved.append(f"interpreter number not in claim: {n.raw}")
        a.numbers = kept
    for tok in number_tokens(normalize(claim.text)):
        d = tok.raw.lstrip("+-−–").replace(",", "")
        if d not in covered:
            unresolved.append(f"claim number not covered by any assertion: {tok.raw}")
            if "coverage_gap" not in gates:
                gates.append("coverage_gap")
    return unresolved, gates


def _assertions_payload(assertions: list[Assertion]) -> list[dict]:
    return [a.model_dump(exclude={"search_terms"}) for a in assertions]


def _assertions_for_doc(interp: Interpretation, doc_id: str, panels: list[dict], cited: list[str]) -> list[Assertion]:
    """Claim-text, population and molecule assertions go to every cited document; a panel's assertions go
    only to the cited documents its registry entry names as sources (to all of them if it names none)."""
    sources = {p["panel_id"]: [d for d in p.get("sources", []) if d in cited] for p in panels}
    return [a for a in interp.assertions
            if a.origin != "panel" or not sources.get(a.origin_ref) or doc_id in sources[a.origin_ref]]


def _extract(claim: Claim, ctx: Context, doc: Document, assertions: list[Assertion], pages: list[int],
             role: str) -> Extraction:
    system, _ = prompt("extract")
    meta = ctx.materials.doc(doc.doc_id)
    header = {"document": {"doc_id": doc.doc_id, "doc_type": meta.get("doc_type"), "title": meta.get("title"),
                           "role": role}, "assertions": _assertions_payload(assertions)}
    text = "\n\n".join(_page_block(doc, p) for p in pages)
    image_pages = [p for p in pages if doc.pages[p - 1].ocr_regions or doc.pages[p - 1].flagged
                   or CAPTION.search(doc.pages[p - 1].raw_text)][:MAX_IMAGES_PER_DOC]
    images = [_render(doc, p, None, 150, ctx.corpus_dir) for p in image_pages]
    parts = [json.dumps(header, ensure_ascii=False, indent=1), text] + images
    return ctx.llm.generate_json(stage="extract", model=ctx.cfg["models"]["extract"], system=system, parts=parts,
                                 schema=Extraction, thinking=ctx.cfg["models"]["extract_thinking"],
                                 claim_id=claim.claim_id, doc_id=doc.doc_id)


def _token_value(norm: str, tok) -> tuple[str | None, str | None]:
    """Signed value of one number token: explicit sign, then a preceding operator, then direction wording."""
    if tok.sign_unknown or (norm[:tok.start].rstrip().endswith("�") and not tok.sign_explicit):
        return None, "sign_unknown"
    op = OP_BEFORE.search(norm[:tok.start])
    if op and op.group(1) != "=":
        return op.group(1) + tok.raw, None
    value = tok.raw
    if not tok.sign_explicit:
        d = DIRECTION.search(norm[:tok.start])
        if d:  # "decreased by 0.7" is a change of -0.7; "increased by 0.7" of +0.7
            value = ("-" if d.group("neg") else "+") + tok.raw
    return value.replace("−", "-").replace("–", "-"), None


def _ground_fact(fact_raw: str, loc: Location, doc: Document, context: str | None = None) -> tuple[str | None, str]:
    """Value and status of a fact, read from the grounded source text (never from the LLM's copy).

    Every occurrence of the fact's digits in the quote is read. If they mean different things (the same
    0.7 after "decreased by" and after "increased by"), the fact's verbatim context must pick one;
    otherwise the fact is ambiguous and unusable."""
    digits = _digits(fact_raw)
    if digits is None:
        return None, "not_in_quote"
    text = _masked(doc.pages[loc.page - 1])[loc.start:loc.end] if loc.source_kind == "text_layer" else loc.text
    norm = normalize(text)
    toks = [t for t in number_tokens(norm) if t.raw.lstrip("+-−–").replace(",", "") == digits]
    if not toks:
        return None, "not_in_quote"
    span = find_quote(context, norm) if context else None
    if span:
        toks = [t for t in toks if span[0] <= t.start < span[1]] or toks
    readings = {_token_value(norm, t) for t in toks}
    if len(readings) > 1:
        return None, "ambiguous_occurrence"
    value, problem = readings.pop()
    if problem:
        return digits, problem
    return value, ("ocr" if loc.source_kind == "ocr" else "ok")


def _visual(claim: Claim, ctx: Context, doc: Document, page_no: int, bbox, requests: list[dict]) -> VisualReading:
    system, _ = prompt("visual")
    pw = doc.pages[page_no - 1].width
    clip = (0, max(0, bbox[1] - 150), pw, bbox[3] + 150) if bbox else None
    image = _render(doc, page_no, clip, ctx.cfg["ingest"]["crop_dpi"], ctx.corpus_dir)
    return ctx.llm.generate_json(stage="visual", model=ctx.cfg["models"]["extract"], system=system,
                                 parts=[json.dumps({"facts": requests}, ensure_ascii=False, indent=1), image],
                                 schema=VisualReading, thinking="low", claim_id=claim.claim_id, doc_id=doc.doc_id)


def _same_digits(a: str | None, b: str | None) -> bool:
    return bool(a and b and _digits(a) == _digits(b))


def _locate_evidence(doc: Document, quote: str, page: int) -> list[Location] | None:
    """The quote as one verbatim span; failing that (figure text whose lines the model put in another order
    than the text layer), every line located verbatim on the same page, one record per line."""
    loc = locate_quote(doc, quote, page_hint=page)
    if loc is not None:
        return [loc]
    lines = [ln.strip() for ln in quote.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    locs = [locate_quote(doc, ln, page_hint=page) for ln in lines]
    if any(x is None or x.page != page for x in locs):
        return None
    return locs


def _ground_in_records(fact, by_rid: dict[str, Location], doc: Document) -> tuple[str, str | None, str]:
    """Ground a fact in the record(s) of its evidence item; readings that differ across lines are ambiguous."""
    hits = [(rid, *_ground_fact(fact.raw, loc, doc, context=fact.context)) for rid, loc in by_rid.items()]
    found = [h for h in hits if h[2] != "not_in_quote"]
    if not found:
        return hits[0]
    if len({(v, s) for _, v, s in found}) > 1:
        return found[0][0], None, "ambiguous_occurrence"
    return found[0]


def _visual_value(printed: str, text_value: str | None) -> str:
    """The image's reading replaces the digits; a sign or operator read from the source wording is kept
    when the printed token itself carries none."""
    value = normalize(printed).replace(" ", "").lstrip("pP=")
    if value[:1] in "+-<>≤≥" or not text_value or text_value[:1] not in "+-<>≤≥":
        return value
    return text_value[0] + value


def _arm_tokens(label: str) -> set[str]:
    label = re.sub(r"\([^)]*\)", " ", normalize(label).lower())  # drop "(n=444)" and similar
    return set(re.findall(r"[a-z0-9.]+", label))


def _arm_agrees(extracted: str | None, printed: str | None) -> bool:
    """Same arm if one label's words are all in the other ("BIAsp 30" vs "premix BIAsp 30 (n=444)");
    whole words only, so "iGlar" never matches "iGlarLixi"."""
    if not extracted or not printed:
        return True
    x, y = _arm_tokens(extracted), _arm_tokens(printed)
    return bool(x and y) and (x <= y or y <= x)


# ------------------------------------------------------------------------------------------ main
def verify_claim(claim: Claim, ctx: Context) -> Prediction:
    res = resolve_citation(claim.cited_paper or "", ctx.materials)
    if not res.resolved:
        detail = "; ".join(filter(None, [
            f"unresolved: {', '.join(res.unresolved)}" if res.unresolved else "",
            f"ambiguous between: {', '.join(res.ambiguous)}" if res.ambiguous else "",
            "no citation" if not (claim.cited_paper or "").strip() else ""]))
        return _semantic_abstention(claim, ctx.scope, "citation_unresolved",
                                    f"Cited source could not be identified ({detail}); nothing was verified.")

    binding = bind_panels(claim.text, claim.panel_refs, ctx.materials, ctx.scope)
    panels_payload, panel_texts = [], ""
    if binding.triggered and ctx.materials.promotional_material:
        promo = ctx.doc(ctx.materials.promotional_material)
        for p in binding.panels + binding.context:
            t = _panel_text(promo, p)
            panels_payload.append({"panel_id": p["panel_id"], "label": p["label"], "text": t})
            panel_texts += "\n" + t

    interp = _interpret(claim, ctx, panels_payload)
    unresolved, gates = _check_interpretation(claim, interp, panel_texts)
    if binding.unresolved:
        gates.append("panel_unresolved")
        unresolved += [f"panel: {u}" for u in binding.unresolved]
    if interp.has_propositional_or:
        gates.append("unsupported_logic")

    # ---- evidence per resolved source document
    records: dict[str, EvidenceRecord] = {}
    facts: dict[str, GroundedFact] = {}
    fact_meta: dict[str, dict] = {}
    links = []
    dropped: list[str] = []
    for doc_id in res.doc_ids:
        meta = ctx.materials.doc(doc_id)
        doc = ctx.doc(doc_id)
        mine = _assertions_for_doc(interp, doc_id, binding.panels, res.doc_ids)
        if meta.get("doc_type") == "promotional":
            label = meta.get("label") or {}
            if not label.get("pages"):
                continue  # promotional material is never evidence for its own efficacy claims
            pages, role = list(label["pages"]), "label (reproduced prescribing information)"
        else:
            queries = [" ".join([a.statement] + a.search_terms) for a in mine]
            anchors = sorted({n.raw for a in mine for n in a.numbers if n.role in ANCHOR_ROLES})
            cands = candidate_pages_for_assertions(doc, queries, [d for d in map(_digits, anchors) if d],
                                                   page_hint=res.page_hints.get(doc_id))
            pages, role = cands.pages, "study evidence"
        extraction = _extract(claim, ctx, doc, mine, pages, role)
        if not extraction.evidence and meta.get("doc_type") != "promotional":
            more = candidate_pages(doc, " ".join(queries), None, k=4, exclude=pages)
            if more.pages:
                extraction = _extract(claim, ctx, doc, mine, more.pages, role)
        prefix = doc_id.split("_")[0][:10]
        for item in extraction.evidence:
            locs = _locate_evidence(doc, item.quote, item.page)
            if locs is None:
                dropped.append(f"{prefix}:{item.id} (quote not found in source)")
                continue
            by_rid = {(f"{prefix}:{item.id}" if len(locs) == 1 else f"{prefix}:{item.id}.L{j}"): loc
                      for j, loc in enumerate(locs, 1)}
            for rid, loc in by_rid.items():
                records[rid] = EvidenceRecord(doc_id=doc_id, sha256=doc.sha256, page=loc.page,
                                              source_kind=loc.source_kind, literal_quote=loc.text, bbox=loc.bbox,
                                              region_id=loc.region_id, supports=item.assertion_ids)
            for f in item.facts:
                rid, value, status = _ground_in_records(f, by_rid, doc)
                key = f"{prefix}:{f.id}"
                facts[key] = GroundedFact(key, value, status, rid)
                fact_meta[key] = {"doc": doc, "loc": by_rid[rid], "arm": f.arm, "statistic": f.statistic, "raw": f.raw}
        for ln in extraction.links:
            links.append(ln.model_copy(update={"fact_ids": [f"{prefix}:{x}" for x in ln.fact_ids]}))

    # ---- visual reading of decisive values (three paths)
    decisive = {fid for ln in links if ln.operation != "none" for fid in ln.fact_ids if fid in facts}
    by_page: dict[tuple, list[str]] = {}
    for fid in decisive:
        m = fact_meta[fid]
        page = m["doc"].pages[m["loc"].page - 1]
        figure_like = bool(CAPTION.search(page.raw_text)) or m["loc"].source_kind == "ocr"
        if facts[fid].status in ("sign_unknown", "ocr") or figure_like:
            by_page.setdefault((m["doc"].doc_id, m["loc"].page), []).append(fid)
    visual_notes = []
    for (doc_id, page_no), fids in by_page.items():
        doc = ctx.doc(doc_id)
        bboxes = [fact_meta[f]["loc"].bbox for f in fids if fact_meta[f]["loc"].bbox]
        bbox = (min(b[0] for b in bboxes), min(b[1] for b in bboxes), max(b[2] for b in bboxes),
                max(b[3] for b in bboxes)) if bboxes else None
        reqs = [{"fact_id": f, "statistic": fact_meta[f]["statistic"], "arm": fact_meta[f]["arm"],
                 "value_in_text": facts[f].value} for f in fids]
        reading = _visual(claim, ctx, doc, page_no, bbox, reqs)
        seen = {v.fact_id: v for v in reading.values}
        for f in fids:
            v, gf = seen.get(f), facts[f]
            path = "text" if gf.status == "ok" else "ocr_or_unreliable"
            if v is None or not v.readable:
                if path != "text":
                    gf.status = "visual_unreadable"
                visual_notes.append(f"{f}: visual {'uncorroborated' if path == 'text' else 'unreadable'}")
                continue
            if not _same_digits(v.value_as_printed, gf.value) or not _arm_agrees(fact_meta[f]["arm"], v.arm_label_as_printed):
                gf.status = "visual_disagrees"
                visual_notes.append(f"{f}: visual reading {v.value_as_printed!r} / {v.arm_label_as_printed!r} "
                                    f"disagrees with source {gf.value!r} / {fact_meta[f]['arm']!r}")
                continue
            if path != "text":  # the source image is the evidence when the text layer is missing or defective
                gf.value = _visual_value(v.value_as_printed, gf.value)
                gf.status = "ok"

    # ---- compute
    results: dict[str, list[LinkResult]] = {}
    computed_by_assertion: dict[str, dict] = {}
    computed_prio: dict[str, int] = {}
    assertion_by_id = {a.id: a for a in interp.assertions}
    for ln in links:
        a = assertion_by_id.get(ln.assertion_id)
        if a is None or ln.operation == "none":
            continue
        roles = {n.role for n in a.numbers if n.raw == ln.claimed_raw}
        if roles and roles <= {"other"}:
            continue  # numbers inside names ("type 2", "BIAsp 30") are never compared
        r = evaluate_link(a, ln, facts, fold=fold_settings(ctx.cfg))
        results.setdefault(a.id, []).append(r)
        prio = min((ROLE_PRIORITY[rl] for rl in roles if rl in ROLE_PRIORITY), default=None)  # never dose/time
        if r.computed and a.check_category == "figure" and prio is not None and prio < computed_prio.get(a.id, 99):
            computed_by_assertion[a.id], computed_prio[a.id] = r.computed, prio

    # ---- judge
    system, _ = prompt("judge")
    judge_payload = {
        "claim": {"text": claim.text, "population": claim.population, "molecule": claim.molecule},
        "scope": ctx.scope, "assertions": _assertions_payload(interp.assertions),
        "evidence": [{"id": rid, "doc_id": r.doc_id, "doc_type": ctx.materials.doc(r.doc_id).get("doc_type"),
                      "page": r.page, "source_kind": r.source_kind, "quote": r.literal_quote,
                      "assertion_ids": r.supports} for rid, r in records.items()],
        "facts": [{"id": k, "value": f.value, "status": f.status, "evidence_id": f.evidence_id,
                   "arm": fact_meta[k]["arm"], "statistic": fact_meta[k]["statistic"]} for k, f in facts.items()],
        "numeric_results": [{"assertion_id": aid, "outcome": r.outcome.value, "reason": r.reason, "flag": r.flag,
                             "computed": r.computed} for aid, rs in results.items() for r in rs],
        "panels": panels_payload, "unresolved": unresolved + dropped, "visual_notes": visual_notes,
    }
    judgment: Judgment = ctx.llm.generate_json(
        stage="judge", model=ctx.cfg["models"]["judge"], system=system,
        parts=[json.dumps(judge_payload, ensure_ascii=False, indent=1, default=str)], schema=Judgment,
        thinking=ctx.cfg["models"]["judge_thinking"], claim_id=claim.claim_id)
    return _decide(claim, ctx, interp, judgment, results, computed_by_assertion, records, gates,
                   unresolved + dropped + visual_notes, binding, panel_texts=panel_texts)


def _final_relation(a, judged, numeric: Relation | None, link_count: int) -> Relation:
    rel = Relation(judged.relation) if judged else Relation.UNKNOWN
    # code, not the judge, settles claimed numbers: it alone can contradict them, and when it found them
    # unclear or never compared them, the judge can neither confirm nor refute them
    if numeric is Relation.CONTRADICTED:
        return Relation.CONTRADICTED
    if numeric is Relation.UNKNOWN:
        return Relation.UNKNOWN
    quantitative = [n for n in a.numbers if n.role in QUANT_ROLES]
    if quantitative and link_count == 0:
        return Relation.UNKNOWN
    return rel


def _decide(claim, ctx, interp, judgment, results, computed_by_assertion, records, gates, notes, binding,
            panel_texts: str = "") -> Prediction:
    judged = {j.assertion_id: j for j in judgment.assertions}
    leaves, final = [], {}
    for a in interp.assertions:
        rel = _final_relation(a, judged.get(a.id), assertion_numeric_relation(results.get(a.id, [])),
                              len(results.get(a.id, [])))
        flags = set(judged[a.id].policy_flags) if a.id in judged else set()
        final[a.id] = (rel, flags)
        leaf = Leaf(a.id, rel, flags)
        leaves.append((a, Not(leaf) if a.negated else leaf))
    if not records:
        gates = gates + ["evidence_missing"]

    def verdict_for(selected):
        if not selected:
            return None
        return decide(And([node for _, node in selected]), gate_failures=list(dict.fromkeys(gates)),
                      interpretation_ok=interp.interpretation_ok)

    d = verdict_for(leaves)
    text_d = verdict_for([(a, n) for a, n in leaves if a.origin != "panel"])

    checks = {}
    for cat in CHECK_NAMES:
        rels = [final[a.id] for a in interp.assertions if a.check_category == cat]
        if not rels:
            checks[cat] = None
        elif any(r is Relation.CONTRADICTED or (f & MATERIAL_FLAGS) for r, f in rels):
            checks[cat] = "mismatch"
        elif any(r is Relation.UNKNOWN for r, _ in rels):
            checks[cat] = "unclear"
        else:
            checks[cat] = "match"

    contradicted = [a.id for a in interp.assertions if final[a.id][0] is Relation.CONTRADICTED]
    deciding = contradicted + [x for x in d.flagged if x not in contradicted]
    if not deciding:
        deciding = [a.id for a in interp.assertions if a.check_category == "figure"] or [a.id for a in interp.assertions]
    chosen = None
    for aid in deciding:
        ids = judged[aid].evidence_ids if aid in judged else []
        cands = [r for rid, r in records.items() if rid.split(":")[-1] in ids or rid in ids] + \
                [r for r in records.values() if aid in r.supports]
        if cands:
            chosen = cands[0]
            break
    computed = next((computed_by_assertion[a] for a in deciding if a in computed_by_assertion),
                    next(iter(computed_by_assertion.values()), None))
    reason = judgment.summary
    ungrounded = _ungrounded_numbers(judgment.summary, claim, records, computed_by_assertion, panel_texts=panel_texts)
    if ungrounded:  # the judge may not introduce or derive numbers; fall back to a code-written reason
        notes = notes + [f"judge_numbers_not_grounded: {', '.join(ungrounded)}"]
        reason = _code_reason(d, interp, final, computed)
    if d.verdict == "cannot_verify" and d.abstention_type:
        reason += f" [abstained: {d.abstention_type}]"
    panel_findings = [{"assertion_id": a.id, "panel_id": a.origin_ref, "statement": a.statement,
                       "relation": final[a.id][0].value, "policy_flags": sorted(final[a.id][1])}
                      for a in interp.assertions if a.origin == "panel"]
    return Prediction(
        claim_id=claim.claim_id, verdict=d.verdict, checks=Checks(**checks),
        evidence_page=chosen.page if chosen else None, evidence_span=chosen.literal_quote if chosen else None,
        computed=computed, reason=reason, scope=ctx.scope, text_verdict=text_d.verdict if text_d else None,
        evidence_source=f"{chosen.doc_id} ({chosen.source_kind})" if chosen else None,
        evidence_records=list(records.values()), panel_findings=panel_findings,
        abstention_type=d.abstention_type if d.verdict == "cannot_verify" else None,
        disclosures=d.disclosures, unresolved=list(dict.fromkeys(d.unresolved + notes)))


def _ungrounded_numbers(text: str, claim, records, computed_by_assertion, panel_texts: str = "") -> list[str]:
    """Numbers in judge-written text that appear nowhere in the claim (incl. its bound panels), the evidence
    or the code results."""
    pool = " ".join([claim.text or "", claim.population or "", claim.molecule or "", panel_texts]
                    + [r.literal_quote for r in records.values()]
                    + [json.dumps(c, ensure_ascii=False) for c in computed_by_assertion.values()])
    allowed = {t.raw.lstrip("+-\u2212\u2013").replace(",", "") for t in number_tokens(normalize(pool))}
    seen = [t.raw.lstrip("+-\u2212\u2013").replace(",", "") for t in number_tokens(normalize(text or ""))]
    return sorted({x for x in seen if x not in allowed})


def _code_reason(d, interp, final, computed) -> str:
    by_id = {a.id: a for a in interp.assertions}
    parts = [f"{aid}: {by_id[aid].statement} -> {final[aid][0].value}" for aid in by_id
             if final[aid][0] is not Relation.ENTAILED or final[aid][1] & MATERIAL_FLAGS]
    if computed:
        parts.append(f"computed {computed.get('result')} from {computed.get('from_source')} vs claimed {computed.get('claimed')}")
    return f"{d.verdict}: " + ("; ".join(parts) if parts else "all assertions entailed by the cited evidence")
