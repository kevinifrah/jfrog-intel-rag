from __future__ import annotations

import json
from typing import Any

import google.auth

from ci_engine.db import connection


def run() -> dict[str, Any]:
    report: dict[str, Any] = {
        "connection": connection.connection_settings(),
        "adc": _adc_summary(),
    }
    try:
        report["healthcheck"] = connection.healthcheck()
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["error"] = connection.describe_connection_error(exc)
    finally:
        connection.close_engine()

    return report


def main() -> int:
    print(json.dumps(run(), indent=2, sort_keys=True, default=str))
    return 0


def _adc_summary() -> dict[str, Any]:
    try:
        credentials, project_id = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    principal = (
        getattr(credentials, "service_account_email", None)
        or getattr(credentials, "target_principal", None)
    )
    return {
        "ok": True,
        "credential_type": type(credentials).__name__,
        "project_id": project_id,
        "principal": principal,
        "quota_project_id": getattr(credentials, "quota_project_id", None),
    }


if __name__ == "__main__":
    raise SystemExit(main())
