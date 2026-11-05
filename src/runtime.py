import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from aiip_kit.api_tokens.reader import DragonflyApiTokenReader
from aiip_kit.messaging.publisher import AmqpPublisher
from aiip_kit.service_auth.provider import AuthServiceTokenProvider
from fastapi import FastAPI
from redis.asyncio import Redis

from src.config import Config
from src.core.limiter import DragonflyProjectLimiter
from src.core.observability import setup_logging, setup_tracing, shutdown_tracing
from src.main import create_app

log = logging.getLogger(__name__)

cfg = Config()
redis = Redis.from_url(
    cfg.cache.url, decode_responses=True, socket_connect_timeout=2.0, socket_timeout=2.0
)
http = httpx.AsyncClient(timeout=2.0)
service_tokens = AuthServiceTokenProvider(
    http, cfg.auth.url, cfg.auth.client_id, cfg.auth.client_secret
)
token_reader = DragonflyApiTokenReader(
    redis,
    http,
    cfg.projects.url,
    lambda: service_tokens.token("projects", ["projects.tokens.read"]),
)
publisher = AmqpPublisher(cfg.rabbitmq.url)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    if missing := cfg.missing_required():
        raise RuntimeError(f"Отсутствуют настройки: {', '.join(missing)}")
    try:
        yield
    finally:
        await publisher.close()
        await http.aclose()
        await redis.aclose()
        shutdown_tracing()


async def ready() -> None:
    await redis.ping()


app = create_app(token_reader, publisher, DragonflyProjectLimiter(redis), ready)
app.router.lifespan_context = lifespan
if cfg.tracing.enabled and cfg.tracing.otlp_endpoint:
    setup_tracing(app, cfg.tracing.otlp_endpoint)


def main() -> None:
    uvicorn.run("src.runtime:app", host=cfg.service.bind_host, port=cfg.service.port, workers=1)


if __name__ == "__main__":
    main()
