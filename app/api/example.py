from fastapi import APIRouter, Depends

from app.deps.auth import CurrentUser, require_scope
from app.schemas.profile import ProfileResponse

router = APIRouter(prefix="/api", tags=["profile"])


@router.get(
    "/profile",
    response_model=ProfileResponse,
    summary="Return the profile for the current access token",
    responses={401: {"description": "Invalid token"}, 403: {"description": "Insufficient scope or role"}},
)
def profile(current_user: CurrentUser = Depends(require_scope("user:read"))) -> ProfileResponse:
    return ProfileResponse(user_id=current_user.user_id, message="authorized")
