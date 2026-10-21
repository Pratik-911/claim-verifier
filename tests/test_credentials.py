import google.auth
from google.auth.exceptions import RefreshError

from verifier.credentials import credential_status

VERTEX = {"GOOGLE_GENAI_USE_VERTEXAI": "true", "GOOGLE_CLOUD_PROJECT": "p"}


class Creds:
    def __init__(self, fail):
        self.fail = fail

    def refresh(self, request):
        if self.fail:
            raise RefreshError("Reauthentication is needed. Please run `gcloud auth application-default login`")


def test_expired_adc_is_reported_before_any_claim_runs(monkeypatch):
    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (Creds(fail=True), "p"))
    backend, detail = credential_status(VERTEX, check_refresh=True)
    assert backend is None and "application-default login" in detail


def test_valid_adc_passes_preflight(monkeypatch):
    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (Creds(fail=False), "p"))
    assert credential_status(VERTEX, check_refresh=True)[0] == "vertex"


def test_missing_everything_is_reported():
    assert credential_status({})[0] is None
