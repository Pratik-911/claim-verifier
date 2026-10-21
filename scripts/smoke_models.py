"""Smoke-test the pinned models for each available auth mode (plan §12.1 step 2).

For every auth mode that is configured (Vertex ADC, Gemini API key), call the pinned extraction/vision
model and the judge with a representative image (an LBL p3 crop) and the REAL pipeline schemas, and
save results to outputs/smoke/. All calls are logged like pipeline calls (outputs/smoke/llm_log.jsonl).

Usage: python scripts/smoke_models.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from verifier.config import load_config  # noqa: E402
from verifier.credentials import credential_status  # noqa: E402
from verifier.llm import LLM, ImagePart, LLMError, make_client  # noqa: E402
from verifier.llm_schemas import Judgment, VisualReading  # noqa: E402


def modes() -> list[tuple[str, dict]]:
    base = dict(os.environ)
    out = []
    vertex = {k: v for k, v in base.items() if k not in ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
    if credential_status(vertex)[0] == "vertex":
        out.append(("vertex", vertex))
    key = {k: v for k, v in base.items() if k != "GOOGLE_GENAI_USE_VERTEXAI"}
    if credential_status(key)[0] == "api_key":
        out.append(("api_key", key))
    return out


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    cfg = load_config()
    out_dir = ROOT / "outputs" / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(ROOT / "papers" / "SOLIQUA_LBL.pdf") as pdf:
        png = pdf[2].get_pixmap(dpi=200, clip=pymupdf.Rect(290, 512, 475, 800)).tobytes("png")
    crop = out_dir / "lbl_p3_solimix_panel.png"
    crop.write_bytes(png)
    image = ImagePart(png, "image/png", str(crop.relative_to(ROOT)))
    found = modes()
    if not found:
        print("no auth mode configured:", credential_status()[1])
        return 2
    results = []
    for mode, env in found:
        saved = dict(os.environ)
        os.environ.clear()
        os.environ.update(env)
        try:
            llm = LLM(make_client(mode, cfg["auth"]["location"]), out_dir / "llm_log.jsonl", f"smoke-{mode}",
                      max_retries=2)
            for stage, model, schema, parts, thinking in [
                ("visual", cfg["models"]["extract"], VisualReading,
                 [json.dumps({"facts": [{"fact_id": "F1", "statistic": "LS mean change in body weight",
                                         "arm": "SOLIQUA"}, {"fact_id": "F2", "statistic": "p-value",
                                                             "arm": "SOLIQUA vs BIAsp 30"}]}), image], "low"),
                ("judge", cfg["models"]["judge"], Judgment,
                 [json.dumps({"assertions": [{"id": "A1", "statement": "weight decreased"}],
                              "evidence": [{"id": "E1", "quote": "-0.7 kg"}]})], cfg["models"]["judge_thinking"]),
            ]:
                row = {"mode": mode, "stage": stage, "model": model}
                try:
                    res = llm.generate_json(stage=f"smoke-{stage}", model=model, system="Smoke test. Return JSON.",
                                            parts=parts, schema=schema, thinking=thinking)
                    row.update(ok=True, output=res.model_dump())
                except LLMError as e:
                    row.update(ok=False, error=e.kind, detail=str(e)[:300])
                results.append(row)
                print(f"{mode:8s} {stage:7s} {model:18s} {'OK' if row['ok'] else 'FAIL ' + row['error']}")
        finally:
            os.environ.clear()
            os.environ.update(saved)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"smoke-{stamp}.json").write_text(json.dumps(results, indent=2, default=str))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
