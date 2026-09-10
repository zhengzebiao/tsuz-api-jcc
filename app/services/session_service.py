from app.core.config import settings
from app.core.redis import get_main_redis


class SessionService:
    def revoke_session(self, sid: str) -> None:
        get_main_redis().set(f"{settings.session_prefix}{sid}", "revoked")

    def ensure_session_active(self, sid: str) -> None:
        if get_main_redis().get(f"{settings.session_prefix}{sid}") == "revoked":
            raise ValueError("session is revoked")
