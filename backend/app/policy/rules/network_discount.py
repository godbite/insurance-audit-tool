"""
Network discount rule.

TC010: Apollo Hospitals (network) → 20% discount applied FIRST.
Returns (discounted_amount, discount_applied, is_network).
The caller (PolicyRulesEngine) must apply this BEFORE copay.
"""
from __future__ import annotations

from app.models.decision import CheckResult
from app.models.domain import PolicyTerms


def apply_network_discount(
    amount: float,
    hospital_name: str | None,
    claim_category: str,
    policy: PolicyTerms,
) -> tuple[float, float, bool, CheckResult]:
    """
    Apply network hospital discount if applicable.

    Returns:
        discounted_amount  — amount after discount
        discount_applied   — absolute discount amount (₹)
        is_network         — True if hospital is in-network
        check_result       — CheckResult for the trace
    """
    category_key = claim_category.lower()
    opd_cat = policy.opd_categories.get(category_key)
    discount_pct = opd_cat.network_discount_percent if opd_cat else 0.0

    is_network = policy.is_network_hospital(hospital_name or "")

    if not is_network or discount_pct == 0.0:
        reason = (
            f"Hospital '{hospital_name or 'unknown'}' is not in the network hospital list."
            if not is_network
            else f"No network discount applies for category '{claim_category}'."
        )
        return (
            amount,
            0.0,
            is_network,
            CheckResult(
                check_name="network_discount",
                passed=True,
                detail=f"No network discount applied. {reason} Amount remains ₹{amount:,.0f}.",
                policy_reference=f"opd_categories.{category_key}.network_discount_percent",
                data={"is_network": is_network, "discount_pct": 0.0, "amount": amount},
            ),
        )

    discount_amount = amount * (discount_pct / 100.0)
    discounted = amount - discount_amount

    return (
        discounted,
        discount_amount,
        True,
        CheckResult(
            check_name="network_discount",
            passed=True,
            detail=(
                f"Hospital '{hospital_name}' is a network hospital. "
                f"{discount_pct:.0f}% network discount applied: "
                f"₹{amount:,.0f} × {(1 - discount_pct/100):.2f} = ₹{discounted:,.0f} "
                f"(discount: ₹{discount_amount:,.0f})."
            ),
            policy_reference=f"opd_categories.{category_key}.network_discount_percent",
            data={
                "hospital_name": hospital_name,
                "is_network": True,
                "discount_pct": discount_pct,
                "original_amount": amount,
                "discount_amount": discount_amount,
                "discounted_amount": discounted,
            },
        ),
    )
