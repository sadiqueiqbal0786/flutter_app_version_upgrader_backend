"""API authentication: a single admin key for the control plane and an
optional list of client keys for the public check endpoint."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _matches(provided: str | None, allowed: list[str]) -> bool:
    if not provided:
        return False
    return any(hmac.compare_digest(provided, k) for k in allowed)


async def require_admin(
    settings: Settings = Depends(_settings),
    authorization: str | None = Header(default=None),
    x_admin_key: str | None = Header(default=None),
) -> None:
    token = x_admin_key
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not _matches(token, [settings.admin_api_key]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "admin key required")


async def require_client(
    settings: Settings = Depends(_settings),
    x_api_key: str | None = Header(default=None),
) -> None:
    allowed = settings.client_api_key_list
    if not allowed:
        return
    if not _matches(x_api_key, allowed):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")
