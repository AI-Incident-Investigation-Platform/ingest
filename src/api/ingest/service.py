import uuid
from datetime import UTC, datetime
from uuid import UUID

from aiip_kit.api_tokens.reader import ApiTokenInfo, ApiTokenReader
from aiip_kit.messaging.message import Actor, MessageV1
from aiip_kit.messaging.publisher import Publisher
from aiip_kit.messaging.topology import Destination
from fastapi import Request
from opentelemetry.propagate import inject
from pydantic import BaseModel, ValidationError

from src.api.ingest.schemas import AlertIngest, AlertmanagerWebhook, DeploymentIngest
from src.core.limiter import ProjectLimiter


class IngestError(Exception):
    def __init__(
        self, status: int, code: str, message: str, retry_after: int | None = None
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


class IngestService:
    def __init__(
        self, tokens: ApiTokenReader, publisher: Publisher, limiter: ProjectLimiter
    ) -> None:
        self._tokens = tokens
        self._publisher = publisher
        self._limiter = limiter

    async def authenticate(self, request: Request, scope: str) -> ApiTokenInfo:
        key = request.headers.get("X-Ingest-Key")
        if not key:
            raise IngestError(401, "ingest.invalid_key", "Ключ приёма не указан")
        token = await self._tokens.resolve(key)
        now = datetime.now(UTC)
        if (
            token is None
            or token.state != "active"
            or (token.expires_at and now >= token.expires_at)
        ):
            raise IngestError(401, "ingest.invalid_key", "Ключ приёма недействителен")
        if scope not in token.scopes:
            raise IngestError(403, "ingest.scope_denied", "У ключа нет нужного права")
        if not await self._limiter.allow(token.project_id):
            raise IngestError(429, "ingest.rate_limited", "Лимит запросов превышен", 1)
        return token

    async def parse(self, request: Request, model: type[BaseModel]) -> BaseModel:
        if (
            (length := request.headers.get("Content-Length"))
            and length.isdigit()
            and int(length) > 256 * 1024
        ):
            raise IngestError(413, "ingest.body_too_large", "Тело запроса превышает 256 КиБ")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 256 * 1024:
                raise IngestError(413, "ingest.body_too_large", "Тело запроса превышает 256 КиБ")
            body.extend(chunk)
        try:
            return model.model_validate_json(body)
        except ValidationError as exc:
            raise IngestError(422, "request.invalid", "Неверный формат запроса") from exc

    def validate_identity(
        self, token: ApiTokenInfo, project_id: UUID, environment_id: UUID
    ) -> None:
        if project_id != token.project_id:
            raise IngestError(422, "ingest.project_mismatch", "Проект не соответствует ключу")
        if token.environment_id is None or environment_id != token.environment_id:
            raise IngestError(
                422, "ingest.environment_mismatch", "Окружение не соответствует ключу"
            )

    async def alert(self, request: Request) -> UUID:
        token = await self.authenticate(request, "alert.ingest")
        payload = await self.parse(request, AlertIngest)
        assert isinstance(payload, AlertIngest)
        self.validate_identity(token, payload.project_id, payload.environment_id)
        return await self._publish(
            token,
            "incidents",
            "alert.received",
            {
                "token_id": str(token.token_id),
                "environment_id": str(token.environment_id),
                "format": "aiip",
                "payload": payload.model_dump(mode="json"),
                "received_at": datetime.now(UTC).isoformat(),
            },
        )

    async def alertmanager(self, request: Request) -> list[UUID]:
        token = await self.authenticate(request, "alert.ingest")
        payload = await self.parse(request, AlertmanagerWebhook)
        assert isinstance(payload, AlertmanagerWebhook)
        if token.environment_id is None:
            raise IngestError(422, "ingest.environment_mismatch", "Ключ не привязан к окружению")
        receipts: list[UUID] = []
        for alert in payload.alerts:
            receipt_id = uuid.uuid5(
                token.token_id,
                f"alertmanager:{payload.receiver}:{alert.fingerprint}:{alert.startsAt.isoformat()}:{alert.status}",
            )
            receipts.append(
                await self._publish(
                    token,
                    "incidents",
                    "alert.received",
                    {
                        "token_id": str(token.token_id),
                        "environment_id": str(token.environment_id),
                        "format": "alertmanager",
                        "payload": alert.model_dump(mode="json"),
                        "received_at": datetime.now(UTC).isoformat(),
                    },
                    receipt_id=receipt_id,
                )
            )
        return receipts

    async def deployment(self, request: Request) -> UUID:
        token = await self.authenticate(request, "deployment.ingest")
        payload = await self.parse(request, DeploymentIngest)
        assert isinstance(payload, DeploymentIngest)
        self.validate_identity(token, payload.project_id, payload.environment_id)
        return await self._publish(
            token,
            "projects",
            "deployment.received",
            {
                "token_id": str(token.token_id),
                "environment_id": str(token.environment_id),
                "payload": payload.model_dump(mode="json"),
            },
        )

    async def _publish(
        self,
        token: ApiTokenInfo,
        target: str,
        command: str,
        data: dict[str, object],
        *,
        receipt_id: UUID | None = None,
    ) -> UUID:
        receipt_id = receipt_id or uuid.uuid7()
        message = MessageV1.command(
            command,
            "ingest",
            {"receipt_id": str(receipt_id), **data},
            project_id=token.project_id,
            lifecycle_version=token.lifecycle_version,
            actor=Actor(kind="service", id="ingest"),
            correlation_id=receipt_id,
        )
        destination = Destination.command(target, command)
        headers: dict[str, str] = {}
        inject(headers)
        await self._publisher.publish(
            destination, message.model_dump_json().encode(), message, headers
        )
        return receipt_id
