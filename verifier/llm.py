"""Gemini wrapper: structured JSON output, validation in code, bounded retries, and a full call log.

Every attempt (including repair attempts and failures) is appended to llm_log.jsonl with the full
system prompt, all request parts (images by path + sha256), parameters, the raw response and the
resolved model version. Credentials are never logged.
"""
from __future__ import annotations

import contextvars
import datetime as dt
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

_LOCK = threading.Lock()
# Per-claim deadline (time.monotonic() value). A ContextVar, not an attribute, because one LLM object
# serves several claims concurrently from worker threads.
CLAIM_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("claim_deadline", default=None)
DEFAULT_REQUEST_TIMEOUT_S = 180
_THINKING = {"minimal": types.ThinkingLevel.MINIMAL, "low": types.ThinkingLevel.LOW,
             "medium": types.ThinkingLevel.MEDIUM, "high": types.ThinkingLevel.HIGH}


class LLMError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind  # auth | quota | unavailable | timeout | invalid_output | internal_error


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    mime: str
    path: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def make_client(backend: str, location: str = "global", timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S):
    """google-genai defaults to NO request timeout (a stalled response blocks forever), so always set one.
    The SDK also forwards it to the server as X-Server-Timeout."""
    from google import genai

    http = types.HttpOptions(timeout=int(timeout_s * 1000))
    if backend == "vertex":
        return genai.Client(vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"],
                            location=os.environ.get("GOOGLE_CLOUD_LOCATION", location), http_options=http)
    return genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
                        http_options=http)


def _error_kind(exc: Exception) -> str:
    if isinstance(exc, errors.APIError):
        if exc.code in (401, 403):
            return "auth"
        if exc.code == 429:
            return "quota"
        if exc.code in (408, 504):
            return "timeout"
        if exc.code >= 500:
            return "unavailable"
        return "client_error"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    from google.auth.exceptions import DefaultCredentialsError, RefreshError
    if isinstance(exc, (RefreshError, DefaultCredentialsError)):
        return "auth"  # expired or revoked Google credentials (e.g. an org re-authentication policy)
    return "internal_error"


RETRYABLE = {"quota", "unavailable", "timeout"}
MAX_STRING_CHARS = 2000  # no field in any schema needs more; a longer one is a decoding loop


def _runaway_string(text: str | None) -> str | None:
    """A repetition loop can still close as schema-valid JSON (with later fields silently missing)."""
    def walk(x):
        if isinstance(x, str):
            yield x
        elif isinstance(x, dict):
            for v in x.values():
                yield from walk(v)
        elif isinstance(x, list):
            for v in x:
                yield from walk(v)
    longest = max((len(s) for s in walk(json.loads(text or "null"))), default=0)
    if longest > MAX_STRING_CHARS:
        return f"runaway string of {longest} characters (limit {MAX_STRING_CHARS}); keep every field short"
    return None


def _schema_hash(schema: dict) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:16]


class LLM:
    def __init__(self, client, log_path: str | Path, run_id: str, max_retries: int = 3, backoff_s: float = 2.0):
        self.client = client
        self.log_path = Path(log_path)
        self.run_id = run_id
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.deadline: float | None = None  # overrides CLAIM_DEADLINE when set (single-claim use)

    def _log(self, entry: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _LOCK, open(self.log_path, "a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    @staticmethod
    def _contents(parts: list) -> list:
        out = []
        for p in parts:
            out.append(types.Part.from_bytes(data=p.data, mime_type=p.mime) if isinstance(p, ImagePart)
                       else types.Part.from_text(text=p))
        return [types.Content(role="user", parts=out)]

    @staticmethod
    def _loggable(parts: list) -> list:
        return [{"image": p.path, "sha256": p.sha256, "mime": p.mime} if isinstance(p, ImagePart) else p
                for p in parts]

    def generate_json(self, *, stage: str, model: str, system: str, parts: list, schema: type[BaseModel],
                      thinking: str, claim_id: str | None = None, doc_id: str | None = None,
                      response_schema: bool = True) -> BaseModel:
        json_schema = schema.model_json_schema()
        params = {"thinking_level": thinking, "response_mime_type": "application/json",
                  "response_schema": {"title": json_schema.get("title"), "sha": _schema_hash(json_schema),
                                      "enforced": response_schema},
                  "temperature": "model default"}
        call_id = uuid.uuid4().hex[:12]
        prompt_hash = hashlib.sha256((system + json.dumps(self._loggable(parts), default=str)).encode()).hexdigest()[:16]
        repaired = False
        attempt = 0
        while True:
            attempt += 1
            deadline = self.deadline if self.deadline is not None else CLAIM_DEADLINE.get()
            if deadline is not None and time.monotonic() > deadline:
                raise LLMError("timeout", f"claim deadline exceeded before {stage} call")
            config = types.GenerateContentConfig(
                system_instruction=system, response_mime_type="application/json",
                response_json_schema=json_schema if response_schema else None,
                thinking_config=types.ThinkingConfig(thinking_level=_THINKING[thinking]))
            entry = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "run_id": self.run_id, "call_id": call_id,
                     "attempt": attempt, "stage": stage, "claim_id": claim_id, "doc_id": doc_id,
                     "requested_model": model, "params": params, "system": system,
                     "parts": self._loggable(parts), "prompt_hash": prompt_hash}
            t0 = time.monotonic()
            try:
                resp = self.client.models.generate_content(model=model, contents=self._contents(parts), config=config)
            except Exception as exc:  # classified, logged, retried or raised as LLMError
                kind = _error_kind(exc)
                entry.update(status=kind, error=f"{type(exc).__name__}: {str(exc)[:500]}",
                             latency_ms=int((time.monotonic() - t0) * 1000))
                self._log(entry)
                if kind in RETRYABLE and attempt < self.max_retries:
                    time.sleep(self.backoff_s * 2 ** (attempt - 1))
                    continue
                raise LLMError(kind, str(exc)[:300]) from None
            usage = getattr(resp, "usage_metadata", None)
            entry.update(resolved_model=getattr(resp, "model_version", None), raw_response=resp.text,
                         latency_ms=int((time.monotonic() - t0) * 1000),
                         usage={k: getattr(usage, k, None) for k in
                                ("prompt_token_count", "candidates_token_count", "thoughts_token_count")})
            try:
                result = schema.model_validate_json(resp.text or "")
                problem = _runaway_string(resp.text)
            except ValidationError as ve:
                result, problem = None, str(ve)
            if problem:
                entry.update(status="invalid_output", error=problem[:1000])
                self._log(entry)
                if repaired:
                    raise LLMError("invalid_output", problem[:300]) from None
                repaired = True
                parts = list(parts) + [f"Your previous answer failed JSON schema validation: {problem[:800]}\n"
                                       f"Return only JSON that matches the schema."]
                continue
            entry["status"] = "ok"
            self._log(entry)
            return result
