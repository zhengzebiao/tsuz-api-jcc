from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.database import get_db
from app.deps.service_auth import ServicePrincipal, require_service_scope
from app.jcc_data.repository import get_current_resource_counts
from app.models.sample_profile import SampleProfile
from app.schemas.internal import (
    InternalRecordResponse,
    InternalResourceStatisticsResponse,
    InternalSnapshotMetadata,
)

router = APIRouter(prefix="/internal/v1", tags=["internal"])
_RECORD_READ_DEPENDENCY = Depends(require_service_scope("jcc:record:read"))
_STATS_READ_DEPENDENCY = Depends(require_service_scope("jcc:stats:read"))
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


@router.get(
    "/resource-statistics",
    response_model=InternalResourceStatisticsResponse,
    summary="Return current JCC resource counts",
    responses={
        401: {"description": "Invalid service token"},
        403: {"description": "Insufficient service scope"},
        503: {"description": "JCC data unavailable"},
    },
)
def resource_statistics(
    _principal: ServicePrincipal = _STATS_READ_DEPENDENCY,
    db: DbSession = _DB_DEPENDENCY,
) -> InternalResourceStatisticsResponse:
    try:
        snapshot, counts = get_current_resource_counts(db, settings.jcc_data_mode)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JCC_DATA_UNAVAILABLE",
        ) from exc
    return InternalResourceStatisticsResponse(
        snapshot=InternalSnapshotMetadata(
            mode=snapshot.mode,
            mode_name=snapshot.mode_name,
            season=snapshot.season,
            version=snapshot.version,
            revision=snapshot.revision,
            content_hash=snapshot.content_hash,
            source_updated_at=snapshot.source_updated_at,
        ),
        items=[{"resource": item.resource, "count": item.count} for item in counts],
    )
