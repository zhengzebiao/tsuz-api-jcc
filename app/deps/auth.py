import logging
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.security import parse_pem_key
from app.services.blacklist_service import BlacklistService
from app.services.session_service import SessionService

security = HTTPBearer(auto_error=False)
logger = logging.getLogger("app.auth")


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    sid: str
    jti: str
    roles: list[str]
    scope: str

    @property
    def scopes(self) -> set[str]:
        return {scope for scope in self.scope.split() if scope}

    @property
    def role_set(self) -> set[str]:
        return set(self.roles)


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Security(security)) -> CurrentUser:
    if credentials is None:
        logger.warning("auth rejected reason=missing_token")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    try:
        payload = jwt.decode(
            credentials.credentials,
            parse_pem_key(settings.jwt_public_key),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
        user_id = _required_string_claim(payload, "sub")
        sid = _required_string_claim(payload, "sid")
        jti = _required_string_claim(payload, "jti")
        roles = _roles_claim(payload)
        scope = _scope_claim(payload)
    except jwt.ExpiredSignatureError as exc:
        logger.warning("auth rejected reason=expired_token")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc
    except jwt.InvalidSignatureError as exc:
        logger.warning("auth rejected reason=invalid_signature")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc
    except jwt.InvalidIssuerError as exc:
        logger.warning("auth rejected reason=invalid_issuer")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc
    except jwt.InvalidAudienceError as exc:
        logger.warning("auth rejected reason=invalid_audience")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc
    except jwt.PyJWTError as exc:
        logger.warning("auth rejected reason=invalid_token")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc
    except ValueError as exc:
        logger.warning("auth rejected reason=invalid_claims")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc

    try:
        BlacklistService().ensure_not_blacklisted(jti)
    except ValueError as exc:
        logger.warning("auth rejected reason=blacklisted_token jti=%s", jti)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc

    try:
        SessionService().ensure_session_active(sid)
    except ValueError as exc:
        logger.warning("auth rejected reason=revoked_session sid=%s", sid)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc

    return CurrentUser(user_id=user_id, sid=sid, jti=jti, roles=roles, scope=scope)


def require_scope(required_scope: str) -> Callable[[CurrentUser], CurrentUser]:
    return require_scopes(required_scope)


def require_scopes(*required_scopes: str) -> Callable[[CurrentUser], CurrentUser]:
    required = set(required_scopes)

    def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        missing = required - current_user.scopes
        if missing:
            logger.warning(
                "authorization rejected reason=insufficient_scope user_id=%s required_scopes=%s",
                current_user.user_id,
                ",".join(sorted(missing)),
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient scope")
        return current_user

    return dependency


def require_any_scope(*required_scopes: str) -> Callable[[CurrentUser], CurrentUser]:
    required = set(required_scopes)

    def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.scopes.isdisjoint(required):
            logger.warning(
                "authorization rejected reason=insufficient_scope user_id=%s required_scopes=%s",
                current_user.user_id,
                ",".join(sorted(required)),
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient scope")
        return current_user

    return dependency


def require_role(required_role: str) -> Callable[[CurrentUser], CurrentUser]:
    def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if required_role not in current_user.role_set:
            logger.warning(
                "authorization rejected reason=insufficient_role user_id=%s required_role=%s",
                current_user.user_id,
                required_role,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")
        return current_user

    return dependency


def require_any_role(*required_roles: str) -> Callable[[CurrentUser], CurrentUser]:
    required = set(required_roles)

    def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role_set.isdisjoint(required):
            logger.warning(
                "authorization rejected reason=insufficient_role user_id=%s required_roles=%s",
                current_user.user_id,
                ",".join(sorted(required)),
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")
        return current_user

    return dependency


def _required_string_claim(payload: dict, claim: str) -> str:
    value = payload.get(claim)
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid {claim} claim")
    return value


def _roles_claim(payload: dict) -> list[str]:
    value = payload.get("roles", [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(role, str) and role for role in value):
        raise ValueError("invalid roles claim")
    return value


def _scope_claim(payload: dict) -> str:
    value = payload.get("scope", "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("invalid scope claim")
    return value
