"""App and connection manifests: what an app declares (DevRev snap-in style).

DevRev snap-in        Sinoprex Invoice app
--------------        -------------------
inputs           →    `inputs` (typed settings, rendered as a form)
keyrings         →    `connections` (requirements) + ConnectionType (secret fields)
event sources    →    pipeline `events` + `timers`
automations      →    `handlers` (event or timer name → function)
hooks            →    `on_validate` / `on_activate` / `on_deactivate`
commands         →    `run_now` (manual trigger from the UI)
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from app.apps.context import AppContext, Transports

FieldKind = Literal[
    "text",
    "email",
    "emails",
    "url",
    "number",
    "boolean",
    "select",
    "multiselect",
    "time",
    "textarea",
]

# Pipeline events an app can subscribe to (emitted by the run service).
EVENTS: dict[str, str] = {
    "case.waiting_review": "A case stops and waits for a reviewer",
    "case.waiting_approval": "A case waits for an approver",
    "case.request_info": "A reviewer asks the vendor for information",
    "case.closed": "A case is closed (approved or rejected)",
    "case.failed": "A case fails (e.g. the AI provider is down)",
}

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(ValueError):
    """Settings that don't satisfy the manifest (shown to the user next to the form)."""


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: FieldKind = "text"
    required: bool = False
    default: Any = None
    help: str = ""
    options: tuple[tuple[str, str], ...] = ()  # (value, label) for select / multiselect
    minimum: float | None = None
    maximum: float | None = None

    def coerce(self, value: Any) -> Any:
        """Validate and normalise one submitted value (raises ConfigError)."""
        empty = value is None or value == "" or value == []
        if empty:
            if self.required:
                raise ConfigError(f"{self.label} is required")
            return self.default
        if self.kind == "boolean":
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("1", "true", "yes", "on")
        if self.kind == "number":
            try:
                n = float(value)
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"{self.label} must be a number") from exc
            if self.minimum is not None and n < self.minimum:
                raise ConfigError(f"{self.label} must be at least {self.minimum:g}")
            if self.maximum is not None and n > self.maximum:
                raise ConfigError(f"{self.label} must be at most {self.maximum:g}")
            return int(n) if n.is_integer() else n
        if self.kind == "email":
            v = str(value).strip()
            if not _EMAIL.match(v):
                raise ConfigError(f"{self.label} is not a valid email address")
            return v
        if self.kind == "emails":
            items = value if isinstance(value, list) else re.split(r"[,;\s]+", str(value))
            emails = [e.strip() for e in items if str(e).strip()]
            bad = [e for e in emails if not _EMAIL.match(e)]
            if bad:
                raise ConfigError(f"{self.label}: not a valid email address: {', '.join(bad)}")
            if self.required and not emails:
                raise ConfigError(f"{self.label} is required")
            return emails
        if self.kind == "url":
            v = str(value).strip()
            if not v.startswith("https://"):
                raise ConfigError(f"{self.label} must start with https://")
            return v.rstrip("/")
        if self.kind == "time":
            v = str(value).strip()
            if not _TIME.match(v):
                raise ConfigError(f"{self.label} must be a time like 09:00")
            return v
        allowed = {v for v, _ in self.options}
        if self.kind == "select":
            if str(value) not in allowed:
                raise ConfigError(f"{self.label}: choose one of {', '.join(sorted(allowed))}")
            return str(value)
        if self.kind == "multiselect":
            items = value if isinstance(value, list) else [value]
            bad = [str(v) for v in items if str(v) not in allowed]
            if bad:
                raise ConfigError(f"{self.label}: unknown option(s) {', '.join(bad)}")
            return [str(v) for v in items]
        return str(value).strip()

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "required": self.required,
            "default": self.default,
            "help": self.help,
            "options": [{"value": v, "label": label} for v, label in self.options],
            "minimum": self.minimum,
            "maximum": self.maximum,
        }


@dataclass(frozen=True)
class SecretField:
    """A credential: entered once, stored encrypted, never shown again."""

    name: str
    label: str
    help: str = ""
    required: bool = True

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "help": self.help,
            "required": self.required,
        }


ConnectionValues = dict[str, Any]  # plain fields + decrypted secrets, only in memory
ConnectionTest = Callable[[ConnectionValues, "Transports"], Awaitable[str]]


@dataclass(frozen=True)
class ConnectionType:
    """A kind of credential an app can use (DevRev: keyring type)."""

    id: str
    name: str
    icon: str
    description: str
    fields: tuple[Field, ...]
    secrets: tuple[SecretField, ...]
    test: ConnectionTest  # returns a short success message; raises on failure
    setup_help: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "description": self.description,
            "fields": [f.public() for f in self.fields],
            "secrets": [s.public() for s in self.secrets],
            "setup_help": self.setup_help,
        }


@dataclass(frozen=True)
class ConnectionRequirement:
    name: str  # what the app calls it, e.g. "mailbox"
    type: str  # ConnectionType.id
    label: str


@dataclass(frozen=True)
class Timer:
    """A scheduled trigger. `interval_input` names a number input (minutes); `daily_input` names
    a time input (HH:MM, Asia/Kolkata) and `weekdays_input` an optional boolean input."""

    name: str
    label: str
    interval_input: str | None = None
    interval_minutes: float | None = None
    daily_input: str | None = None
    weekdays_input: str | None = None


Handler = Callable[["AppContext", dict[str, Any]], Awaitable[str]]
Hook = Callable[["AppContext"], Awaitable[None]]


@dataclass(frozen=True)
class AppManifest:
    id: str
    name: str
    tagline: str
    description: str  # Markdown-free plain text, a few sentences
    icon: str  # lucide icon name, rendered by the UI
    category: str
    inputs: tuple[Field, ...] = ()
    connections: tuple[ConnectionRequirement, ...] = ()
    events: tuple[str, ...] = ()
    timers: tuple[Timer, ...] = ()
    handlers: dict[str, Handler] = field(default_factory=dict)  # event or timer name → handler
    on_validate: Hook | None = None
    on_activate: Hook | None = None
    on_deactivate: Hook | None = None
    run_now: str | None = None  # handler run by the "Run now" button (a timer name)
    publisher: str = "Sinoprex Invoice"
    version: str = "1.0.0"

    def __post_init__(self) -> None:
        unknown = [e for e in self.events if e not in EVENTS]
        assert not unknown, f"{self.id}: unknown events {unknown}"
        for name in [*self.events, *(t.name for t in self.timers)]:
            assert name in self.handlers, f"{self.id}: no handler for {name}"

    def coerce_inputs(self, submitted: dict[str, Any]) -> dict[str, Any]:
        """Validate a settings form against the manifest (all errors at once)."""
        errors: list[str] = []
        out: dict[str, Any] = {}
        for f in self.inputs:
            try:
                out[f.name] = f.coerce(submitted.get(f.name))
            except ConfigError as exc:
                errors.append(str(exc))
        if errors:
            raise ConfigError("; ".join(errors))
        return out

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tagline": self.tagline,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
            "publisher": self.publisher,
            "version": self.version,
            "inputs": [f.public() for f in self.inputs],
            "connections": [
                {"name": c.name, "type": c.type, "label": c.label} for c in self.connections
            ],
            "events": [{"name": e, "label": EVENTS[e]} for e in self.events],
            "timers": [{"name": t.name, "label": t.label} for t in self.timers],
            "run_now": self.run_now,
        }
