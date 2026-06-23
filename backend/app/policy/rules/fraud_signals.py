"""
Fraud signal detection.

TC009: Member EMP008 — 4th same-day claim → MANUAL_REVIEW.
Message must list the specific signals that triggered the flag.

This is deterministic code — no LLM involved.
Signals → MANUAL_REVIEW, not auto-reject (per assignment spec).
"""
from __future__ import annotations

from datetime import date

from app.models.decision import CheckResult
from app.models.domain import ClaimHistoryEntry, PolicyTerms


def check_fraud_signals(
    member_id: str,
    treatment_date: date,
    claimed_amount: float,
    claims_history: list[ClaimHistoryEntry],
    policy: PolicyTerms,
) -> tuple[bool, list[str], CheckResult]:
    """
    Evaluate fraud signals for a claim submission.

    Returns:
        triggered       — True if any signal crossed a threshold
        signal_list     — human-readable signal descriptions
        check_result    — CheckResult for the trace
    """
    thresholds = policy.fraud_thresholds
    signals: list[str] = []

    # ── Same-day claims count ──────────────────────────────────────────────────
    same_day = [
        h for h in claims_history
        if h.treatment_date == treatment_date and h.member_id == member_id
    ]
    same_day_count = len(same_day)
    same_day_total = sum(h.claimed_amount for h in same_day)

    if same_day_count >= thresholds.same_day_claims_limit:
        signals.append(
            f"{same_day_count} prior claim(s) already submitted today "
            f"(treatment date {treatment_date.isoformat()}) totalling ₹{same_day_total:,.0f}; "
            f"this would be claim #{same_day_count + 1} today "
            f"(limit: {thresholds.same_day_claims_limit})."
        )

    # ── Monthly claims count ───────────────────────────────────────────────────
    monthly = [
        h for h in claims_history
        if h.treatment_date.year == treatment_date.year
        and h.treatment_date.month == treatment_date.month
        and h.member_id == member_id
    ]
    monthly_count = len(monthly)
    if monthly_count >= thresholds.monthly_claims_limit:
        signals.append(
            f"{monthly_count} claim(s) submitted this month "
            f"(limit: {thresholds.monthly_claims_limit})."
        )

    # ── High-value claim ──────────────────────────────────────────────────────
    if claimed_amount >= thresholds.high_value_claim_threshold:
        signals.append(
            f"Claimed amount ₹{claimed_amount:,.0f} meets or exceeds the high-value "
            f"threshold of ₹{thresholds.high_value_claim_threshold:,.0f}, "
            f"requiring manual review."
        )

    triggered = len(signals) > 0

    if triggered:
        return (
            True,
            signals,
            CheckResult(
                check_name="fraud_signals",
                passed=False,  # triggers MANUAL_REVIEW path
                detail=(
                    f"Fraud signals detected for member {member_id}: "
                    + " | ".join(signals)
                    + " Routing to manual review rather than auto-rejecting."
                ),
                policy_reference="fraud_thresholds",
                data={
                    "member_id": member_id,
                    "treatment_date": treatment_date.isoformat(),
                    "claimed_amount": claimed_amount,
                    "same_day_count": same_day_count,
                    "monthly_count": monthly_count,
                    "signals": signals,
                },
            ),
        )

    return (
        False,
        [],
        CheckResult(
            check_name="fraud_signals",
            passed=True,
            detail=(
                f"No fraud signals detected for member {member_id}. "
                f"Same-day claims: {same_day_count}/{thresholds.same_day_claims_limit}. "
                f"Monthly claims: {monthly_count}/{thresholds.monthly_claims_limit}."
            ),
            policy_reference="fraud_thresholds",
        ),
    )
