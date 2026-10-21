"""Portable OCR (RapidOCR / ONNX) on rendered PDF crops, with crop-linked provenance."""
from __future__ import annotations

import functools
import hashlib
import importlib.metadata
import logging
from dataclasses import dataclass

import numpy as np
import pymupdf

ENGINE = "rapidocr"
# RapidOCR downscales any input whose longer side exceeds Global.max_side_len (default 2000 px). A 300 DPI
# A4 page is ~2900x4200 px, so small print (e.g. a 6 pt prescribing-information block) was being read at
# ~144 DPI. Allow native resolution up to this size instead.
MAX_SIDE_LEN = 6000


def engine_version() -> str:
    return importlib.metadata.version("rapidocr")


@functools.lru_cache(maxsize=1)
def _engine():
    from rapidocr import RapidOCR

    engine = RapidOCR(params={"Global.max_side_len": MAX_SIDE_LEN})
    for name in ("RapidOCR", "rapidocr"):  # after construction: RapidOCR resets its logger level to INFO
        logging.getLogger(name).setLevel(logging.ERROR)
    return engine


@dataclass
class OcrLine:
    text: str
    conf: float
    bbox: tuple[float, float, float, float]  # PDF points


def ocr_clip(page: pymupdf.Page, clip: pymupdf.Rect, dpi: int) -> tuple[list[OcrLine], str]:
    """OCR one rendered region. Returns lines in reading order and the sha256 of the rendered PNG."""
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    image_sha256 = hashlib.sha256(pix.tobytes("png")).hexdigest()
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    result = _engine()(np.ascontiguousarray(img))
    scale = 72.0 / dpi
    lines: list[OcrLine] = []
    if result.txts:
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            bbox = (clip.x0 + min(xs) * scale, clip.y0 + min(ys) * scale,
                    clip.x0 + max(xs) * scale, clip.y0 + max(ys) * scale)
            lines.append(OcrLine(str(text), float(score), bbox))
    lines.sort(key=lambda ln: (round((ln.bbox[1] + ln.bbox[3]) / 2 / 4), ln.bbox[0]))
    return lines, image_sha256
