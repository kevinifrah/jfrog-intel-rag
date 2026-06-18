import pytest


def _require_gcp_credentials() -> None:
    google_auth = pytest.importorskip("google.auth")

    from google.auth.exceptions import DefaultCredentialsError, RefreshError, TransportError
    from google.auth.transport.requests import Request

    try:
        credentials, _ = google_auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if not credentials.valid:
            credentials.refresh(Request())
    except (DefaultCredentialsError, RefreshError, TransportError) as exc:
        pytest.skip(f"GCP application default credentials are not usable: {exc}")


def test_embed_documents_returns_configured_dimensions():
    _require_gcp_credentials()

    pytest.importorskip("google.genai")

    from google.auth.exceptions import DefaultCredentialsError, RefreshError, TransportError

    from ci_engine.embed.gemini import embed_documents

    try:
        vectors = embed_documents(
            [
                "JFrog tracks artifact metadata.",
                "Snyk scans open source dependencies.",
            ]
        )
    except (DefaultCredentialsError, RefreshError, TransportError) as exc:
        pytest.skip(f"GCP application default credentials are not usable: {exc}")

    assert len(vectors) == 2
    assert all(len(vector) == 1536 for vector in vectors)
