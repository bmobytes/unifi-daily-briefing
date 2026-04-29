from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .config import Settings, get_settings
from .service import BriefingService


TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app(
    service_override: BriefingService | None = None,
    settings_override: Settings | None = None,
) -> FastAPI:
    app_settings = settings_override or get_settings()
    app = FastAPI(title="UniFi Daily Briefing")
    app.state.settings = app_settings
    app.state.service = service_override

    def get_service() -> BriefingService:
        if app.state.service is None:
            app.state.service = BriefingService(app.state.settings)
        return app.state.service

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/api/reports/latest")
    def latest_report() -> dict:
        report = get_service().latest_report()
        if not report:
            raise HTTPException(status_code=404, detail="No reports yet")
        return report

    @app.get("/api/reports")
    def list_reports() -> list[dict]:
        return get_service().list_reports()

    @app.get("/api/reports/{report_id}")
    def get_report(report_id: int) -> dict:
        report = get_service().get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        return report

    @app.post("/api/reports/run")
    def run_report() -> dict:
        return get_service().generate_report()

    @app.post("/api/collect")
    def run_collect() -> dict:
        return get_service().collect()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        service = get_service()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "latest": service.latest_report(),
                "reports": service.list_reports(),
                "host": app_settings.ingress_host,
            },
        )

    @app.get("/reports/{report_id}", response_class=HTMLResponse)
    def report_page(request: Request, report_id: int):
        report = get_service().get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        return templates.TemplateResponse(request, "report.html", {"report": report})

    return app


app = create_app()
