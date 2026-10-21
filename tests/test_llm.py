"""LLM wrapper behaviour with a fake Gemini client (the real API is external; everything else is real)."""
import json
from types import SimpleNamespace

import pytest
from google.genai import errors
from pydantic import BaseModel

from verifier.llm import LLM, ImagePart, LLMError


class Answer(BaseModel):
    value: str


def response(text, model="gemini-test-001"):
    usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=3)
    return SimpleNamespace(text=text, model_version=model, usage_metadata=usage)


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        nxt = self.outcomes.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def make(tmp_path, outcomes):
    client = FakeClient(outcomes)
    llm = LLM(client=client, log_path=tmp_path / "llm_log.jsonl", run_id="run1", max_retries=3, backoff_s=0)
    return llm, client


def log_lines(tmp_path):
    return [json.loads(x) for x in (tmp_path / "llm_log.jsonl").read_text().splitlines()]


def call(llm, **kw):
    return llm.generate_json(stage="judge", model="gemini-4-argon", system="SYSTEM RULES",
                             parts=["user prompt text"], schema=Answer, thinking="high", claim_id="C1", **kw)


def test_logs_full_prompt_parameters_and_raw_response(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-key")
    llm, _ = make(tmp_path, [response('{"value": "ok"}')])
    assert call(llm).value == "ok"
    (entry,) = log_lines(tmp_path)
    assert entry["system"] == "SYSTEM RULES" and entry["parts"] == ["user prompt text"]
    assert entry["requested_model"] == "gemini-4-argon" and entry["resolved_model"] == "gemini-test-001"
    assert entry["params"]["thinking_level"] == "high"
    assert entry["params"]["response_schema"]["title"] == "Answer"
    assert entry["raw_response"] == '{"value": "ok"}'
    assert entry["status"] == "ok" and entry["claim_id"] == "C1" and entry["stage"] == "judge"
    assert "super-secret-key" not in (tmp_path / "llm_log.jsonl").read_text()


def test_images_are_logged_by_path_and_hash_not_bytes(tmp_path):
    llm, client = make(tmp_path, [response('{"value": "ok"}')])
    img = ImagePart(data=b"\x89PNG fake bytes", mime="image/png", path="corpus/x/crop.png")
    llm.generate_json(stage="visual", model="m", system="s", parts=["read this", img], schema=Answer, thinking="low")
    (entry,) = log_lines(tmp_path)
    assert entry["parts"][1] == {"image": "corpus/x/crop.png", "sha256": img.sha256, "mime": "image/png"}


def test_invalid_json_gets_one_repair_retry(tmp_path):
    llm, client = make(tmp_path, [response("not json"), response('{"value": "fixed"}')])
    assert call(llm).value == "fixed"
    entries = log_lines(tmp_path)
    assert [e["status"] for e in entries] == ["invalid_output", "ok"]
    assert "validation" in entries[1]["parts"][-1].lower()


def test_invalid_twice_raises_invalid_output(tmp_path):
    llm, _ = make(tmp_path, [response("nope"), response('{"wrong": 1}')])
    with pytest.raises(LLMError) as e:
        call(llm)
    assert e.value.kind == "invalid_output"


def test_rate_limit_is_retried_with_attempts_logged(tmp_path):
    quota = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    llm, _ = make(tmp_path, [quota, response('{"value": "ok"}')])
    assert call(llm).value == "ok"
    assert [(e["attempt"], e["status"]) for e in log_lines(tmp_path)] == [(1, "quota"), (2, "ok")]


def test_auth_error_is_not_retried(tmp_path):
    denied = errors.ClientError(403, {"error": {"code": 403, "message": "denied", "status": "PERMISSION_DENIED"}})
    llm, client = make(tmp_path, [denied, response('{"value": "never"}')])
    with pytest.raises(LLMError) as e:
        call(llm)
    assert e.value.kind == "auth" and len(client.calls) == 1


def test_retries_exhausted_raise_with_kind(tmp_path):
    busy = errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})
    llm, client = make(tmp_path, [busy, busy, busy])
    with pytest.raises(LLMError) as e:
        call(llm)
    assert e.value.kind == "unavailable" and len(client.calls) == 3


def test_client_has_a_request_timeout(monkeypatch):
    # root cause of a 3.5 h hang: google-genai defaults to no timeout at all
    from verifier.llm import make_client
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-construction-only")
    client = make_client("api_key", timeout_s=120)
    assert client._api_client._http_options.timeout == 120_000


def test_transport_timeout_is_retried_and_logged_with_request(tmp_path):
    import httpx
    llm, client = make(tmp_path, [httpx.ReadTimeout("stalled"), response('{"value": "ok"}')])
    assert call(llm).value == "ok"
    entries = log_lines(tmp_path)
    assert [(e["attempt"], e["status"]) for e in entries] == [(1, "timeout"), (2, "ok")]
    assert entries[0]["parts"] == ["user prompt text"]  # the failed request is in the log


def test_claim_deadline_stops_further_calls(tmp_path):
    import time
    llm, client = make(tmp_path, [response('{"value": "ok"}')])
    llm.deadline = time.monotonic() - 1  # already past
    with pytest.raises(LLMError) as e:
        call(llm)
    assert e.value.kind == "timeout" and client.calls == []


def test_expired_google_credentials_are_an_auth_failure_not_internal(tmp_path):
    from google.auth.exceptions import RefreshError
    llm, client = make(tmp_path, [RefreshError("Reauthentication is needed."), response('{"value": "x"}')])
    with pytest.raises(LLMError) as e:
        call(llm)
    assert e.value.kind == "auth" and len(client.calls) == 1


def test_degenerate_runaway_string_is_invalid_output_and_repaired(tmp_path):
    loop = "level marker variable test " * 400  # a repetition loop that still closes as valid JSON
    llm, _ = make(tmp_path, [response(json.dumps({"value": loop})), response('{"value": "ok"}')])
    assert call(llm).value == "ok"
    first = log_lines(tmp_path)[0]
    assert first["status"] == "invalid_output" and "runaway" in first["error"]
