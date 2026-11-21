"""Apps marketplace and connections (Phase 13). Secrets are write-only: no endpoint returns one."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.apps.connections import CONNECTION_TYPES
from app.apps.manifest import ConfigError
from app.apps.platform import AppPlatform, NotFoundError
from app.apps.secrets import SecretsUnavailableError

router = APIRouter(tags=["apps"])


def get_platform(request: Request) -> AppPlatform:
    platform: AppPlatform | None = getattr(request.app.state, "apps", None)
    if platform is None:
        raise HTTPException(status_code=503, detail="the apps platform is not running")
    return platform


PlatformDep = Annotated[AppPlatform, Depends(get_platform)]
ActorDep = Annotated[str | None, Header(alias="x-actor")]


@asynccontextmanager
async def _errors() -> AsyncIterator[None]:
    try:
        yield
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SecretsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# --- catalog and installs ----------------------------------------------------------------------


@router.get("/apps")
async def list_apps(platform: PlatformDep) -> list[dict[str, Any]]:
    return await platform.list_apps()


@router.get("/apps/{app_id}")
async def get_app(platform: PlatformDep, app_id: str) -> dict[str, Any]:
    async with _errors():
        return await platform.get_app(app_id)


@router.post("/apps/{app_id}/install")
async def install_app(platform: PlatformDep, app_id: str, actor: ActorDep = None) -> dict[str, Any]:
    async with _errors():
        return await platform.install(app_id, actor)


class ConfigureIn(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    bindings: dict[str, int | None] = Field(default_factory=dict)


@router.put("/apps/{app_id}/config")
async def configure_app(
    platform: PlatformDep, app_id: str, body: ConfigureIn, actor: ActorDep = None
) -> dict[str, Any]:
    async with _errors():
        return await platform.configure(app_id, body.inputs, body.bindings, actor)


@router.post("/apps/{app_id}/activate")
async def activate_app(
    platform: PlatformDep, app_id: str, actor: ActorDep = None
) -> dict[str, Any]:
    async with _errors():
        return await platform.activate(app_id, actor)


@router.post("/apps/{app_id}/deactivate")
async def deactivate_app(
    platform: PlatformDep, app_id: str, actor: ActorDep = None
) -> dict[str, Any]:
    async with _errors():
        return await platform.deactivate(app_id, actor)


@router.post("/apps/{app_id}/run")
async def run_app_now(platform: PlatformDep, app_id: str, actor: ActorDep = None) -> dict[str, Any]:
    async with _errors():
        return await platform.run_now(app_id, actor)


@router.delete("/apps/{app_id}", status_code=204)
async def uninstall_app(platform: PlatformDep, app_id: str, actor: ActorDep = None) -> None:
    async with _errors():
        await platform.uninstall(app_id, actor)


# --- connections -------------------------------------------------------------------------------


@router.get("/connection-types")
async def connection_types() -> list[dict[str, Any]]:
    return [t.public() for t in CONNECTION_TYPES.values()]


@router.get("/connections")
async def list_connections(platform: PlatformDep) -> list[dict[str, Any]]:
    async with _errors():
        return await platform.list_connections()


class ConnectionIn(BaseModel):
    type: str
    name: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


@router.post("/connections", status_code=201)
async def create_connection(
    platform: PlatformDep, body: ConnectionIn, actor: ActorDep = None
) -> dict[str, Any]:
    async with _errors():
        return await platform.create_connection(
            body.type, body.name, body.fields, body.secrets, actor
        )


class ConnectionPatch(BaseModel):
    name: str | None = None
    fields: dict[str, Any] | None = None
    # New value → replace; null → remove; absent → unchanged.
    secrets: dict[str, str | None] = Field(default_factory=dict)


@router.patch("/connections/{cid}")
async def update_connection(
    platform: PlatformDep, cid: int, body: ConnectionPatch, actor: ActorDep = None
) -> dict[str, Any]:
    async with _errors():
        return await platform.update_connection(
            cid, name=body.name, fields=body.fields, secrets=body.secrets, actor=actor
        )


@router.post("/connections/{cid}/test")
async def test_connection(
    platform: PlatformDep, cid: int, actor: ActorDep = None
) -> dict[str, Any]:
    async with _errors():
        return await platform.test_connection(cid, actor)


@router.delete("/connections/{cid}", status_code=204)
async def delete_connection(platform: PlatformDep, cid: int, actor: ActorDep = None) -> None:
    async with _errors():
        await platform.delete_connection(cid, actor)
