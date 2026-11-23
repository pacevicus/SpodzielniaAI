"""Request dependencies: who is calling, and may they?"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from spoldzielnia_ai.audit.service import AuditService
from spoldzielnia_ai.auth.cookies import SID_COOKIE, clear_sid_header
from spoldzielnia_ai.auth.login_state import LoginStateStore
from spoldzielnia_ai.auth.oidc_client import OidcClient, OidcProviderError
from spoldzielnia_ai.auth.provisioning import DbUserProvisioner, UserProvisioner
from spoldzielnia_ai.auth.sessions import (
    RefreshTokenReuseError,
    SessionBackendUnavailableError,
    SessionData,
    SessionManager,
    SessionStore,
)
from spoldzielnia_ai.auth.users import DbUserDirectory, UserDirectory, UserRecord
from spoldzielnia_ai.db.enums import Role
from spoldzielnia_ai.llm.usage_context import set_usage_owner

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping
    from uuid import UUID

    from pydantic import JsonValue

    from spoldzielnia_ai.audit.events import EventType
    from spoldzielnia_ai.core.settings import SecuritySettings, Settings
    from spoldzielnia_ai.db.session import SessionFactory


def _settings(request: Request) -> Settings:
    return request.app.state.settings


# --- Services -------------------------------------------------------------------------------


def get_oidc_client(request: Request) -> OidcClient:
    return request.app.state.oidc_client


def get_login_state_store(request: Request) -> LoginStateStore:
    return LoginStateStore(request.app.state.redis, _settings(request).oidc.login_state_ttl_seconds)


def get_user_provisioner(request: Request) -> UserProvisioner:
    return DbUserProvisioner(request.app.state.session_factory)


def get_user_directory(request: Request) -> UserDirectory:
    return DbUserDirectory(request.app.state.session_factory)


class DbAuditSink:
    """Session events (logout, refresh-token reuse) in their own transaction."""

    def __init__(self, app_state: Any) -> None:
        self._state = app_state

    async def __call__(
        self, event: EventType, actor_id: UUID, payload: Mapping[str, JsonValue]
    ) -> None:
        factory: SessionFactory = self._state.session_factory
        async with factory() as session, session.begin():
            await AuditService(session).record(event, actor_id, None, payload)


def get_session_manager(request: Request) -> SessionManager:
    return SessionManager(
        SessionStore(request.app.state.redis),
        request.app.state.oidc_client,
        request.app.state.token_sealer,
        _settings(request).security,
        request.app.state.audit_sink,
    )


OidcClientDep = Annotated[OidcClient, Depends(get_oidc_client)]
LoginStateStoreDep = Annotated[LoginStateStore, Depends(get_login_state_store)]
UserProvisionerDep = Annotated[UserProvisioner, Depends(get_user_provisioner)]
UserDirectoryDep = Annotated[UserDirectory, Depends(get_user_directory)]
SessionManagerDep = Annotated[SessionManager, Depends(get_session_manager)]


# --- Authentication -------------------------------------------------------------------------


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers=clear_sid_header(),
    )


async def current_session(request: Request) -> SessionData:
    """The caller's live session, refreshed with the IdP when due. Without a session cookie it
    fails before touching Redis or the IdP."""
    sid = request.cookies.get(SID_COOKIE)
    if not sid:
        raise _unauthenticated()
    sessions = get_session_manager(request)
    client_ip = request.client.host if request.client else None
    try:
        session = await sessions.resolve(sid, client_ip)
    except RefreshTokenReuseError:
        raise _unauthenticated() from None
    except (SessionBackendUnavailableError, OidcProviderError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication temporarily unavailable",
            headers={"Retry-After": "5"},
        ) from None
    if session is None:
        raise _unauthenticated()
    return session


def is_mfa_satisfied(session: SessionData, security: SecuritySettings) -> bool:
    """MFA as asserted by the IdP: an MFA ``acr`` or a second-factor ``amr`` method."""
    if not security.mfa_required:
        return True
    if session.acr is not None and session.acr in security.mfa_acr_values:
        return True
    return bool(set(session.amr) & set(security.mfa_amr_values))


@dataclass(frozen=True, slots=True)
class Principal:
    user: UserRecord
    session: SessionData
    mfa_satisfied: bool

    @property
    def id(self) -> UUID:
        return self.user.id


async def current_user(
    request: Request,
    session: Annotated[SessionData, Depends(current_session)],
    users: UserDirectoryDep,
    sessions: SessionManagerDep,
) -> Principal:
    user = await users.get(session.user_id)
    if user is None:
        await sessions.revoke_family(session)
        raise _unauthenticated()
    if user.is_locked:
        # A locked account keeps no live sessions.
        await sessions.revoke_family(session)
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED, detail="Account locked", headers=clear_sid_header()
        )
    # Every LLM call this request makes is counted for this user (15.2).
    set_usage_owner(user.id)
    return Principal(user, session, is_mfa_satisfied(session, _settings(request).security))


CurrentUser = Annotated[Principal, Depends(current_user)]


async def current_user_id(principal: CurrentUser) -> UUID:
    return principal.id


# --- Authorization --------------------------------------------------------------------------


def require_roles(*roles: Role) -> Callable[[Principal], Coroutine[Any, Any, Principal]]:
    """The caller must hold at least one of ``roles``."""
    if not roles:
        raise ValueError("require_roles needs at least one role")
    wanted = frozenset(roles)

    async def dependency(principal: CurrentUser) -> Principal:
        if not wanted & set(principal.user.roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return principal

    return dependency


def _step_up_challenge(acr: str) -> str:
    return f'Bearer error="insufficient_user_authentication", acr_values="{acr}"'


def step_up_url(settings: Settings) -> str:
    """A forced re-login asking the IdP for MFA."""
    acr = " ".join(settings.security.mfa_acr_values)
    step_up = f"{settings.root_path}/auth/login?prompt=login"
    if acr:
        step_up += f"&acr_values={acr}"
    return step_up


async def require_mfa(request: Request, principal: CurrentUser) -> Principal:
    """The session was established with a second factor. If not, 403 with a step-up hint. The
    frontend sends the user through ``step_up`` (a forced re-login asking for MFA)."""
    if principal.mfa_satisfied:
        return principal
    settings = _settings(request)
    acr = " ".join(settings.security.mfa_acr_values)
    step_up = step_up_url(settings)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": "mfa_required", "step_up": step_up},
        # RFC 9470 step-up challenge (cookie-authenticated here, but the semantics carry over).
        headers={"WWW-Authenticate": _step_up_challenge(acr)},
    )


ELEVATED_ROLES = (Role.EXPERT, Role.ADMIN)


def elevated_router(*roles: Role, **kwargs: Any) -> APIRouter:
    """Router for role-elevated endpoints: every route requires one of ``roles`` and MFA."""
    if not roles or not set(roles) <= set(ELEVATED_ROLES):
        raise ValueError(f"elevated_router takes roles from {ELEVATED_ROLES}")
    dependencies = [
        Depends(require_roles(*roles)),
        Depends(require_mfa),
        *kwargs.pop("dependencies", []),
    ]
    return APIRouter(dependencies=dependencies, **kwargs)
