"""Verify promotional claims against their cited sources.

Usage: python verify.py data/claims.json [--config verify.toml]

Writes outputs/predictions.json, appends every LLM call to outputs/llm_log.jsonl and records the
effective configuration in outputs/run_meta.json. Exit codes: 0 ok, 1 bad input, 2 no usable
credentials (every claim is then cannot_verify), 3 internal error (details in outputs/errors.log).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import sys
import traceback
import uuid
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from verifier.config import DEFAULT_CONFIG, REPO_ROOT, ConfigError, load_config, resolve
from verifier.credentials import credential_status
from verifier.io import write_json_atomic
from verifier.schemas import load_claims, operational_abstention


def _write_outputs(cfg: dict, config_path: Path, predictions: list, backend: str | None, run_id: str,
                   started: str) -> None:
    write_json_atomic(resolve(cfg["run"]["predictions"]), [p.model_dump() for p in predictions])
    write_json_atomic(resolve(cfg["run"]["run_meta"]), {
        "run_id": run_id, "started": started, "finished": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config_path": str(config_path), "config": cfg, "backend": backend,
        "python": platform.python_version(), "n_claims": len(predictions),
        "verdicts": {v: sum(p.verdict == v for p in predictions)
                     for v in ("supported", "overstated", "cannot_verify")},
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("claims", help="path to a claims JSON file")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="runtime configuration (TOML)")
    args = parser.parse_args(argv)

    run_id, started = uuid.uuid4().hex[:12], dt.datetime.now(dt.timezone.utc).isoformat()
    config_path = Path(args.config)
    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    load_dotenv(REPO_ROOT / ".env", override=False)
    try:
        claims = load_claims(args.claims)
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        print(f"error: could not read claims file {args.claims}: {str(e).splitlines()[0]}", file=sys.stderr)
        return 1

    scope = cfg["run"]["scope"]
    backend, detail = credential_status(check_refresh=True)
    if backend is None:
        print(f"error: {detail}\nEvery claim is reported as cannot_verify (operational_status=auth).",
              file=sys.stderr)
        preds = [operational_abstention(c, "auth", detail, scope) for c in claims]
        _write_outputs(cfg, config_path, preds, None, run_id, started)
        return 2

    from verifier.pipeline import run_claims  # imported only when an LLM backend is usable
    preds = run_claims(claims, cfg, backend=backend, run_id=run_id)
    _write_outputs(cfg, config_path, preds, backend, run_id, started)
    failed = sum(p.operational_status != "ok" for p in preds)
    print(f"wrote {cfg['run']['predictions']} ({len(preds)} claims, {failed} operational failures)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:  # never show a raw traceback; keep it for debugging
        log = REPO_ROOT / "outputs" / "errors.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as f:
            f.write(f"--- {dt.datetime.now(dt.timezone.utc).isoformat()}\n{traceback.format_exc()}\n")
        print(f"internal error: {type(exc).__name__}: {exc} (details in {log})", file=sys.stderr)
        sys.exit(3)
