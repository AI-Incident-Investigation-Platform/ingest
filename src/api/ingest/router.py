from fastapi import APIRouter, Request, status

from src.api.ingest.schemas import AlertmanagerAck, IngestAck
from src.api.ingest.service import IngestService


def build_router(service: IngestService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

    @router.post("/alerts", response_model=IngestAck, status_code=status.HTTP_202_ACCEPTED)
    async def ingest_alert(request: Request) -> IngestAck:
        return IngestAck(receipt_id=await service.alert(request))

    @router.post(
        "/alertmanager", response_model=AlertmanagerAck, status_code=status.HTTP_202_ACCEPTED
    )
    async def ingest_alertmanager(request: Request) -> AlertmanagerAck:
        return AlertmanagerAck(receipt_ids=await service.alertmanager(request))

    @router.post("/deployments", response_model=IngestAck, status_code=status.HTTP_202_ACCEPTED)
    async def ingest_deployment(request: Request) -> IngestAck:
        return IngestAck(receipt_id=await service.deployment(request))

    return router
