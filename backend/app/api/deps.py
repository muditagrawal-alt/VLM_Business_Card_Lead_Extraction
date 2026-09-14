"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, Request, status

from app.config import Settings, get_settings
from app.core.db import get_session
from app.core.errors import AppError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.storage import Storage
    from app.vlm.chain import ProviderChain

SessionDep = Annotated["AsyncSession", Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_chain(request: Request) -> ProviderChain:
    """The provider chain built once at startup."""
    return request.app.state.chain


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


ChainDep = Annotated["ProviderChain", Depends(get_chain)]
StorageDep = Annotated["Storage", Depends(get_storage)]


async def require_access_code(
    settings: SettingsDep,
    x_access_code: Annotated[str | None, Header()] = None,
) -> None:
    """Optional shared passcode for the public demo.

    Off by default. When set, it gates only the endpoints that cost inference
    time, so a reviewer can be given a link that strangers cannot run up a
    bill on.
    """
    if not settings.app_access_code:
        return
    if x_access_code != settings.app_access_code:
        raise AppError("a valid access code is required", status_code=status.HTTP_401_UNAUTHORIZED)


AccessCodeDep = Annotated[None, Depends(require_access_code)]
