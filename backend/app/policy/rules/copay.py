"""
Co-pay rule.

TC004: ₹1,500 consultation × 0.90 = ₹1,350 (10% co-pay).
TC010: ₹3,600 (post-network-discount) × 0.90 = ₹3,240 (co-pay applied SECOND).

CRITICAL: This module is always called AFTER network_discount.apply_network_discount().
The PolicyRulesEngine enforces the order: discount → copay.
"""
from __future__ import annotations

from app.models.decision import CheckResult
from app.models.domain import PolicyTerms


def apply_copay(
    amount: float,
    claim_category: str,
    policy: PolicyTerms,
) -> tuple[float, float, CheckResult]:
    """
    Apply co-pay deduction to the (already discounted) amount.

    Args:
        amount         — amount AFTER network discount has been applied
        claim_category — OPD category key
        policy         — policy terms

    Returns:
        final_amount   — amount the member receives (post-copay)
        copay_deducted — absolute co-pay amount (₹)
        check_result   — CheckResult for the trace
    """
    category_key = claim_category.lower()
    opd_cat = policy.opd_categories.get(category_key)
    copay_pct = opd_cat.copay_percent if opd_cat else 0.0

    if copay_pct == 0.0:
        return (
            amount,
            0.0,
            CheckResult(
                check_name="copay",
                passed=True,
                detail=(
                    f"No co-pay applies for category '{claim_category}'. "
                    f"Full amount ₹{amount:,.0f} approved."
                ),
                policy_reference=f"opd_categories.{category_key}.copay_percent",
                data={"copay_pct": 0.0, "amount": amount},
            ),
        )

    copay_amount = amount * (copay_pct / 100.0)
    final = amount - copay_amount

    return (
        final,
        copay_amount,
        CheckResult(
            check_name="copay",
            passed=True,
            detail=(
                f"Co-pay of {copay_pct:.0f}% applied on ₹{amount:,.0f}: "
                f"₹{amount:,.0f} × {(1 - copay_pct/100):.2f} = ₹{final:,.0f} "
                f"(member contribution: ₹{copay_amount:,.0f})."
            ),
            policy_reference=f"opd_categories.{category_key}.copay_percent",
            data={
                "copay_pct": copay_pct,
                "pre_copay_amount": amount,
                "copay_amount": copay_amount,
                "final_amount": final,
            },
        ),
    )
