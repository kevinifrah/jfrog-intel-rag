from __future__ import annotations

from threading import Lock
from typing import Any

import pg8000.dbapi
from google.cloud.sql.connector import Connector
from pgvector.pg8000 import register_vector
from pgvector.sqlalchemy import VECTOR as _VECTOR
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

INSTANCE_CONNECTION_NAME = "jfrog-intel-rag:europe-west1:ci-db"
DB_NAME = "ci"
DB_USER = "ci-engine-sa@jfrog-intel-rag.iam"
DB_DRIVER = "pg8000"

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
        INSTANCE_CONNECTION_NAME,
        DB_DRIVER,
        user=DB_USER,
        db=DB_NAME,
        enable_iam_auth=True,
    )
    register_vector(conn)
    return conn


def get_engine() -> Engine:
    """Return a singleton SQLAlchemy engine for the CI Cloud SQL database."""
    global _connector, _engine

    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _connector = Connector(refresh_strategy="lazy")
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


__all__ = [
    "close_engine",
    "DB_DRIVER",
    "DB_NAME",
    "DB_USER",
    "INSTANCE_CONNECTION_NAME",
    "VECTOR",
    "get_engine",
    "healthcheck",
]
