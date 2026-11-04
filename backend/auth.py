from __future__ import annotations

from dataclasses import dataclass
from fastapi import HTTPException, Request, status

from .config import settings


@dataclass
class RequestIdentity:
    actor_type: str  # "user" | "agent" | "system"
    actor_id: str
    authenticated_via: str  # "none_profile" | "api_token" | "sso_header"


def resolve_identity(request: Request) -> RequestIdentity:
    """Pluggable identity resolver.

    Defaults to `AMR_AUTH_MODE=none` (zero-auth profile mode) for rapid internal
    team deployment, and cleanly upgrades to token or corporate SSO proxy headers
    via environment configuration without changing downstream route handlers.
    """
    mode = settings.auth_mode.lower().strip()
    profile_id = (
        request.headers.get("X-User-Profile")
        or request.query_params.get("user_id")
        or "member_a"
    )
    agent_id = request.headers.get("X-Agent-Id") or request.query_params.get("agent_id")

    if mode == "none":
        if agent_id:
            return RequestIdentity("agent", agent_id, "none_profile")
        return RequestIdentity("user", profile_id, "none_profile")

    if mode == "token":
        auth_header = request.headers.get("Authorization", "")
        bearer = (
            auth_header.removeprefix("Bearer ").strip()
            if auth_header.startswith("Bearer ")
            else ""
        )
        token = bearer or request.headers.get("X-API-Token", "")
        if not settings.shared_api_token or token != settings.shared_api_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="유효한 API 토큰이 필요합니다 (AMR_AUTH_MODE=token).",
            )
        if agent_id:
            return RequestIdentity("agent", agent_id, "api_token")
        return RequestIdentity("user", profile_id, "api_token")

    if mode == "sso_header":
        sso_user = request.headers.get(settings.sso_user_header, "").strip()
        if not sso_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"사내 SSO 인증 헤더({settings.sso_user_header})가 누락되었습니다.",
            )
        if agent_id:
            return RequestIdentity("agent", agent_id, "sso_header")
        return RequestIdentity("user", sso_user, "sso_header")

    return RequestIdentity("user", profile_id, "none_profile")
