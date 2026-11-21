"""Connection types: the credentials apps can use (DevRev: keyring types)."""

import asyncio

from app.apps.context import ConnectionValues, Transports
from app.apps.manifest import ConnectionType, Field, SecretField


async def _test_gmail(values: ConnectionValues, transports: Transports) -> str:
    mailer = transports.mailer(values)
    mailbox = transports.mailbox(values)
    checks = []
    for client in (mailer, mailbox):
        check = getattr(client, "check", None)
        if check is not None:
            checks.append(check())
    await asyncio.gather(*checks)
    return f"Signed in to {values['address']} (sending and reading mail)"


async def _test_slack(values: ConnectionValues, transports: Transports) -> str:
    url = values["webhook_url"]
    if not url.startswith("https://hooks.slack.com/"):
        raise ValueError("a Slack incoming-webhook URL starts with https://hooks.slack.com/")
    async with transports.client() as client:
        res = await client.post(url, json={"text": "Sinoprex Invoice is connected ✅"})
    if res.status_code >= 300:
        raise ValueError(f"Slack answered HTTP {res.status_code}: {res.text[:120]}")
    return "Posted a test message to the channel"


async def _test_n8n(values: ConnectionValues, transports: Transports) -> str:
    async with transports.client() as client:
        res = await client.get(f"{values['base_url']}/healthz")
    if res.status_code >= 500:
        raise ValueError(f"n8n answered HTTP {res.status_code}")
    return f"n8n reachable at {values['base_url']}"


GMAIL = ConnectionType(
    id="gmail",
    name="Gmail account",
    icon="mail",
    description="Send and read email with a Gmail App Password (IMAP + SMTP).",
    fields=(
        Field("address", "Gmail address", "email", required=True),
        Field(
            "display_name",
            "Sender name",
            default="Sinoprex Invoice AP",
            help="Shown as the 'From' name on emails",
        ),
    ),
    secrets=(
        SecretField(
            "app_password",
            "App Password",
            help="Google Account → Security → 2-Step Verification → App passwords. "
            "16 characters; spaces don't matter.",
        ),
    ),
    test=_test_gmail,
    setup_help="Turn on 2-Step Verification for the account, then create an App Password "
    "(Google Account → Security → App passwords) and paste it here.",
)

SLACK = ConnectionType(
    id="slack",
    name="Slack incoming webhook",
    icon="slack",
    description="Post messages to one Slack channel.",
    fields=(Field("channel", "Channel name", help="Only for display, e.g. #ap-alerts"),),
    secrets=(
        SecretField(
            "webhook_url", "Webhook URL", help="Slack → Apps → Incoming Webhooks → Add to channel"
        ),
    ),
    test=_test_slack,
)

N8N = ConnectionType(
    id="n8n",
    name="n8n instance",
    icon="workflow",
    description="Signed webhooks to and from an n8n instance.",
    fields=(
        Field(
            "base_url", "n8n URL", "url", required=True, help="e.g. https://yourname.app.n8n.cloud"
        ),
    ),
    secrets=(
        SecretField(
            "webhook_secret",
            "Webhook signing secret",
            help="The same value as webhook_secret in the n8n workflows' Config",
        ),
    ),
    test=_test_n8n,
)

CONNECTION_TYPES: dict[str, ConnectionType] = {t.id: t for t in (GMAIL, SLACK, N8N)}
