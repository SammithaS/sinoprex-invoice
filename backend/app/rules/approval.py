"""Approval matrix and non-PO coding proposals (policy §6.2, §7)."""

from collections import Counter
from decimal import Decimal

from app.rules.models import (
    ApprovalRequirement,
    ContractTerms,
    ProposedCoding,
    ReferenceData,
    RuleResult,
    RuleStatus,
    Vendor,
)
from app.rules.money import inr
from app.rules.policy import Policy

REVIEWED_STATUSES = frozenset({"PAID", "POSTED", "APPROVED"})


def _req(role: str, reason: str, citation: str, policy: Policy) -> ApprovalRequirement:
    return ApprovalRequirement(
        role=role, name=policy.approvers.get(role), reason=reason, citation=citation
    )


def po_approvals(total: Decimal, policy: Policy) -> list[ApprovalRequirement]:
    for band in policy.approval_matrix:
        if band.up_to is None or total <= band.up_to:
            limit = f"up to {inr(band.up_to)}" if band.up_to is not None else "above the top band"
            return [
                _req(role, f"Invoice total {inr(total)} ({limit}).", "ap-policy §7.1", policy)
                for role in band.roles
            ]
    return []


def non_po_approvals(
    total: Decimal, cost_centre: str | None, policy: Policy
) -> list[ApprovalRequirement]:
    head = policy.department_head(cost_centre)
    out = [
        _req(
            head,
            f"Non-PO invoice charged to {cost_centre or 'an unknown cost centre'}.",
            "ap-policy §7.2",
            policy,
        )
    ]
    if total > policy.non_po_finance_above:
        out.append(
            _req(
                "Finance Manager",
                f"Non-PO invoice above {inr(policy.non_po_finance_above)}.",
                "ap-policy §7.2",
                policy,
            )
        )
    return out


def bank_change_approvals(policy: Policy) -> list[ApprovalRequirement]:
    return [
        _req(
            role,
            "Bank details differ from the vendor master (four-eyes).",
            "ap-policy §6.2",
            policy,
        )
        for role in policy.bank_change_approvers
    ]


def approval_rule(approvals: list[ApprovalRequirement]) -> RuleResult:
    if not approvals:
        return RuleResult(
            code="approval.matrix",
            name="No approval gate",
            status=RuleStatus.PASS,
            message="No approval required by the matrix — eligible for straight-through posting.",
            citations=["ap-policy §7.1"],
        )
    roles = " + ".join(a.role for a in approvals)
    return RuleResult(
        code="approval.matrix",
        name="No approval gate",
        status=RuleStatus.WARN,
        severity="review",
        message=f"Approval required: {roles}. "
        + " ".join(dict.fromkeys(a.reason for a in approvals)),
        citations=sorted({a.citation for a in approvals}),
    )


def non_po_eligibility(
    vendor: Vendor, contract: ContractTerms | None, policy: Policy
) -> RuleResult | None:
    """None if this vendor's invoices may not go down the non-PO branch."""
    by_contract = contract is not None and contract.no_po_citation is not None
    by_category = vendor.category in policy.non_po_categories
    if not (by_contract or by_category):
        return None
    citations = ["ap-policy §7.2"]
    if by_contract and contract and contract.no_po_citation:
        citations.append(contract.no_po_citation)
    basis = "the contract says no PO is raised" if by_contract else f"category {vendor.category}"
    return RuleResult(
        code="po.non_po",
        name="Non-PO invoice permitted",
        status=RuleStatus.PASS,
        message=f"Non-PO branch: recurring {vendor.category or 'service'} invoice permitted "
        f"without a PO ({basis}).",
        citations=citations,
    )


def propose_coding(vendor: Vendor, ref: ReferenceData) -> ProposedCoding | None:
    """Most common GL / cost centre on this vendor's reviewed invoices, with its basis."""
    coded = [
        (i.gl_account, i.cost_centre)
        for i in ref.invoices
        if i.vendor_id == vendor.id
        and i.status in REVIEWED_STATUSES
        and i.gl_account
        and i.cost_centre
    ]
    if not coded:
        return None
    (gl, cc), count = Counter(coded).most_common(1)[0]
    assert gl and cc
    return ProposedCoding(
        gl_account=gl,
        cost_centre=cc,
        basis=f"Same vendor coded to GL {gl} / {cc} in {count} reviewed case"
        + ("s" if count != 1 else "")
        + ".",
    )
