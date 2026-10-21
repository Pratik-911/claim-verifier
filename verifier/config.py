"""Load the pinned runtime configuration (verify.toml)."""
from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "verify.toml"
REQUIRED_SECTIONS = ("run", "models", "versions", "ingest", "numeric")


class ConfigError(Exception):
    pass


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    path = Path(path)
    try:
        cfg = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read config {path}: {e}") from None
    missing = [s for s in REQUIRED_SECTIONS if s not in cfg]
    if missing:
        raise ConfigError(f"config {path} is missing sections: {', '.join(missing)}")
    if cfg["run"].get("scope") not in ("claim_only", "claim_and_referenced_panels"):
        raise ConfigError(f"config {path}: run.scope must be claim_only or claim_and_referenced_panels")
    return cfg


def resolve(path: str | Path, base: Path = REPO_ROOT) -> Path:
    p = Path(path)
    return p if p.is_absolute() else base / p
