"""Runtime ingestion of immutable source PDFs into a cached, provenance-preserving page model.

A region is usable only if text-layer characters cover it AND its glyphs pass the glyph check.
Unusable regions are OCR'd from a render and every OCR record is tied to its crop.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from verifier import ocr
from verifier.textnorm import find_quote

INGEST_VERSION = "ingest-v2"  # v2: OCR at native resolution (max_side_len)
OCR_DPI = 300
SYMBOL_FONT = re.compile(r"symb|ssyb|psmp|mathpi|symbol", re.I)
MIN_IMAGE_AREA_FRAC = 0.01  # images smaller than this (logos, icons) are ignored
MAX_CHARS_INSIDE_RASTER = 10  # a raster region with fewer text-layer chars than this is unusable

DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;)]")
TRIAL_IDS = re.compile(r"\bNCT\d{8}\b|\b\d{4}-\d{6}-\d{2}\b|\bISRCTN\d{8}\b")

BBox = tuple[float, float, float, float]


@dataclass
class OcrRegion:
    id: str
    page: int
    bbox: BBox
    reason: str  # raster_no_text | glyph_check | no_text_layer
    engine: str
    engine_version: str
    dpi: int
    image_sha256: str
    lines: list[ocr.OcrLine]

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)


@dataclass
class Page:
    number: int  # 1-indexed
    width: float
    height: float
    raw_text: str
    char_boxes: list[BBox | None]  # aligned with raw_text; None for synthetic newlines
    flagged: list[int]  # raw_text indices whose glyphs failed the glyph check
    ocr_regions: list[OcrRegion] = field(default_factory=list)

    def ocr_text(self) -> str:
        return "\n".join(r.text for r in self.ocr_regions)


@dataclass
class Document:
    doc_id: str
    path: str
    sha256: str
    pages: list[Page]
    meta: dict
    from_cache: bool = False


@dataclass
class Location:
    page: int
    source_kind: str  # text_layer | ocr
    start: int
    end: int
    region_id: str | None
    bbox: BBox | None
    text: str  # the literal slice of the source text (or OCR text)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _bad_glyph(ch: str, font: str) -> bool:
    if ch in "\n\t ":
        return False
    if unicodedata.category(ch) in ("Cc", "Co", "Cs") or ch == "�":
        return True
    return bool(SYMBOL_FONT.search(font))


def _union(boxes) -> BBox | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _text_layer(page: pymupdf.Page) -> tuple[str, list[BBox | None], list[int], list[tuple[BBox, list[int]]]]:
    chars: list[str] = []
    boxes: list[BBox | None] = []
    flagged: list[int] = []
    blocks: list[tuple[BBox, list[int]]] = []
    for block in page.get_text("rawdict")["blocks"]:
        if block.get("type") != 0:
            continue
        block_flags: list[int] = []
        for line in block["lines"]:
            for span in line["spans"]:
                for ch in span["chars"]:
                    if _bad_glyph(ch["c"], span["font"]):
                        flagged.append(len(chars))
                        block_flags.append(len(chars))
                    chars.append(ch["c"])
                    boxes.append(tuple(round(v, 2) for v in ch["bbox"]))
            chars.append("\n")
            boxes.append(None)
        if block_flags:
            blocks.append((tuple(block["bbox"]), block_flags))
    return "".join(chars), boxes, flagged, blocks


def _build_page(doc: pymupdf.Document, index: int, dpi: int) -> Page:
    page = doc[index]
    raw_text, boxes, flagged, flagged_blocks = _text_layer(page)
    page_rect = page.rect
    regions: list[OcrRegion] = []

    def add_region(clip: pymupdf.Rect, reason: str) -> None:
        clip = clip & page_rect
        if clip.is_empty:
            return
        lines, img_sha = ocr.ocr_clip(page, clip, dpi)
        regions.append(OcrRegion(f"p{index + 1}r{len(regions) + 1}", index + 1, tuple(clip), reason,
                                 ocr.ENGINE, ocr.engine_version(), dpi, img_sha, lines))

    for info in page.get_image_info():
        rect = pymupdf.Rect(info["bbox"]) & page_rect
        if rect.is_empty or rect.get_area() < MIN_IMAGE_AREA_FRAC * page_rect.get_area():
            continue
        inside = sum(1 for b in boxes if b and rect.contains(pymupdf.Point((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)))
        if inside < MAX_CHARS_INSIDE_RASTER:
            add_region(rect, "raster_no_text")
    if not raw_text.strip() and not regions:
        add_region(page_rect, "no_text_layer")
    for bbox, _ in flagged_blocks:
        add_region(pymupdf.Rect(bbox) + (-2, -2, 2, 2), "glyph_check")
    return Page(index + 1, page_rect.width, page_rect.height, raw_text, boxes, flagged, regions)


def _meta(pages: list[Page], title: str) -> dict:
    """Identifiers from the first pages. Computed at load time (not cached) so rule fixes apply at once.
    Line wraps inside identifiers ("s13300-\\n019", "NCT020\\n58160") are re-joined first."""
    head = "\n".join(p.raw_text + "\n" + p.ocr_text() for p in pages[:2])
    head = re.sub(r"(?<=[\w-])\n(?=[\w-])", "", head)
    doi = DOI.search(head)
    return {"title": title, "doi": doi.group(0) if doi else None,
            "trial_ids": sorted(set(TRIAL_IDS.findall(head)))}


def _to_dict(doc: Document) -> dict:
    d = dataclasses.asdict(doc)
    d.pop("from_cache")
    d.pop("meta")
    return d


def _from_dict(d: dict) -> Document:
    pages = []
    for p in d["pages"]:
        regions = [OcrRegion(**{**r, "bbox": tuple(r["bbox"]),
                                "lines": [ocr.OcrLine(ln["text"], ln["conf"], tuple(ln["bbox"])) for ln in r["lines"]]})
                   for r in p["ocr_regions"]]
        pages.append(Page(p["number"], p["width"], p["height"], p["raw_text"],
                          [tuple(b) if b else None for b in p["char_boxes"]], p["flagged"], regions))
    return Document(d["doc_id"], d["path"], d["sha256"], pages, {}, from_cache=True)


def cache_key(sha256: str, dpi: int) -> str:
    parts = {"sha256": sha256, "ingest": INGEST_VERSION, "dpi": dpi, "ocr_engine": ocr.ENGINE,
             "ocr_version": ocr.engine_version(), "ocr_max_side": ocr.MAX_SIDE_LEN, "pymupdf": pymupdf.VersionBind}
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]


def ingest(path: str | Path, cache_dir: str | Path = "corpus", doc_id: str | None = None,
           dpi: int = OCR_DPI) -> Document:
    """Ingest one PDF (never modified) into the cached page model. Runs OCR only where needed."""
    path = Path(path)
    sha = _sha256_file(path)
    cache_file = Path(cache_dir) / sha / f"document-{cache_key(sha, dpi)}.json"
    doc_id = doc_id or path.stem
    with pymupdf.open(path) as pdf:
        title = (pdf.metadata or {}).get("title") or ""
        if cache_file.exists():
            doc = _from_dict(json.loads(cache_file.read_text()))
            doc.doc_id, doc.path, doc.meta = doc_id, str(path), _meta(doc.pages, title)
            return doc
        pages = [_build_page(pdf, i, dpi) for i in range(pdf.page_count)]
    doc = Document(doc_id, str(path), sha, pages, _meta(pages, title))
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(_to_dict(doc)))
    os.replace(tmp, cache_file)  # atomic
    return doc


def _masked(page: Page) -> str:
    """raw_text with flagged glyphs replaced by a control char, so quotes can never match across them."""
    if not page.flagged:
        return page.raw_text
    chars = list(page.raw_text)
    for i in page.flagged:
        chars[i] = "\x01"
    return "".join(chars)


def _region_bbox(region: OcrRegion, start: int, end: int) -> BBox | None:
    boxes, offset = [], 0
    for ln in region.lines:
        line_end = offset + len(ln.text)
        if offset < end and start < line_end + 1:
            boxes.append(ln.bbox)
        offset = line_end + 1
    return _union(boxes)


def locate_quote(doc: Document, quote: str, page_hint: int | None = None) -> Location | None:
    """Find a literal quote: text layer first (never across flagged glyphs), then OCR regions."""
    order = sorted(doc.pages, key=lambda p: (p.number != page_hint, p.number))
    for page in order:
        span = find_quote(quote, _masked(page))
        if span:
            s, e = span
            return Location(page.number, "text_layer", s, e, None, _union(page.char_boxes[s:e]), page.raw_text[s:e])
    for page in order:
        for region in page.ocr_regions:
            span = find_quote(quote, region.text)
            if span:
                s, e = span
                return Location(page.number, "ocr", s, e, region.id, _region_bbox(region, s, e), region.text[s:e])
    return None
