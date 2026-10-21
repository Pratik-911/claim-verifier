"""The exact assignment command, run from a fresh copy (no .env, no cache), without credentials."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
REQUIRED = {"claim_id", "verdict", "checks", "evidence_page", "evidence_span", "computed", "reason"}
CHECKS = {"figure", "population", "drug_indication", "actually_stated"}
SKIP = {".venv", ".env", "corpus", "outputs", ".git", ".ledger", ".pytest_cache", "__pycache__"}


def fresh_copy(dst: Path) -> Path:
    dst.mkdir(parents=True)
    for item in REPO.iterdir():
        if item.name in SKIP:
            continue
        if item.is_dir():
            shutil.copytree(item, dst / item.name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, dst / item.name)
    return dst


def no_credential_env(tmp: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GOOGLE_", "GEMINI_", "CLOUDSDK_"))}
    empty = tmp / "gcloud-empty"
    empty.mkdir(exist_ok=True)
    env.update(CLOUDSDK_CONFIG=str(empty), NO_GCE_CHECK="True")
    return env


@pytest.fixture(scope="module")
def no_key_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fresh")
    repo = fresh_copy(tmp / "repo")
    proc = subprocess.run([sys.executable, "verify.py", "data/claims.json"], cwd=repo,
                          env=no_credential_env(tmp), capture_output=True, text=True, timeout=120)
    return repo, proc


def test_exact_command_without_credentials_exits_2_cleanly(no_key_run):
    _, proc = no_key_run
    assert proc.returncode == 2
    assert "credentials" in proc.stderr.lower()
    assert "Traceback" not in proc.stderr


def test_no_credentials_returns_cannot_verify_for_every_claim(no_key_run):
    repo, _ = no_key_run
    preds = json.loads((repo / "outputs/predictions.json").read_text())
    claims = json.loads((repo / "data/claims.json").read_text())
    assert [p["claim_id"] for p in preds] == [c["claim_id"] for c in claims]
    assert {p["verdict"] for p in preds} == {"cannot_verify"}
    assert {p["operational_status"] for p in preds} == {"auth"}


def test_prediction_contract_fields(no_key_run):
    repo, _ = no_key_run
    for p in json.loads((repo / "outputs/predictions.json").read_text()):
        assert REQUIRED <= set(p)
        assert set(p["checks"]) == CHECKS


def test_bare_command_uses_submission_config(no_key_run):
    repo, _ = no_key_run
    meta = json.loads((repo / "outputs/run_meta.json").read_text())
    assert meta["config"]["run"]["scope"] == "claim_and_referenced_panels"
    assert meta["config"]["models"]["judge"] == "gemini-4-argon"


def test_malformed_claims_file_is_a_clean_error(tmp_path):
    repo = fresh_copy(tmp_path / "repo")
    (repo / "bad.json").write_text("{not json")
    proc = subprocess.run([sys.executable, "verify.py", "bad.json"], cwd=repo,
                          env=no_credential_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "bad.json" in proc.stderr


def test_eval_prints_assignment_trio(tmp_path):
    repo = fresh_copy(tmp_path / "repo")
    pred = [{"claim_id": "X1", "verdict": "supported"}, {"claim_id": "X2", "verdict": "cannot_verify"},
            {"claim_id": "X3", "verdict": "supported"}]
    gold = [{"claim_id": "X1", "verdict": "supported"}, {"claim_id": "X2", "verdict": "overstated"},
            {"claim_id": "X3", "verdict": "overstated"}]
    (repo / "p.json").write_text(json.dumps(pred))
    (repo / "g.json").write_text(json.dumps(gold))
    proc = subprocess.run([sys.executable, "eval.py", "--pred", "p.json", "--gold", "g.json", "--no-span-check"],
                          cwd=repo, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "Accuracy: 1/3" in out
    assert "Confusion matrix" in out
    assert "False supported: 1" in out
