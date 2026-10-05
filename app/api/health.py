from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.core.config import settings
from app.core.database import SessionLocal

router = APIRouter(tags=["health"])


@router.get("/health", summary="Service health check")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name, "env": settings.app_env}


@router.get("/readyz", summary="Service readiness check")
def readiness(request: Request, response: Response) -> dict[str, object]:
    checks: dict[str, dict[str, str]] = {}
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        checks["database"] = {"status": "ok"}
    except Exception:  # noqa: BLE001 - readiness must not expose dependency details
        checks["database"] = {"status": "failed"}

    runtime = getattr(request.app.state, "agent_runtime", None)
    if runtime is None:
        checks["runtime"] = {"status": "not_configured"}
        checks["llm"] = {"status": "not_configured"}
    else:
        checks["runtime"] = {"status": "ok" if not runtime._stopping else "stopping"}
        checks["llm"] = {"status": "ok"}

    checks["rag"] = {"status": "ok" if settings.rag_enabled else "disabled"}
    required = (checks["database"]["status"] == "ok" and checks["runtime"]["status"] == "ok")
    if settings.rag_enabled:
        required = required and checks["rag"]["status"] == "ok"
    body = {"status": "ready" if required else "not_ready", "checks": checks}
    if not required:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return body
