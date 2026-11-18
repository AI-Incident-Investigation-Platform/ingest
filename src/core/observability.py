import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from src.core.metrics import HTTP_DURATION, HTTP_REQUESTS


def trace_id() -> str:
    span_id = trace.get_current_span().get_span_context().trace_id
    return f"{span_id:032x}" if span_id else "0" * 32


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname,
                "src": record.name,
                "trace_id": trace_id(),
                "message": record.getMessage(),
            },
            ensure_ascii=False,
        )


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def setup_app_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in {"/health", "/ready", "/metrics"}:
            return await call_next(request)
        current_trace_id = trace_id()
        request.state.request_id = (
            current_trace_id if current_trace_id != "0" * 32 else uuid.uuid4().hex
        )
        start = time.perf_counter()
        response = await call_next(request)
        path = request.scope.get("route")
        route_path = path.path if path else "unmatched"
        HTTP_REQUESTS.labels(request.method, route_path, str(response.status_code)).inc()
        HTTP_DURATION.labels(request.method, route_path).observe(time.perf_counter() - start)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def setup_tracing(app: FastAPI, endpoint: str) -> None:
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "ingest"}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/ready,/metrics")
    RedisInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()


def shutdown_tracing() -> None:
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.shutdown()
