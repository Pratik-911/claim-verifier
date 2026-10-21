"""Detect which Gemini backend is usable (optionally refreshing Vertex credentials once) without logging secrets."""
from __future__ import annotations

import os

TRUE = {"1", "true", "yes"}


def credential_status(env: dict | None = None, check_refresh: bool = False) -> tuple[str | None, str]:
    """Return (backend, detail); backend is "vertex", "api_key" or None when nothing usable is configured.

    With check_refresh, Vertex credentials are also refreshed once (an OAuth token call, not a model
    call), so expired or re-authentication-required sessions are reported before any claim runs."""
    env = os.environ if env is None else env
    if env.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in TRUE:
        if not env.get("GOOGLE_CLOUD_PROJECT"):
            return None, "GOOGLE_GENAI_USE_VERTEXAI is set but GOOGLE_CLOUD_PROJECT is missing (see .env.example)."
        try:
            import google.auth
            from google.auth.exceptions import DefaultCredentialsError
            from google.auth.exceptions import RefreshError
            try:
                creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            except DefaultCredentialsError:
                return None, ("Vertex AI is selected but no Application Default Credentials were found. "
                              "Run: gcloud auth application-default login")
            if check_refresh:
                try:
                    from google.auth.transport.requests import Request
                    creds.refresh(Request())
                except RefreshError as e:
                    return None, (f"Application Default Credentials could not be refreshed ({str(e)[:160]}). "
                                  "Run: gcloud auth application-default login")
        except ImportError:
            return None, "google-auth is not installed; run pip install -r requirements.txt"
        return "vertex", f"Vertex AI, project {env['GOOGLE_CLOUD_PROJECT']}"
    if env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY"):
        return "api_key", "Gemini Developer API key"
    return None, ("No LLM credentials found. Set GOOGLE_GENAI_USE_VERTEXAI=true and GOOGLE_CLOUD_PROJECT "
                  "(with gcloud application-default credentials) or GEMINI_API_KEY; see .env.example.")
