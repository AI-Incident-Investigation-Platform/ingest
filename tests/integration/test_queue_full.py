import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid7

import aio_pika
import pytest
from aiip_kit.api_tokens.reader import ApiTokenInfo, FakeApiTokenReader
from aiip_kit.messaging.publisher import AmqpPublisher

from src.api.ingest.service import IngestError, IngestService
from src.core.limiter import FakeProjectLimiter


@pytest.mark.asyncio
@pytest.mark.case(id="ING-014")
async def test_full_bounded_queue_returns_429() -> None:
    url = os.environ.get("AIIP_TEST_RABBITMQ_URL")
    if not url:
        pytest.skip("AIIP_TEST_RABBITMQ_URL is required")
    if urlparse(url).hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Integration test requires local RabbitMQ")
    connection = await aio_pika.connect_robust(url)
    channel = await connection.channel()
    queue = await channel.declare_queue("incidents.alerts", passive=True)
    await queue.purge()
    publisher = AmqpPublisher(url)
    token = ApiTokenInfo(
        state="active",
        token_id=uuid7(),
        project_id=uuid7(),
        environment_id=uuid7(),
        scopes=frozenset({"alert.ingest"}),
        lifecycle_version=1,
        ver=1,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        observed_at_ms=0,
        fresh_until_ms=0,
    )
    service = IngestService(FakeApiTokenReader(), publisher, FakeProjectLimiter())
    try:
        for number in range(1100):
            try:
                await service._publish(
                    token,
                    "incidents",
                    "alert.received",
                    {"receipt_id": str(uuid7()), "sequence": number},
                )
            except IngestError as exc:
                assert exc.status == 429
                assert exc.code == "ingest.queue_full"
                break
        else:
            pytest.fail("Queue did not reject publisher after 1100 messages")
    finally:
        await publisher.close()
        await queue.purge()
        await connection.close()
