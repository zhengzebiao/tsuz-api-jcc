from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.database import get_db
from app.deps.service_auth import ServicePrincipal, require_service_scope
from app.models.sample_profile import SampleProfile
from app.schemas.internal import InternalRecordResponse

router = APIRouter(prefix="/internal/v1", tags=["internal"])
_RECORD_READ_DEPENDENCY = Depends(require_service_scope("jcc:record:read"))
_DB_DEPENDENCY = Depends(get_db)


@router.get(
    "/records",
    response_model=list[InternalRecordResponse],
    summary="List active JCC records",
    responses={
        401: {"description": "Invalid service token"},
        403: {"description": "Insufficient service scope"},
    },
)
def list_internal_records(
    _principal: ServicePrincipal = _RECORD_READ_DEPENDENCY,
    db: DbSession = _DB_DEPENDENCY,
) -> list[InternalRecordResponse]:
    records = db.scalars(
        select(SampleProfile)
        .where(SampleProfile.is_active.is_(True))
        .order_by(SampleProfile.id)
    ).all()
    return [InternalRecordResponse.model_validate(record) for record in records]
