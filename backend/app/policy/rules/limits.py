"""
Per-claim and annual limit checkers.

TC008: ₹7,500 claimed vs ₹5,000 per-claim limit → REJECTED.
Message must state both the limit and the claimed amount.
"""
from __future__ import annotations

from app.models.decision import CheckResult
from app.models.domain import PolicyTerms


def check_per_claim_limit(
    claimed_amount: float,
    policy: PolicyTerms,
) -> CheckResult:
    """
    Check whether claimed_amount exceeds the per-claim limit.
    TC008 requires the message to state BOTH the limit and the claimed amount.
    """
    limit = policy.coverage.per_claim_limit

    if claimed_amount > limit:
        return CheckResult(
            check_name="limits.per_claim",
            passed=False,
            detail=(
                f"The claimed amount of ₹{claimed_amount:,.0f} exceeds the per-claim limit "
                f"of ₹{limit:,.0f}. The maximum payable per single claim is ₹{limit:,.0f}. "
                f"The excess of ₹{(claimed_amount - limit):,.0f} is not covered."
            ),
            policy_reference="coverage.per_claim_limit",
            data={
                "claimed_amount": claimed_amount,
                "per_claim_limit": limit,
                "excess": claimed_amount - limit,
            },
        )

    return CheckResult(
        check_name="limits.per_claim",
        passed=True,
        detail=(
            f"Claimed amount ₹{claimed_amount:,.0f} is within the per-claim limit "
            f"of ₹{limit:,.0f}."
        ),
        policy_reference="coverage.per_claim_limit",
        data={
            "claimed_amount": claimed_amount,
            "per_claim_limit": limit,
        },
    )


def check_minimum_claim_amount(
    claimed_amount: float,
    policy: PolicyTerms,
) -> CheckResult:
    """Check that claimed amount meets the minimum threshold."""
    minimum = policy.submission_rules.minimum_claim_amount

    if claimed_amount < minimum:
        return CheckResult(
            check_name="limits.minimum_claim",
            passed=False,
            detail=(
                f"The claimed amount of ₹{claimed_amount:,.0f} is below the minimum "
                f"claimable amount of ₹{minimum:,.0f}."
            ),
            policy_reference="submission_rules.minimum_claim_amount",
        )

    return CheckResult(
        check_name="limits.minimum_claim",
        passed=True,
        detail=f"Claimed amount ₹{claimed_amount:,.0f} meets the minimum of ₹{minimum:,.0f}.",
        policy_reference="submission_rules.minimum_claim_amount",
    )


def check_annual_opd_limit(
    claimed_amount: float,
    ytd_claims_amount: float,
    policy: PolicyTerms,
) -> CheckResult:
    """
    Check whether adding this claim would exceed the annual OPD limit.
    ytd_claims_amount = sum of all approved OPD claims for this member this year.
    """
    annual_limit = policy.coverage.annual_opd_limit
    remaining = annual_limit - ytd_claims_amount

    if claimed_amount > remaining:
        return CheckResult(
            check_name="limits.annual_opd",
            passed=False,
            detail=(
                f"Annual OPD limit of ₹{annual_limit:,.0f} would be exceeded. "
                f"Year-to-date approved claims: ₹{ytd_claims_amount:,.0f}. "
                f"Remaining balance: ₹{remaining:,.0f}. "
                f"This claim of ₹{claimed_amount:,.0f} exceeds the remaining balance by "
                f"₹{(claimed_amount - remaining):,.0f}."
            ),
            policy_reference="coverage.annual_opd_limit",
            data={
                "annual_limit": annual_limit,
                "ytd_claims_amount": ytd_claims_amount,
                "remaining": remaining,
                "claimed_amount": claimed_amount,
            },
        )

    return CheckResult(
        check_name="limits.annual_opd",
        passed=True,
        detail=(
            f"Annual OPD limit check passed. Remaining balance: ₹{remaining:,.0f} "
            f"(limit: ₹{annual_limit:,.0f}, used: ₹{ytd_claims_amount:,.0f})."
        ),
        policy_reference="coverage.annual_opd_limit",
    )
