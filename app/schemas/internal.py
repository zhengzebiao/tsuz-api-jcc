from pydantic import BaseModel, ConfigDict


class InternalRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    slug: str
    display_name: str
    is_active: bool
