"""Connection types: the credentials apps can use (DevRev: keyring types)."""

import asyncio
from typing import TYPE_CHECKING

from app.apps.context import ConnectionValues, Transports
from app.apps.manifest import ConnectionType, Field, SecretField

if TYPE_CHECKING:
    from app.payments import RazorpayX


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


def razorpayx(values: ConnectionValues, transports: Transports) -> "RazorpayX":
    from app.payments import RazorpayX

    return RazorpayX(
        values["key_id"], values["key_secret"], values["account_number"], transports.client
    )


async def _test_razorpayx(values: ConnectionValues, transports: Transports) -> str:
    if not str(values["key_id"]).startswith(("rzp_test_", "rzp_live_")):
        raise ValueError("a RazorpayX key id starts with rzp_test_ (or rzp_live_)")
    return await razorpayx(values, transports).check()


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

RAZORPAYX = ConnectionType(
    id="razorpayx",
    name="RazorpayX account",
    icon="landmark",
    description="Pay vendors by bank transfer (RazorpayX Payouts). Test keys move no real money.",
    fields=(
        Field(
            "account_number",
            "RazorpayX account number",
            required=True,
            help="RazorpayX → Account details (the business account payouts are made from)",
        ),
    ),
    secrets=(
        SecretField("key_id", "Key ID", help="RazorpayX → Account & Settings → API keys"),
        SecretField("key_secret", "Key secret"),
        SecretField(
            "webhook_secret",
            "Webhook secret",
            help="The secret you set on the webhook (Settings → Webhooks) for payout events",
        ),
    ),
    test=_test_razorpayx,
    setup_help="In RazorpayX (test mode), create API keys, then add a webhook pointing at "
    "<API URL>/webhooks/razorpayx with the payout.processed, payout.failed and payout.reversed "
    "events and a secret of your choice.",
)

CONNECTION_TYPES: dict[str, ConnectionType] = {t.id: t for t in (GMAIL, SLACK, N8N, RAZORPAYX)}
