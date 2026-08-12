"""Application readiness endpoint."""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Report sanitized startup state without contacting the model provider."""
    readiness = request.app.state.readiness
    ready = readiness["database"] == "ready" and readiness["catalog"] == "ready"
    content = {
        "status": "ready" if ready else "degraded",
        "database": readiness["database"],
        "catalog": readiness["catalog"],
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK
        if ready
        else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=content,
    )
