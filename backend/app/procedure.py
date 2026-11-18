"""The versioned Operating Procedure (plan §2b.1): the `Policy` numbers, edited in /procedure.

Each case loads the active version when it starts and records it (`runs.procedure_version`), so a
change applies from the next case. Versions are immutable; editing creates a new version.
"""

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Procedure
from app.rules.policy import Policy

INITIAL_NOTE = "Initial procedure, from AP Policy v3.2 (data/knowledge/ap-policy.md)."


def label(version: int) -> str:
    return f"v{version}"


async def ensure_initial(session: AsyncSession) -> Procedure:
    """Version 1 = the policy defaults, created on first use."""
    active = await session.scalar(select(Procedure).where(Procedure.active.is_(True)))
    if active is not None:
        return active
    latest = await session.scalar(select(Procedure).order_by(Procedure.version.desc()).limit(1))
    if latest is not None:  # versions exist but none active: activate the newest
        latest.active = True
        await session.commit()
        return latest
    first = Procedure(
        version=1,
        policy=Policy(version=label(1)).model_dump(mode="json"),
        note=INITIAL_NOTE,
        created_by="system",
        active=True,
    )
    session.add(first)
    await session.commit()
    return first


async def active_policy(session: AsyncSession) -> Policy:
    proc = await ensure_initial(session)
    return Policy.model_validate({**proc.policy, "version": label(proc.version)})


async def create_version(
    session: AsyncSession, policy: Policy, note: str | None, created_by: str | None, activate: bool
) -> Procedure:
    await ensure_initial(session)
    latest = await session.scalar(select(func.max(Procedure.version))) or 0
    version = latest + 1
    proc = Procedure(
        version=version,
        policy=policy.model_copy(update={"version": label(version)}).model_dump(mode="json"),
        note=note,
        created_by=created_by,
        active=False,
    )
    session.add(proc)
    await session.flush()
    if activate:
        await _activate(session, version)
    await session.commit()
    return proc


async def activate(session: AsyncSession, version: int) -> Procedure | None:
    proc = await session.get(Procedure, version)
    if proc is None:
        return None
    await _activate(session, version)
    await session.commit()
    await session.refresh(proc)
    return proc


async def _activate(session: AsyncSession, version: int) -> None:
    await session.execute(update(Procedure).values(active=Procedure.version == version))


def diff(old: dict[str, Any], new: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    """Flat list of changed settings between two policy dumps (for the version history)."""
    out: list[dict[str, Any]] = []
    for key in sorted(set(old) | set(new)):
        if key == "version":
            continue
        path = f"{prefix}{key}"
        a, b = old.get(key), new.get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            out.extend(diff(a, b, f"{path}."))
        elif a != b:
            out.append({"path": path, "old": a, "new": b})
    return out
