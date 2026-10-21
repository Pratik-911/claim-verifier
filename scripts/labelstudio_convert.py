"""Convert a Label Studio page-image task export into annotation-v1 evidence entries.

Rectangles are stored by Label Studio in percent of the image; they are mapped back to PDF points.
Each per-region quote is located verbatim in the source (text layer or OCR); unlocated quotes are
kept with located=False so the annotator can fix them.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from verifier.sources import ingest, locate_quote

NAME = re.compile(r"(?P<doc>.+)__p(?P<page>\d+)\.png$")


def convert_task(task: dict, page_meta: dict, root: Path) -> list[dict]:
    image_ref = str(task["data"].get("image", ""))
    key = next((k for k in page_meta if k in image_ref), None) or Path(image_ref).name
    doc_id, page_no, width, height = page_meta.get(key) or (NAME.search(key).group("doc"),
                                                            int(NAME.search(key).group("page")), None, None)
    doc = ingest(root / "papers" / f"{doc_id}.pdf", cache_dir=root / "corpus", doc_id=doc_id)
    page = doc.pages[page_no - 1]
    width, height = width or page.width, height or page.height
    regions: dict[str, dict] = {}
    for ann in task.get("annotations", []):
        for item in ann.get("result", []):
            r = regions.setdefault(item["id"], {})
            v = item["value"]
            if item["type"] == "rectanglelabels":
                r["bbox"] = (v["x"] / 100 * width, v["y"] / 100 * height,
                             (v["x"] + v["width"]) / 100 * width, (v["y"] + v["height"]) / 100 * height)
                r["label"] = v["rectanglelabels"][0]
            elif item["type"] == "textarea":
                r["quote"] = (v.get("text") or [""])[0]
            elif item["type"] == "choices":
                r["relation"] = (v.get("choices") or ["unknown"])[0]
    out = []
    for i, (rid, r) in enumerate(regions.items(), 1):
        quote = r.get("quote", "")
        loc = locate_quote(doc, quote, page_hint=page_no) if quote else None
        out.append({"evidence_id": f"E{i}", "doc_id": doc_id, "sha256": doc.sha256, "page": page_no,
                    "source_kind": loc.source_kind if loc else "text_layer",
                    "literal_quote": loc.text if loc else quote, "bbox": r.get("bbox"),
                    "region_id": loc.region_id if loc else None,
                    "located": bool(loc and loc.page == page_no), "relation": r.get("relation", "unknown")})
    return out


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
