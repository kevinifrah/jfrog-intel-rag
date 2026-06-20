from __future__ import annotations

import atexit
import os
from threading import Lock
from typing import Any

import google.auth
import pg8000.dbapi
from google.auth import impersonated_credentials
from google.auth.credentials import Credentials
from google.cloud.sql.connector import Connector
from pgvector.sqlalchemy import VECTOR as _VECTOR
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ci_engine.config import get as config_get

_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

INSTANCE_CONNECTION_NAME = str(
    config_get("database.instance_connection_name", "jfrog-intel-rag:europe-west1:ci-db")
)
DB_NAME = str(config_get("database.name", "ci"))
DB_USER = str(config_get("database.iam_user", "ci-engine-sa@jfrog-intel-rag.iam"))
DB_DRIVER = str(config_get("database.driver", "pg8000"))

_engine: Engine | None = None
_connector: Connector | None = None
_engine_lock = Lock()


class VECTOR(_VECTOR):
    """pgvector SQLAlchemy type that returns vector values as Python lists."""

    cache_ok = True

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        parent_processor = super().result_processor(dialect, coltype)

        def process(value: Any) -> Any:
            result = parent_processor(value) if parent_processor else value
            if hasattr(result, "tolist"):
                return result.tolist()
            return result

        return process


def _connect() -> pg8000.dbapi.Connection:
    if _connector is None:
        raise RuntimeError("Cloud SQL connector has not been initialized")

    conn: pg8000.dbapi.Connection = _connector.connect(
        _instance_connection_name(),
        _db_driver(),
        user=_db_user(),
        db=_db_name(),
        enable_iam_auth=_enable_iam_auth(),
    )
    return conn


def get_engine() -> Engine:
    """Return a singleton SQLAlchemy engine for the CI Cloud SQL database."""
    global _connector, _engine

    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _connector = Connector(
                    refresh_strategy="lazy",
                    credentials=_connector_credentials(),
                    quota_project=str(config_get("project.gcp_project_id", "jfrog-intel-rag")),
                    timeout=int(config_get("database.connect_timeout_s", 15)),
                )
                _engine = create_engine(
                    "postgresql+pg8000://",
                    creator=_connect,
                    pool_pre_ping=True,
                )

    return _engine


def healthcheck() -> int:
    """Run a lightweight database healthcheck."""
    with get_engine().connect() as conn:
        return int(conn.execute(text("SELECT 1")).scalar_one())


def describe_connection_error(exc: BaseException) -> str:
    """Return a concise, actionable database connection diagnosis."""
    message = str(exc)
    if "Cloud SQL IAM service account authentication failed" in message:
        return (
            f"Cloud SQL IAM auth failed for database user {_db_user()}. "
            f"Local ADC is impersonating {_impersonated_service_account() or 'no service account'}. "
            "Ensure the impersonated service account has roles/cloudsql.client and "
            "roles/cloudsql.instanceUser, and that the Cloud SQL instance has an IAM "
            f"database user named {_db_user()} of type CLOUD_IAM_SERVICE_ACCOUNT."
        )
    if "iamcredentials" in message.lower() or "iam.serviceAccounts.getAccessToken" in message:
        return (
            f"Could not impersonate {_impersonated_service_account()}. "
            "Grant your local ADC principal roles/iam.serviceAccountTokenCreator on that "
            "service account, then retry."
        )
    return message


def connection_settings() -> dict[str, Any]:
    """Return non-secret DB settings for diagnostics and logs."""
    return {
        "instance_connection_name": _instance_connection_name(),
        "database": _db_name(),
        "driver": _db_driver(),
        "iam_user": _db_user(),
        "enable_iam_auth": _enable_iam_auth(),
        "impersonate_service_account": _impersonated_service_account(),
    }


def close_engine() -> None:
    """Dispose the engine and close the Cloud SQL connector."""
    global _connector, _engine

    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None
        if _connector is not None:
            _connector.close()
            _connector = None


atexit.register(close_engine)


def _connector_credentials() -> Credentials | None:
    target = _impersonated_service_account()
    if not target:
        return None
    if _running_on_cloud_run():
        return None

    source_credentials, _project_id = google.auth.default(scopes=[_CLOUD_PLATFORM_SCOPE])
    if _credential_principal(source_credentials) == target:
        return source_credentials

    return impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=target,
        target_scopes=[_CLOUD_PLATFORM_SCOPE],
        quota_project_id=str(config_get("project.gcp_project_id", "jfrog-intel-rag")),
    )


def _credential_principal(credentials: Credentials) -> str | None:
    for attribute in ("service_account_email", "target_principal"):
        value = getattr(credentials, attribute, None)
        if value:
            return str(value)
    return None


def _running_on_cloud_run() -> bool:
    return bool(os.environ.get("K_SERVICE"))


def _instance_connection_name() -> str:
    return str(config_get("database.instance_connection_name", INSTANCE_CONNECTION_NAME))


def _db_name() -> str:
    return str(config_get("database.name", DB_NAME))


def _db_user() -> str:
    return str(config_get("database.iam_user", DB_USER))


def _db_driver() -> str:
    return str(config_get("database.driver", DB_DRIVER))


def _enable_iam_auth() -> bool:
    return bool(config_get("database.enable_iam_auth", True))


def _impersonated_service_account() -> str | None:
    value = config_get("database.impersonate_service_account")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


__all__ = [
    "close_engine",
    "connection_settings",
    "DB_DRIVER",
    "DB_NAME",
    "DB_USER",
    "describe_connection_error",
    "INSTANCE_CONNECTION_NAME",
    "VECTOR",
    "get_engine",
    "healthcheck",
]
