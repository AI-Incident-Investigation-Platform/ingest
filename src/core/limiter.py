from typing import Protocol
from uuid import UUID

from aiip_kit.errors import DependencyUnavailableError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.core.limiter_lua import TOKEN_BUCKET_LUA


class ProjectLimiter(Protocol):
    async def allow(self, project_id: UUID) -> bool: ...


class FakeProjectLimiter:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    async def allow(self, project_id: UUID) -> bool:
        return self.allowed


class DragonflyProjectLimiter:
    """Атомарный token bucket: 5 запросов/с, всплеск 20 на проект."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def allow(self, project_id: UUID) -> bool:
        try:
            result = await self._redis.eval(TOKEN_BUCKET_LUA, 1, f"ingest:rate:{project_id}")
        except RedisError as exc:
            raise DependencyUnavailableError("dragonfly") from exc
        return result == 1
