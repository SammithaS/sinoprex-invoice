"""The apps platform: connections (encrypted secrets), app lifecycle, events, timers, run log.

Lifecycle (DevRev-style):  not installed → DRAFT → (validate) → ACTIVE ⇄ INACTIVE, or ERROR
with a reason. Events from the pipeline reach every ACTIVE app that subscribes to them; timers
fire from a small scheduler loop. Every run is logged; secrets are scrubbed from messages.
"""

import asyncio
import copy
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.apps.connections import CONNECTION_TYPES
from app.apps.context import AppContext, ConnectionValues, Transports
from app.apps.manifest import AppManifest, ConfigError, Timer
from app.apps.secrets import SecretBox, hint, scrub
from app.config import Settings
from app.db.models import AppAudit, AppConnection, AppInstall, AppRun, AppSecret

if TYPE_CHECKING:
    from app.graph.runner import RunService

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
HANDLER_TIMEOUT = 180  # seconds
DRAFT, ACTIVE, INACTIVE, ERROR = "DRAFT", "ACTIVE", "INACTIVE", "ERROR"


class NotFoundError(LookupError):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AppPlatform:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        apps: dict[str, AppManifest] | None = None,
        transports: Transports | None = None,
        run_service: "RunService | None" = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        from app.apps.registry import APPS

        self.sessionmaker = sessionmaker
        self.settings = settings
        self.apps = apps if apps is not None else APPS
        self.transports = transports or Transports()
        self.run_service = run_service
        self.clock = clock
        self._tasks: set[asyncio.Task[None]] = set()
        self._running: set[str] = set()
        self._loop_task: asyncio.Task[None] | None = None

    # --- secrets ---------------------------------------------------------------------------------

    @property
    def box(self) -> SecretBox:
        return SecretBox.from_setting(self.settings.secrets_key)

    async def _audit(
        self,
        session: AsyncSession,
        action: str,
        actor: str | None,
        *,
        app_id: str | None = None,
        connection_id: int | None = None,
        detail: str | None = None,
    ) -> None:
        session.add(
            AppAudit(
                app_id=app_id,
                connection_id=connection_id,
                action=action,
                actor=actor,
                detail=detail,
            )
        )

    # --- connections -----------------------------------------------------------------------------

    async def _connection(self, session: AsyncSession, cid: int) -> AppConnection:
        conn = await session.scalar(
            select(AppConnection)
            .where(AppConnection.id == cid)
            .options(selectinload(AppConnection.secrets))
        )
        if conn is None:
            raise NotFoundError(f"connection {cid} not found")
        return conn

    def _connection_public(self, conn: AppConnection, used_by: list[str]) -> dict[str, Any]:
        ctype = CONNECTION_TYPES.get(conn.type)
        stored = {s.name: s for s in conn.secrets}
        return {
            "id": conn.id,
            "type": conn.type,
            "type_name": ctype.name if ctype else conn.type,
            "name": conn.name,
            "fields": conn.fields,
            # Never the values: only whether each secret is set, a hint and when it changed.
            "secrets": [
                {
                    "name": sf.name,
                    "label": sf.label,
                    "set": sf.name in stored,
                    "hint": stored[sf.name].hint if sf.name in stored else None,
                    "updated_at": stored[sf.name].updated_at if sf.name in stored else None,
                    "updated_by": stored[sf.name].updated_by if sf.name in stored else None,
                }
                for sf in (ctype.secrets if ctype else ())
            ],
            "status": conn.status,
            "status_message": conn.status_message,
            "tested_at": conn.tested_at,
            "used_by": used_by,
            "created_at": conn.created_at,
            "updated_at": conn.updated_at,
        }

    async def _used_by(self, session: AsyncSession) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for inst in await session.scalars(select(AppInstall)):
            for cid in (inst.bindings or {}).values():
                if isinstance(cid, int):
                    out.setdefault(cid, []).append(inst.app_id)
        return out

    async def list_connections(self) -> list[dict[str, Any]]:
        async with self.sessionmaker() as session:
            used = await self._used_by(session)
            rows = await session.scalars(
                select(AppConnection)
                .options(selectinload(AppConnection.secrets))
                .order_by(AppConnection.id)
            )
            return [self._connection_public(c, used.get(c.id, [])) for c in rows]

    async def create_connection(
        self,
        type_id: str,
        name: str,
        fields: dict[str, Any],
        secrets: dict[str, str],
        actor: str | None,
    ) -> dict[str, Any]:
        ctype = CONNECTION_TYPES.get(type_id)
        if ctype is None:
            raise ConfigError(f"unknown connection type {type_id}")
        clean = self._coerce_fields(ctype.fields, fields)
        missing = [
            s.label for s in ctype.secrets if s.required and not (secrets.get(s.name) or "").strip()
        ]
        if missing:
            raise ConfigError(f"required: {', '.join(missing)}")
        box = self.box
        async with self.sessionmaker() as session:
            conn = AppConnection(
                type=type_id, name=name.strip() or ctype.name, fields=clean, updated_at=self.clock()
            )
            session.add(conn)
            await session.flush()
            for sf in ctype.secrets:
                value = (secrets.get(sf.name) or "").strip()
                if value:
                    session.add(self._encrypted(box, conn.id, sf.name, value, actor))
            await self._audit(
                session,
                "connection.created",
                actor,
                connection_id=conn.id,
                detail=f"{type_id}: {conn.name}",
            )
            await session.commit()
            cid = conn.id
        return await self.get_connection(cid)

    def _encrypted(
        self, box: SecretBox, cid: int, name: str, value: str, actor: str | None
    ) -> AppSecret:
        value = value.replace(" ", "") if name == "app_password" else value
        return AppSecret(
            connection_id=cid,
            name=name,
            ciphertext=box.encrypt(value),
            hint=hint(value),
            updated_by=actor,
            updated_at=self.clock(),
        )

    @staticmethod
    def _coerce_fields(spec: tuple[Any, ...], values: dict[str, Any]) -> dict[str, Any]:
        errors, out = [], {}
        for f in spec:
            try:
                out[f.name] = f.coerce(values.get(f.name))
            except ConfigError as exc:
                errors.append(str(exc))
        if errors:
            raise ConfigError("; ".join(errors))
        return out

    async def get_connection(self, cid: int) -> dict[str, Any]:
        async with self.sessionmaker() as session:
            conn = await self._connection(session, cid)
            used = await self._used_by(session)
            return self._connection_public(conn, used.get(cid, []))

    async def update_connection(
        self,
        cid: int,
        *,
        name: str | None,
        fields: dict[str, Any] | None,
        secrets: dict[str, str | None],
        actor: str | None,
    ) -> dict[str, Any]:
        """`secrets`: a new value replaces, None removes, absent/empty leaves it unchanged."""
        async with self.sessionmaker() as session:
            conn = await self._connection(session, cid)
            ctype = CONNECTION_TYPES[conn.type]
            if name is not None and name.strip():
                conn.name = name.strip()
            if fields is not None:
                conn.fields = self._coerce_fields(ctype.fields, {**conn.fields, **fields})
            known = {s.name for s in ctype.secrets}
            stored = {s.name: s for s in conn.secrets}
            for sname, value in secrets.items():
                if sname not in known:
                    raise ConfigError(f"unknown secret {sname}")
                if value is None:
                    if sname in stored:
                        await session.delete(stored[sname])
                        await self._audit(
                            session, "secret.removed", actor, connection_id=cid, detail=sname
                        )
                elif value.strip():
                    if sname in stored:
                        await session.delete(stored[sname])
                        await session.flush()
                    session.add(self._encrypted(self.box, cid, sname, value.strip(), actor))
                    await self._audit(
                        session,
                        "secret.replaced" if sname in stored else "secret.set",
                        actor,
                        connection_id=cid,
                        detail=sname,
                    )
            conn.status, conn.status_message, conn.updated_at = "UNTESTED", None, self.clock()
            await self._audit(session, "connection.updated", actor, connection_id=cid)
            await session.commit()
        return await self.get_connection(cid)

    async def connection_values(self, session: AsyncSession, cid: int) -> ConnectionValues:
        """Fields + decrypted secrets. Only ever used in memory, while an app runs."""
        conn = await self._connection(session, cid)
        box = self.box
        values: ConnectionValues = dict(conn.fields)
        for s in conn.secrets:
            values[s.name] = box.decrypt(s.ciphertext)
        return values

    async def test_connection(self, cid: int, actor: str | None) -> dict[str, Any]:
        async with self.sessionmaker() as session:
            conn = await self._connection(session, cid)
            ctype = CONNECTION_TYPES[conn.type]
            secret_values: list[str] = []
            try:
                values = await self.connection_values(session, cid)
                secret_values = [str(values[s.name]) for s in ctype.secrets if s.name in values]
                missing = [s.label for s in ctype.secrets if s.required and s.name not in values]
                if missing:
                    raise ConfigError(f"missing: {', '.join(missing)}")
                message = await asyncio.wait_for(ctype.test(values, self.transports), 45)
                conn.status, conn.status_message = "OK", message
            except Exception as exc:
                conn.status = "ERROR"
                conn.status_message = scrub(f"{_reason(exc)}", secret_values)[:500]
            conn.tested_at = self.clock()
            await self._audit(
                session, "connection.tested", actor, connection_id=cid, detail=conn.status
            )
            await session.commit()
        return await self.get_connection(cid)

    async def delete_connection(self, cid: int, actor: str | None) -> None:
        async with self.sessionmaker() as session:
            conn = await self._connection(session, cid)
            used = (await self._used_by(session)).get(cid, [])
            if used:
                raise ConfigError(f"in use by: {', '.join(used)}; change or uninstall those first")
            await session.delete(conn)  # its secrets go with it (cascade)
            await self._audit(
                session, "connection.deleted", actor, connection_id=cid, detail=conn.name
            )
            await session.commit()

    async def rotate_secrets(self) -> int:
        """Re-encrypt every stored secret with the newest key in SECRETS_KEY."""
        box = self.box
        async with self.sessionmaker() as session:
            rows = list(await session.scalars(select(AppSecret)))
            for s in rows:
                s.ciphertext = box.rotate(s.ciphertext)
            await session.commit()
            return len(rows)

    # --- apps: catalog and lifecycle -------------------------------------------------------------

    def manifest(self, app_id: str) -> AppManifest:
        m = self.apps.get(app_id)
        if m is None:
            raise NotFoundError(f"no app {app_id}")
        return m

    async def _install(self, session: AsyncSession, app_id: str) -> AppInstall:
        inst = await session.get(AppInstall, app_id)
        if inst is None:
            raise NotFoundError(f"{app_id} is not installed")
        return inst

    def _install_public(self, inst: AppInstall | None) -> dict[str, Any] | None:
        if inst is None:
            return None
        return {
            "state": inst.state,
            "inputs": inst.inputs,
            "bindings": inst.bindings,
            "error": inst.error,
            "installed_by": inst.installed_by,
            "installed_at": inst.installed_at,
            "updated_at": inst.updated_at,
        }

    async def list_apps(self) -> list[dict[str, Any]]:
        async with self.sessionmaker() as session:
            installs = {i.app_id: i for i in await session.scalars(select(AppInstall))}
            out = []
            for m in self.apps.values():
                last = await session.scalar(
                    select(AppRun).where(AppRun.app_id == m.id).order_by(AppRun.id.desc()).limit(1)
                )
                out.append(
                    {
                        **m.public(),
                        "install": self._install_public(installs.get(m.id)),
                        "last_run": _run_public(last) if last else None,
                    }
                )
            return out

    async def get_app(self, app_id: str) -> dict[str, Any]:
        m = self.manifest(app_id)
        async with self.sessionmaker() as session:
            inst = await session.get(AppInstall, app_id)
            runs = await session.scalars(
                select(AppRun).where(AppRun.app_id == app_id).order_by(AppRun.id.desc()).limit(50)
            )
            audit = await session.scalars(
                select(AppAudit)
                .where(AppAudit.app_id == app_id)
                .order_by(AppAudit.id.desc())
                .limit(20)
            )
            return {
                **m.public(),
                "install": self._install_public(inst),
                "runs": [_run_public(r) for r in runs],
                "audit": [
                    {"action": a.action, "actor": a.actor, "detail": a.detail, "at": a.at}
                    for a in audit
                ],
            }

    async def install(self, app_id: str, actor: str | None) -> dict[str, Any]:
        m = self.manifest(app_id)
        async with self.sessionmaker() as session:
            if await session.get(AppInstall, app_id) is None:
                session.add(
                    AppInstall(
                        app_id=app_id,
                        state=DRAFT,
                        inputs={f.name: f.default for f in m.inputs},
                        bindings={},
                        runtime={},
                        installed_by=actor,
                        updated_at=self.clock(),
                    )
                )
                await self._audit(session, "app.installed", actor, app_id=app_id)
                await session.commit()
        return await self.get_app(app_id)

    async def configure(
        self,
        app_id: str,
        inputs: dict[str, Any],
        bindings: dict[str, int | None],
        actor: str | None,
    ) -> dict[str, Any]:
        m = self.manifest(app_id)
        clean = m.coerce_inputs(inputs)
        async with self.sessionmaker() as session:
            inst = await self._install(session, app_id)
            new_bindings = dict(inst.bindings or {})
            for req in m.connections:
                if req.name not in bindings:
                    continue
                cid = bindings[req.name]
                if cid is None:
                    new_bindings.pop(req.name, None)
                    continue
                conn = await session.get(AppConnection, cid)
                if conn is None or conn.type != req.type:
                    raise ConfigError(f"{req.label}: choose a {CONNECTION_TYPES[req.type].name}")
                new_bindings[req.name] = cid
            inst.inputs, inst.bindings, inst.updated_at = clean, new_bindings, self.clock()
            await self._audit(session, "app.configured", actor, app_id=app_id)
            await session.commit()
            state = inst.state
        if state == ACTIVE:
            # Like DevRev's validate → update: a live app must stay valid after the change.
            problem = await self._validate(app_id)
            if problem:
                await self._set_state(app_id, ERROR, problem, actor, "app.error")
        return await self.get_app(app_id)

    async def _validate(self, app_id: str) -> str | None:
        """Why the app can't run right now, or None."""
        m = self.manifest(app_id)
        async with self.sessionmaker() as session:
            inst = await self._install(session, app_id)
            try:
                m.coerce_inputs(inst.inputs or {})
            except ConfigError as exc:
                return f"Settings: {exc}"
            for req in m.connections:
                if req.name not in (inst.bindings or {}):
                    return f"Choose a connection for '{req.label}'"
            try:
                ctx = await self._context(session, inst)
            except Exception as exc:
                return _reason(exc)
        if m.on_validate is not None:
            try:
                await asyncio.wait_for(m.on_validate(ctx), 60)
            except Exception as exc:
                return scrub(_reason(exc), _secret_strings(ctx))
        return None

    async def _set_state(
        self, app_id: str, state: str, error: str | None, actor: str | None, action: str
    ) -> None:
        async with self.sessionmaker() as session:
            inst = await self._install(session, app_id)
            inst.state, inst.error, inst.updated_at = state, error, self.clock()
            await self._audit(session, action, actor, app_id=app_id, detail=error)
            await session.commit()

    async def activate(self, app_id: str, actor: str | None) -> dict[str, Any]:
        m = self.manifest(app_id)
        problem = await self._validate(app_id)
        if problem:
            await self._set_state(app_id, ERROR, problem, actor, "app.error")
            return await self.get_app(app_id)
        if m.on_activate is not None:
            async with self.sessionmaker() as session:
                ctx = await self._context(session, await self._install(session, app_id))
            try:
                await m.on_activate(ctx)
            except Exception as exc:
                await self._set_state(app_id, ERROR, _reason(exc), actor, "app.error")
                return await self.get_app(app_id)
        await self._set_state(app_id, ACTIVE, None, actor, "app.activated")
        return await self.get_app(app_id)

    async def deactivate(self, app_id: str, actor: str | None) -> dict[str, Any]:
        m = self.manifest(app_id)
        async with self.sessionmaker() as session:
            inst = await self._install(session, app_id)
            was_active = inst.state == ACTIVE
        if was_active and m.on_deactivate is not None:
            try:
                async with self.sessionmaker() as session:
                    await m.on_deactivate(await self._context(session, inst))
            except Exception:
                log.exception("deactivate hook of %s failed", app_id)
        await self._set_state(app_id, INACTIVE, None, actor, "app.paused")
        return await self.get_app(app_id)

    async def uninstall(self, app_id: str, actor: str | None) -> None:
        async with self.sessionmaker() as session:
            inst = await self._install(session, app_id)
            state = inst.state
        if state == ACTIVE:
            await self.deactivate(app_id, actor)
        async with self.sessionmaker() as session:
            await session.execute(delete(AppInstall).where(AppInstall.app_id == app_id))
            await session.execute(delete(AppRun).where(AppRun.app_id == app_id))
            await self._audit(session, "app.uninstalled", actor, app_id=app_id)
            await session.commit()

    # --- running apps ----------------------------------------------------------------------------

    async def _context(self, session: AsyncSession, inst: AppInstall) -> AppContext:
        m = self.manifest(inst.app_id)
        connections: dict[str, ConnectionValues] = {}
        secret_values: list[str] = []
        for req in m.connections:
            cid = (inst.bindings or {}).get(req.name)
            if cid is not None:
                values = await self.connection_values(session, int(cid))
                connections[req.name] = values
                secret_names = {sf.name for sf in CONNECTION_TYPES[req.type].secrets}
                secret_values += [str(v) for k, v in values.items() if k in secret_names]
        return AppContext(
            app_id=inst.app_id,
            inputs=dict(inst.inputs or {}),
            connections=connections,
            sessionmaker=self.sessionmaker,
            settings=self.settings,
            transports=self.transports,
            run_service=self.run_service,
            runtime=copy.deepcopy(inst.runtime or {}),
            now=self.clock(),
            secret_values=secret_values,
        )

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        """Called by the pipeline; runs every active subscriber in the background."""
        task = asyncio.create_task(self._dispatch(event, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _dispatch(self, event: str, payload: dict[str, Any]) -> None:
        async with self.sessionmaker() as session:
            active = [
                i.app_id
                for i in await session.scalars(select(AppInstall).where(AppInstall.state == ACTIVE))
            ]
        for app_id in active:
            m = self.apps.get(app_id)
            if m is not None and event in m.events:
                await self.execute(app_id, event, payload)

    async def execute(self, app_id: str, trigger: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run one handler, log the run (secrets scrubbed) and save the app's runtime state."""
        m = self.manifest(app_id)
        handler = m.handlers[trigger]
        started = time.perf_counter()
        status, message, error = "OK", "", None
        secrets: list[str] = []
        async with self.sessionmaker() as session:
            inst = await self._install(session, app_id)
            try:
                ctx = await self._context(session, inst)
                secrets = _secret_strings(ctx)
                message = await asyncio.wait_for(handler(ctx, payload), HANDLER_TIMEOUT)
                if message.startswith("SKIP:"):
                    status, message = "SKIPPED", message.removeprefix("SKIP:").strip()
                inst.runtime = ctx.runtime
                flag_modified(inst, "runtime")
            except Exception as exc:
                status, error = "ERROR", _reason(exc)
                log.warning("app %s (%s) failed: %s", app_id, trigger, scrub(error, secrets))
            row = AppRun(
                app_id=app_id,
                trigger=trigger,
                status=status,
                message=scrub(message, secrets)[:2000] if message else None,
                error=scrub(error, secrets)[:2000] if error else None,
                run_id=payload.get("run_id"),
                duration_ms=int((time.perf_counter() - started) * 1000),
                started_at=self.clock(),
            )
            session.add(row)
            await session.commit()
            return _run_public(row)

    async def run_now(self, app_id: str, actor: str | None) -> dict[str, Any]:
        m = self.manifest(app_id)
        if not m.run_now:
            raise ConfigError(f"{m.name} has nothing to run on demand")
        async with self.sessionmaker() as session:
            inst = await self._install(session, app_id)
            if inst.state != ACTIVE:
                raise ConfigError("activate the app first")
            await self._audit(session, "app.run_now", actor, app_id=app_id)
            await session.commit()
        return await self.execute(app_id, m.run_now, {"manual": True})

    # --- n8n: migration from environment settings, inbound secret ------------------------------

    async def import_env_n8n(self) -> bool:
        """One-time move of N8N_BASE_URL / N8N_WEBHOOK_SECRET into an active n8n Connector app,
        so a server upgraded to the apps platform keeps notifying n8n without a gap."""
        s = self.settings
        if not (s.n8n_base_url and s.n8n_webhook_secret and s.secrets_key):
            return False
        async with self.sessionmaker() as session:
            if await session.get(AppInstall, "n8n-connector") is not None:
                return False
        conn = await self.create_connection(
            "n8n",
            "n8n (from server settings)",
            {"base_url": s.n8n_base_url},
            {"webhook_secret": s.n8n_webhook_secret},
            "system",
        )
        await self.install("n8n-connector", "system")
        await self.configure("n8n-connector", {}, {"n8n": conn["id"]}, "system")
        await self.activate("n8n-connector", "system")
        log.info("moved the n8n settings into the n8n Connector app")
        return True

    async def n8n_intake_secret(self) -> str | None:
        """The signing secret of the active n8n Connector (inbound email intake)."""
        async with self.sessionmaker() as session:
            inst = await session.get(AppInstall, "n8n-connector")
            if inst is None or inst.state != ACTIVE or "n8n" not in (inst.bindings or {}):
                return None
            values = await self.connection_values(session, int(inst.bindings["n8n"]))
            return str(values.get("webhook_secret") or "") or None

    # --- scheduler -------------------------------------------------------------------------------

    def _due(self, timer: Timer, inputs: dict[str, Any], state: dict[str, Any]) -> bool:
        now = self.clock()
        if timer.interval_input or timer.interval_minutes:
            raw = inputs.get(timer.interval_input or "", 0)
            minutes = float(raw or timer.interval_minutes or 0)
            last = state.get("last")
            if not minutes:
                return False
            return last is None or now - datetime.fromisoformat(last) >= timedelta(minutes=minutes)
        if timer.daily_input:
            at = str(inputs.get(timer.daily_input) or "09:00")
            local = now.replace(tzinfo=UTC).astimezone(IST)
            if timer.weekdays_input and inputs.get(timer.weekdays_input) and local.weekday() >= 5:
                return False
            hh, mm = (int(x) for x in at.split(":"))
            return (local.hour, local.minute) >= (hh, mm) and state.get("date") != str(local.date())
        return False

    async def tick(self) -> int:
        """Fire due timers of active apps once. Returns how many ran."""
        fired = 0
        async with self.sessionmaker() as session:
            installs = list(
                await session.scalars(select(AppInstall).where(AppInstall.state == ACTIVE))
            )
        for inst in installs:
            m = self.apps.get(inst.app_id)
            if m is None:
                continue
            for timer in m.timers:
                key = f"{inst.app_id}:{timer.name}"
                timers = (inst.runtime or {}).get("timers", {})
                if key in self._running or not self._due(
                    timer, inst.inputs or {}, timers.get(timer.name, {})
                ):
                    continue
                self._running.add(key)
                try:
                    await self._mark_fired(inst.app_id, timer.name)
                    await self.execute(inst.app_id, timer.name, {"timer": timer.name})
                    fired += 1
                finally:
                    self._running.discard(key)
        return fired

    async def _mark_fired(self, app_id: str, timer_name: str) -> None:
        now = self.clock()
        local = now.replace(tzinfo=UTC).astimezone(IST)
        async with self.sessionmaker() as session:
            inst = await self._install(session, app_id)
            runtime = dict(inst.runtime or {})
            timers = dict(runtime.get("timers", {}))
            timers[timer_name] = {"last": now.isoformat(), "date": str(local.date())}
            runtime["timers"] = timers
            inst.runtime = runtime
            await session.commit()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("apps scheduler tick failed")
            await asyncio.sleep(self.settings.apps_tick_seconds)

    def start(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None
        await self.drain()

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _secret_strings(ctx: AppContext) -> list[str]:
    return list(ctx.secret_values)


def _reason(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    if isinstance(exc, asyncio.TimeoutError):
        text = "timed out"
    return f"{text}"


def _run_public(r: AppRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "trigger": r.trigger,
        "status": r.status,
        "message": r.message,
        "error": r.error,
        "run_id": r.run_id,
        "duration_ms": r.duration_ms,
        "started_at": r.started_at,
    }
