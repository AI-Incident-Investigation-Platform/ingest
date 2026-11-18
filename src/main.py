from collections.abc import Awaitable, Callable

from aiip_kit.api_tokens.reader import ApiTokenReader
from aiip_kit.errors import DependencyUnavailableError
from aiip_kit.messaging.publisher import Publisher
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.ingest.router import build_router
from src.api.ingest.service import IngestError, IngestService
from src.core.limiter import FakeProjectLimiter, ProjectLimiter
from src.core.observability import setup_app_observability


def create_app(
    tokens: ApiTokenReader,
    publisher: Publisher,
    limiter: ProjectLimiter | None = None,
    readiness: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    service = IngestService(tokens, publisher, limiter or FakeProjectLimiter())
    app = FastAPI(title="AIIP ingest")
    app.include_router(build_router(service))
    setup_app_observability(app)

    @app.exception_handler(IngestError)
    async def ingest_error(request: Request, exc: IngestError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else {}
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request.state.request_id,
                }
            },
            status_code=exc.status,
            headers=headers,
        )

    @app.exception_handler(DependencyUnavailableError)
    async def dependency_error(request: Request, exc: DependencyUnavailableError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": "ingest.dependency_unavailable",
                    "message": "Зависимость недоступна",
                    "request_id": request.state.request_id,
                }
            },
            status_code=503,
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        if readiness is not None:
            await readiness()
        return {"status": "ok"}

    return app
