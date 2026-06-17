import pytest
from google.auth.exceptions import DefaultCredentialsError, RefreshError, TransportError
from google.auth.transport.requests import Request
from sqlalchemy.exc import DatabaseError


def _is_iam_auth_failure(exc: DatabaseError) -> bool:
    return "Cloud SQL IAM service account authentication failed" in str(exc)


def test_healthcheck():
    try:
        import google.auth

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if not credentials.valid:
            credentials.refresh(Request())
    except (DefaultCredentialsError, RefreshError, TransportError) as exc:
        pytest.skip(f"GCP application default credentials are not usable: {exc}")

    from ci_engine.db import connection

    try:
        assert connection.healthcheck() == 1
    except (DefaultCredentialsError, RefreshError, TransportError) as exc:
        pytest.skip(f"GCP application default credentials are not usable: {exc}")
    except DatabaseError as exc:
        if _is_iam_auth_failure(exc):
            pytest.skip(f"GCP credentials cannot authenticate as the CI DB user: {exc}")
        raise
    finally:
        connection.close_engine()
