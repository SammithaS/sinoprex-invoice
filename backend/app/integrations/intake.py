"""Email → cases: shared by the n8n webhook (`POST /intake/email`) and the Gmail Inbox Intake app.

One case per PDF attachment (source EMAIL). Idempotent: `intake_ref = "<message id>#<file>"`
is unique, so a redelivered email returns the existing case instead of creating another.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Run
from app.graph.runner import RunService

MAX_UPLOAD = 10 * 1024 * 1024


@dataclass
class IngestedRun:
    run_id: str
    file_name: str
    duplicate: bool


@dataclass
class RejectedFile:
    filename: str
    reason: str


def check_pdf(filename: str, data: bytes) -> str | None:
    """Why an attachment can't become a case, or None."""
    if not filename.lower().endswith(".pdf"):
        return "not a PDF file name"
    if len(data) > MAX_UPLOAD:
        return "larger than 10 MB"
    if not data.startswith(b"%PDF"):
        return "file content is not a PDF"
    return None


async def ingest_email(
    service: RunService,
    session: AsyncSession,
    *,
    message_id: str,
    sender: str | None,
    attachments: list[tuple[str, bytes | str]],
    max_attachments: int = 5,
) -> tuple[list[IngestedRun], list[RejectedFile]]:
    """`attachments`: (file name, PDF bytes) or (file name, reason it was already refused)."""
    runs: list[IngestedRun] = []
    rejected: list[RejectedFile] = []
    seen: set[str] = set()
    for i, (filename, data) in enumerate(attachments):
        if i >= max_attachments:
            rejected.append(RejectedFile(filename, "too many attachments"))
            continue
        if isinstance(data, str):
            rejected.append(RejectedFile(filename, data))
            continue
        problem = check_pdf(filename, data)
        if problem:
            rejected.append(RejectedFile(filename, problem))
            continue
        ref = f"{message_id}#{filename}"[:300]
        if ref in seen:
            continue
        seen.add(ref)
        existing = await session.scalar(select(Run).where(Run.intake_ref == ref))
        if existing is not None:
            runs.append(IngestedRun(existing.id, existing.file_name, duplicate=True))
            continue
        run = await service.create_run(
            data, filename, source="EMAIL", intake_ref=ref, intake_from=sender or None
        )
        runs.append(IngestedRun(run.id, run.file_name, duplicate=False))
    return runs, rejected
