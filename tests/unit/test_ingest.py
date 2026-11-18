from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from aiip_kit.api_tokens.reader import ApiTokenInfo, FakeApiTokenReader
from aiip_kit.errors import DependencyUnavailableError
from aiip_kit.messaging.message import MessageV1
from aiip_kit.messaging.publisher import FakePublisher
from aiip_kit.messaging.topology import Destination
from aio_pika.exceptions import DeliveryError
from pamqp.commands import Basic
from starlette.types import Message

from src.core.limiter import FakeProjectLimiter
from src.main import create_app

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
ENVIRONMENT_ID = UUID("22222222-2222-4222-8222-222222222222")
TOKEN_ID = UUID("33333333-3333-4333-8333-333333333333")


def token(*scopes: str) -> ApiTokenInfo:
    now = datetime.now(UTC)
    return ApiTokenInfo(
        state="active",
        token_id=TOKEN_ID,
        project_id=PROJECT_ID,
        environment_id=ENVIRONMENT_ID,
        scopes=frozenset(scopes),
        lifecycle_version=1,
        ver=1,
        expires_at=now + timedelta(hours=1),
        observed_at_ms=0,
        fresh_until_ms=0,
    )


def alert() -> dict[str, object]:
    return {
        "source": "prometheus",
        "source_event_id": "alert-1",
        "project_id": str(PROJECT_ID),
        "environment_id": str(ENVIRONMENT_ID),
        "service_ids": [],
        "severity": "high",
        "title": "HTTP 500",
        "started_at": "2026-11-05T10:00:00Z",
    }


@pytest.mark.asyncio
@pytest.mark.case(id="ING-001")
async def test_unknown_token_is_rejected_before_body_is_read() -> None:
    app = create_app(FakeApiTokenReader(), FakePublisher())
    body_reads = 0

    async def receive() -> dict[str, object]:
        nonlocal body_reads
        body_reads += 1
        return {"type": "http.request", "body": b"invalid", "more_body": False}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/ingest/alerts",
            "raw_path": b"/api/v1/ingest/alerts",
            "query_string": b"",
            "headers": [(b"x-ingest-key", b"bad")],
            "http_version": "1.1",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1234),
            "root_path": "",
        },
        receive,
        send,
    )
    assert sent[0]["status"] == 401
    assert body_reads == 0


@pytest.mark.asyncio
@pytest.mark.case(id="ING-002")
async def test_alert_ack_requires_broker_confirm() -> None:
    publisher = FakePublisher()
    app = create_app(FakeApiTokenReader({"secret": token("alert.ingest")}), publisher)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        publisher.fail_next = 1
        failed = await client.post(
            "/api/v1/ingest/alerts", json=alert(), headers={"X-Ingest-Key": "secret"}
        )
        accepted = await client.post(
            "/api/v1/ingest/alerts", json=alert(), headers={"X-Ingest-Key": "secret"}
        )
    assert failed.status_code == 503
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "queued"
    assert len(publisher.published) == 1
    destination, message, _ = publisher.published[0]
    assert (destination.exchange, destination.routing_key) == (
        "incidents.commands",
        "alert.received",
    )
    assert message.project_id == PROJECT_ID
    assert message.data["token_id"] == str(TOKEN_ID)
    assert message.data["receipt_id"] == accepted.json()["receipt_id"]


@pytest.mark.asyncio
@pytest.mark.case(id="ING-003")
async def test_alertmanager_limit_is_50_alerts() -> None:
    publisher = FakePublisher()
    app = create_app(FakeApiTokenReader({"secret": token("alert.ingest")}), publisher)
    payload = {
        "receiver": "aiip",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {},
                "startsAt": "2026-11-05T10:00:00Z",
                "fingerprint": str(i),
            }
            for i in range(51)
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ingest/alertmanager", json=payload, headers={"X-Ingest-Key": "secret"}
        )
    assert response.status_code == 422
    assert not publisher.published


@pytest.mark.asyncio
@pytest.mark.case(id="ING-004")
async def test_body_larger_than_256_kib_is_rejected() -> None:
    publisher = FakePublisher()
    app = create_app(FakeApiTokenReader({"secret": token("alert.ingest")}), publisher)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ingest/alerts",
            content=b"x" * (256 * 1024 + 1),
            headers={"X-Ingest-Key": "secret"},
        )
    assert response.status_code == 413
    assert not publisher.published


@pytest.mark.asyncio
@pytest.mark.case(id="ING-005")
async def test_deployment_goes_to_projects_with_deployment_scope() -> None:
    publisher = FakePublisher()
    app = create_app(FakeApiTokenReader({"secret": token("deployment.ingest")}), publisher)
    payload = {
        "source_event_id": "deploy-1",
        "project_id": str(PROJECT_ID),
        "environment_id": str(ENVIRONMENT_ID),
        "service_id": "44444444-4444-4444-8444-444444444444",
        "version": "v1",
        "deployed_at": "2026-11-05T10:00:00Z",
        "status": "succeeded",
        "source": "github",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ingest/deployments", json=payload, headers={"X-Ingest-Key": "secret"}
        )
    assert response.status_code == 202
    destination, message, _ = publisher.published[0]
    assert (destination.exchange, destination.routing_key) == (
        "projects.commands",
        "deployment.received",
    )
    assert message.data["receipt_id"] == response.json()["receipt_id"]


@pytest.mark.asyncio
@pytest.mark.case(id="ING-006")
async def test_wrong_scope_is_rejected_without_publish() -> None:
    publisher = FakePublisher()
    app = create_app(FakeApiTokenReader({"secret": token("deployment.ingest")}), publisher)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ingest/alerts", json=alert(), headers={"X-Ingest-Key": "secret"}
        )
    assert response.status_code == 403
    assert not publisher.published


@pytest.mark.asyncio
@pytest.mark.case(id="ING-007")
async def test_project_rate_limit_returns_retry_after() -> None:
    publisher = FakePublisher()
    app = create_app(
        FakeApiTokenReader({"secret": token("alert.ingest")}),
        publisher,
        FakeProjectLimiter(allowed=False),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ingest/alerts", json=alert(), headers={"X-Ingest-Key": "secret"}
        )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"
    assert not publisher.published


@pytest.mark.asyncio
@pytest.mark.case(id="ING-008")
async def test_foreign_project_is_rejected() -> None:
    publisher = FakePublisher()
    app = create_app(FakeApiTokenReader({"secret": token("alert.ingest")}), publisher)
    payload = alert()
    payload["project_id"] = "99999999-9999-4999-8999-999999999999"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ingest/alerts", json=payload, headers={"X-Ingest-Key": "secret"}
        )
    assert response.status_code == 422
    assert not publisher.published


@pytest.mark.asyncio
@pytest.mark.case(id="ING-009")
async def test_alertmanager_returns_receipt_for_each_alert() -> None:
    publisher = FakePublisher()
    app = create_app(FakeApiTokenReader({"secret": token("alert.ingest")}), publisher)
    payload = {
        "receiver": "aiip",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {},
                "startsAt": "2026-11-05T10:00:00Z",
                "fingerprint": str(i),
            }
            for i in range(2)
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ingest/alertmanager", json=payload, headers={"X-Ingest-Key": "secret"}
        )
    assert response.status_code == 202
    assert len(response.json()["receipt_ids"]) == 2
    assert len(publisher.published) == 2


@pytest.mark.asyncio
@pytest.mark.case(id="ING-010")
async def test_error_request_id_matches_header_and_is_not_zero() -> None:
    app = create_app(FakeApiTokenReader(), FakePublisher())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/ingest/alerts", content=b"invalid")
    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
    assert response.headers["X-Request-ID"] != "0" * 32


@pytest.mark.asyncio
@pytest.mark.case(id="ING-011")
async def test_alertmanager_retry_keeps_receipt_after_partial_publish() -> None:
    class FailSecondPublisher(FakePublisher):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def publish(
            self,
            destination: Destination,
            body: bytes,
            message: MessageV1,
            headers: Mapping[str, str],
        ) -> None:
            self.calls += 1
            if self.calls == 2:
                raise DependencyUnavailableError("rabbitmq")
            await super().publish(destination, body, message, headers)

    publisher = FailSecondPublisher()
    app = create_app(FakeApiTokenReader({"secret": token("alert.ingest")}), publisher)
    payload = {
        "receiver": "aiip",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {},
                "startsAt": "2026-11-05T10:00:00Z",
                "fingerprint": str(i),
            }
            for i in range(2)
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/api/v1/ingest/alertmanager", json=payload, headers={"X-Ingest-Key": "secret"}
        )
        second = await client.post(
            "/api/v1/ingest/alertmanager", json=payload, headers={"X-Ingest-Key": "secret"}
        )
    assert first.status_code == 503
    assert second.status_code == 202
    assert len(publisher.published) == 3
    receipt_ids = second.json()["receipt_ids"]
    assert publisher.published[0][1].data["receipt_id"] == receipt_ids[0]
    assert [message.data["receipt_id"] for _, message, _ in publisher.published[1:]] == receipt_ids


@pytest.mark.asyncio
@pytest.mark.case(id="ING-012")
async def test_alertmanager_ignores_unneeded_native_fields() -> None:
    publisher = FakePublisher()
    app = create_app(FakeApiTokenReader({"secret": token("alert.ingest")}), publisher)
    payload = {
        "receiver": "aiip",
        "status": "firing",
        "version": "4",
        "alerts": [
            {
                "status": "firing",
                "labels": {},
                "startsAt": "2026-11-05T10:00:00Z",
                "fingerprint": "abc",
                "generatorURL": "https://example.com",
            }
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ingest/alertmanager", json=payload, headers={"X-Ingest-Key": "secret"}
        )
    assert response.status_code == 202
    assert "generatorURL" not in publisher.published[0][1].data["payload"]


@pytest.mark.asyncio
@pytest.mark.case(id="ING-013")
async def test_broker_nack_for_alert_queue_returns_429() -> None:
    class NackingPublisher(FakePublisher):
        async def publish(
            self,
            destination: Destination,
            body: bytes,
            message: MessageV1,
            headers: Mapping[str, str],
        ) -> None:
            raise DependencyUnavailableError("rabbitmq") from DeliveryError(None, Basic.Nack())

    app = create_app(FakeApiTokenReader({"secret": token("alert.ingest")}), NackingPublisher())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/ingest/alerts", json=alert(), headers={"X-Ingest-Key": "secret"}
        )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"
