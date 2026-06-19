from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ci_engine.chat.schemas import ChatAnswer, ChatRequest
from ci_engine.chat.service import run_chat, stream_chat_events
from ci_engine.chat.report_store import ReportArtifactStore
from ci_engine.config import get as config_get

ChatHandler = Callable[[ChatRequest], ChatAnswer]

_UI_DIR = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_UI_DIR / "templates"))


def create_app(
    *,
    report_root: str | Path | None = None,
    chat_handler: ChatHandler | None = None,
) -> FastAPI:
    root = Path(report_root or str(config_get("chat.report_root", "reports")))
    app = FastAPI(title="JFrog Competitive Intelligence Console")
    app.mount("/static", StaticFiles(directory=str(_UI_DIR / "static")), name="static")

    def store() -> ReportArtifactStore:
        return ReportArtifactStore(root)

    @app.get("/")
    async def index(request: Request):
        return _TEMPLATES.TemplateResponse(
            request,
            "console.html.j2",
            {
                "title": "JFrog Competitive Intelligence",
            },
        )

    @app.get("/api/reports")
    async def reports() -> dict[str, Any]:
        summaries = [summary.model_dump(mode="json") for summary in store().list_reports()]
        return {"reports": summaries, "report_root": str(root)}

    @app.get("/reports/{slug}/html")
    async def report_html(slug: str):
        path = store().html_path(slug)
        if path is None:
            raise HTTPException(status_code=404, detail="HTML report not found")
        return HTMLResponse(
            _screen_friendly_report_html(path),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/reports/{slug}/pdf")
    async def report_pdf(slug: str):
        artifact_store = store()
        path = artifact_store.pdf_path(slug)
        if path is not None:
            return FileResponse(
                path,
                media_type="application/pdf",
                filename=f"{slug}-report.pdf",
            )
        summary = artifact_store.get_report(slug)
        if summary and summary.pdf_status == "blocked":
            return JSONResponse(
                status_code=409,
                content={
                    "status": "blocked",
                    "message": "PDF generation is blocked by report validation.",
                    "error_count": summary.error_count,
                    "warning_count": summary.warning_count,
                    "blocker_codes": list(summary.blocker_codes),
                },
            )
        raise HTTPException(status_code=404, detail="PDF report not found")

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> dict[str, Any]:
        handler = chat_handler or (
            lambda chat_request: run_chat(chat_request, report_root=str(root))
        )
        answer = handler(request)
        return answer.model_dump(mode="json")

    @app.websocket("/ws/chat")
    async def chat_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                payload = await websocket.receive_json()
                request = ChatRequest.model_validate(payload)
                async for event in stream_chat_events(request, report_root=str(root)):
                    await websocket.send_json(event)
        except WebSocketDisconnect:
            return

    return app


def _screen_friendly_report_html(path: Path) -> str:
    html = path.read_text(encoding="utf-8")
    if "</head>" not in html:
        return html
    screen_css = """
  <style data-ui-report-screen>
    @media screen {
      html,
      body {
        min-height: 100%;
        overflow: auto !important;
      }
      * {
        break-before: auto !important;
        break-after: auto !important;
        break-inside: auto !important;
        page-break-before: auto !important;
        page-break-after: auto !important;
        page-break-inside: auto !important;
        -webkit-column-break-before: auto !important;
        -webkit-column-break-after: auto !important;
        -webkit-column-break-inside: auto !important;
      }
      .page,
      .hero,
      .section {
        display: block !important;
      }
    }
  </style>
"""
    return html.replace("</head>", f"{screen_css}</head>", 1)


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(),
        host=str(config_get("ui.host", "127.0.0.1")),
        port=int(config_get("ui.port", 8090)),
        log_level="info",
    )


if __name__ == "__main__":
    main()
