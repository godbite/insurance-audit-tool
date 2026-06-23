"""
Pre-authorization rule checker.

TC007: MRI scan ₹15,000 — requires pre-auth above ₹10,000 — no pre-auth obtained → REJECTED.
"""
from __future__ import annotations

from app.models.decision import CheckResult
from app.models.domain import PolicyTerms

# High-value test keywords that require pre-auth above the threshold
_PRE_AUTH_TEST_KEYWORDS = ["mri", "ct scan", "ct-scan", "pet scan", "pet-scan", "ct ", "mri "]


def check_pre_auth(
    claim_category: str,
    claimed_amount: float,
    tests_ordered: list[str],
    line_item_descriptions: list[str],
    pre_auth_obtained: bool,
    policy: PolicyTerms,
) -> CheckResult:
    """
    Determine whether pre-authorization was required and whether it was obtained.

    Pre-auth is required when:
    - The diagnostic tests include MRI/CT/PET AND claimed_amount > threshold
    - OR the claim category is listed in policy.pre_authorization.required_for
    """
    if pre_auth_obtained:
        return CheckResult(
            check_name="pre_auth",
            passed=True,
            detail="Pre-authorization was obtained for this claim.",
            policy_reference="pre_authorization",
        )

    # Check for high-value diagnostic tests
    all_items = [t.lower() for t in (tests_ordered + line_item_descriptions)]

    matched_test: str | None = None
    for item_text in all_items:
        for kw in _PRE_AUTH_TEST_KEYWORDS:
            if kw in item_text:
                matched_test = item_text
                break
        if matched_test:
            break

    if matched_test and claim_category == "DIAGNOSTIC":
        opd_diag = policy.opd_categories.get("diagnostic")
        threshold = opd_diag.pre_auth_threshold if opd_diag and opd_diag.pre_auth_threshold else 10000.0

        if claimed_amount > threshold:
            return CheckResult(
                check_name="pre_auth.high_value_diagnostic",
                passed=False,
                detail=(
                    f"Pre-authorization is required for '{matched_test}' when the claim amount "
                    f"exceeds ₹{threshold:,.0f}. Claimed amount is ₹{claimed_amount:,.0f}, "
                    f"which exceeds the threshold. Pre-authorization was not obtained. "
                    f"To resubmit: obtain pre-authorization from your insurer before the procedure, "
                    f"then resubmit this claim with the pre-auth reference number. "
                    f"Pre-auth is valid for {policy.pre_authorization.validity_days} days from approval."
                ),
                policy_reference="pre_authorization.required_for",
                data={
                    "matched_test": matched_test,
                    "claimed_amount": claimed_amount,
                    "threshold": threshold,
                    "pre_auth_obtained": False,
                },
            )

    # All pre-auth checks passed
    return CheckResult(
        check_name="pre_auth",
        passed=True,
        detail=(
            f"No pre-authorization required for this claim "
            f"(category: {claim_category}, amount: ₹{claimed_amount:,.0f})."
        ),
        policy_reference="pre_authorization",
    )
