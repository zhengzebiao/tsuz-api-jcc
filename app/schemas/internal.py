from pydantic import BaseModel, ConfigDict, Field


class InternalRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    slug: str
    display_name: str
    is_active: bool


class InternalSnapshotMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    mode_name: str
    season: str
    version: str
    revision: int
    content_hash: str
    source_updated_at: str | None


class InternalResourceCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource: str
    count: int = Field(ge=0)


class InternalResourceStatisticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: InternalSnapshotMetadata
    items: list[InternalResourceCount]
