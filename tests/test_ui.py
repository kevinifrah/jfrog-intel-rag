from __future__ import annotations

import json

from fastapi.testclient import TestClient

from ci_engine.chat.schemas import ChatAnswer
from ci_engine.ui.app import create_app


def test_report_console_serves_registry_html_pdf_and_chat(tmp_path):
    report_dir = tmp_path / "sonatype"
    report_dir.mkdir()
    (report_dir / "report.html").write_text("<html><body>Sonatype report</body></html>", encoding="utf-8")
    (report_dir / "report.pdf").write_bytes(b"%PDF-1.4\n")
    (report_dir / "report.json").write_text(
        json.dumps(
            {
                "draft": {
                    "competitor": "Sonatype",
                    "generated_at": "2026-06-19T08:00:00Z",
                    "sections": [],
                },
                "validation": {"passed": True, "findings": []},
            }
        ),
        encoding="utf-8",
    )

    app = create_app(
        report_root=tmp_path,
        chat_handler=lambda request: ChatAnswer(
            answer=f"Answered: {request.question}",
            confidence="medium",
            used_tools=("test",),
        ),
    )
    client = TestClient(app)

    page = client.get("/")
    assert page.status_code == 200
    assert "Web check" not in page.text
    assert "Haiku" not in page.text
    assert "jfrog-logo.svg" in page.text
    assert 'id="competitor-index"' in page.text
    assert "Competitors" in page.text
    # The competitor picker is a typeset index, not a dropdown.
    assert 'id="report-select"' not in page.text
    registry = client.get("/api/reports").json()
    assert registry["reports"][0]["slug"] == "sonatype"
    assert registry["reports"][0]["executive_status_label"] == "Final report ready"
    assert client.get("/reports/sonatype/html").text == "<html><body>Sonatype report</body></html>"
    assert client.get("/reports/sonatype/pdf").status_code == 200
    answer = client.post("/api/chat", json={"question": "What changed?"}).json()
    assert answer["answer"] == "Answered: What changed?"


def test_pdf_blocked_response_for_failed_validation(tmp_path):
    report_dir = tmp_path / "black-duck"
    report_dir.mkdir()
    (report_dir / "report.html").write_text("<html><body>Black Duck report</body></html>", encoding="utf-8")
    (report_dir / "report.json").write_text(
        json.dumps(
            {
                "draft": {
                    "competitor": "Black Duck",
                    "generated_at": "2026-06-19T08:00:00Z",
                    "sections": [],
                },
                "validation": {
                    "passed": False,
                    "findings": [
                        {
                            "severity": "warning",
                            "code": "evidence_gap",
                            "message": "gap",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(report_root=tmp_path))

    response = client.get("/reports/black-duck/pdf")

    assert response.status_code == 409
    assert response.json()["status"] == "blocked"


def test_report_html_injects_screen_friendly_css(tmp_path):
    report_dir = tmp_path / "gitlab"
    report_dir.mkdir()
    (report_dir / "report.html").write_text(
        "<html><head><style>.section{break-inside:avoid}</style></head>"
        "<body><section class='section'>GitLab report</section></body></html>",
        encoding="utf-8",
    )
    client = TestClient(create_app(report_root=tmp_path))

    response = client.get("/reports/gitlab/html")

    assert response.status_code == 200
    assert "data-ui-report-screen" in response.text
    assert "break-inside: auto !important" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_report_html_serves_the_dossier_artifact(tmp_path):
    # The console iframe loads the polished on-disk dossier (no separate JSON viewer).
    report_dir = tmp_path / "sonatype"
    report_dir.mkdir()
    (report_dir / "report.html").write_text(
        "<html><head></head><body>Sonatype dossier</body></html>",
        encoding="utf-8",
    )
    client = TestClient(create_app(report_root=tmp_path))

    response = client.get("/reports/sonatype/html")

    assert response.status_code == 200
    assert "Sonatype dossier" in response.text
    assert "data-ui-report-screen" in response.text
