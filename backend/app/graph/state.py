"""Graph state. Values are plain JSON (model dumps) so any checkpointer can store them."""

import operator
from typing import Annotated, Any, Literal, TypedDict

ReviewAction = Literal["APPROVE", "REJECT", "REQUEST_INFO"]


class ReviewInput(TypedDict, total=False):
    action: ReviewAction
    comment: str
    reviewer: str


class CaseState(TypedDict, total=False):
    run_id: str
    file_path: str
    file_name: str
    file_hash: str
    sample_key: str | None
    received_on: str  # ISO date
    document: dict[str, Any]  # page count, scanned?, quality, doc type hint
    extraction: dict[str, Any]  # ExtractionResult
    vendor: dict[str, Any] | None  # rules Vendor
    contract: dict[str, Any] | None  # ContractTerms
    clauses: list[dict[str, Any]]  # retrieved contract excerpts {citation, heading, text}
    contract_note: str | None
    assessment: dict[str, Any]  # rules Assessment
    decision: str
    reviews: Annotated[list[dict[str, Any]], operator.add]
    final_decision: str | None
    explanation: str | None
    next_action: str | None
    procedure_version: str
    failed: bool
    errors: Annotated[list[str], operator.add]
