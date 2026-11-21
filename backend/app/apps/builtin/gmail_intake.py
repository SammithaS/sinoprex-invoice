"""Gmail Inbox Intake: unread emails with a PDF → cases, with a reply to the sender."""

from typing import Any

from app.apps.context import AppContext
from app.apps.mail import OutgoingEmail
from app.apps.manifest import AppManifest, ConnectionRequirement, Field, Timer
from app.integrations.intake import ingest_email


async def _check(ctx: AppContext) -> None:
    if ctx.run_service is None:
        raise RuntimeError("the pipeline isn't running on this server")
    mailbox = ctx.mailbox("mailbox")
    check = getattr(mailbox, "check", None)
    if check is not None:
        await check()


def _query(ctx: AppContext) -> str:
    to = ctx.inputs.get("only_to") or ctx.connections["mailbox"]["address"]
    return f"to:{to} has:attachment filename:pdf is:unread"


def _reply_text(runs: list[Any], rejected: list[Any]) -> str:
    lines = ["Thank you, we have received your email.", ""]
    for r in runs:
        what = "already received earlier" if r.duplicate else "received"
        lines.append(f"- {r.file_name}: {what} (reference {r.run_id[:8]})")
    for x in rejected:
        lines.append(f"- {x.filename}: not accepted ({x.reason})")
    lines += ["", "Accounts Payable"]
    return "\n".join(lines)


async def poll(ctx: AppContext, payload: dict[str, Any]) -> str:
    assert ctx.run_service is not None
    mailbox = ctx.mailbox("mailbox")
    emails = await mailbox.fetch(_query(ctx), limit=10)
    if not emails:
        return "SKIP: no new invoice emails"
    created = duplicates = refused = 0
    done: list[str] = []
    for mail in emails:
        async with ctx.sessionmaker() as session:
            runs, rejected = await ingest_email(
                ctx.run_service,
                session,
                message_id=mail.message_id,
                sender=mail.sender,
                attachments=list(mail.attachments),
                max_attachments=int(ctx.inputs.get("max_attachments") or 5),
            )
        created += sum(1 for r in runs if not r.duplicate)
        duplicates += sum(1 for r in runs if r.duplicate)
        refused += len(rejected)
        if ctx.inputs.get("reply_to_sender", True) and mail.sender:
            await ctx.mailer("mailbox").send(
                OutgoingEmail(
                    to=[mail.sender],
                    subject=f"Re: {mail.subject}" if mail.subject else "Your invoice",
                    body=_reply_text(runs, rejected),
                    in_reply_to=mail.message_id,
                )
            )
        done.append(mail.uid)
    await mailbox.mark_seen(done)
    return (
        f"{len(emails)} email(s): {created} new case(s), {duplicates} already known, "
        f"{refused} attachment(s) refused"
    )


APP = AppManifest(
    id="gmail-intake",
    name="Gmail Inbox Intake",
    tagline="Turn invoice emails into cases automatically",
    description=(
        "Checks a Gmail inbox for unread emails with a PDF attached, creates one case per PDF, "
        "replies to the sender with a reference and marks the email read. Only mail sent to "
        "the chosen address is touched, so the rest of the inbox is left alone. A redelivered "
        "email never creates a second case."
    ),
    icon="inbox",
    category="Intake",
    inputs=(
        Field("poll_minutes", "Check every (minutes)", "number", default=1, minimum=1, maximum=60),
        Field(
            "only_to",
            "Only emails sent to",
            "email",
            help="Default: the Gmail address of the connection",
        ),
        Field("reply_to_sender", "Reply to the sender", "boolean", default=True),
        Field("max_attachments", "Max PDFs per email", "number", default=5, minimum=1, maximum=10),
    ),
    connections=(ConnectionRequirement("mailbox", "gmail", "Invoice inbox"),),
    timers=(Timer("poll", "Check the inbox", interval_input="poll_minutes"),),
    handlers={"poll": poll},
    on_validate=_check,
    run_now="poll",
)
