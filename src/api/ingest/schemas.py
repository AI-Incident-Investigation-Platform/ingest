from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class AlertIngest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=200)
    source_event_id: str = Field(min_length=1, max_length=500)
    project_id: UUID
    environment_id: UUID
    service_ids: list[UUID]
    severity: Literal["critical", "high", "medium", "low"]
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    started_at: AwareDatetime
    labels: dict[str, str] = Field(default_factory=dict)


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: AwareDatetime
    endsAt: AwareDatetime | None = None
    fingerprint: str = Field(min_length=1)


class AlertmanagerWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    receiver: str
    status: Literal["firing", "resolved"]
    groupKey: str | None = None
    alerts: list[AlertmanagerAlert] = Field(min_length=1, max_length=50)


class DeploymentIngest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_event_id: str = Field(min_length=1)
    project_id: UUID
    environment_id: UUID
    service_id: UUID
    version: str = Field(min_length=1)
    commit_sha: str | None = None
    deployed_at: AwareDatetime
    status: Literal["started", "succeeded", "failed", "rolled_back"]
    source: str = Field(min_length=1)
    source_url: str | None = None


class IngestAck(BaseModel):
    receipt_id: UUID
    status: Literal["queued"] = "queued"


class AlertmanagerAck(BaseModel):
    receipt_ids: list[UUID]
    status: Literal["queued"] = "queued"
