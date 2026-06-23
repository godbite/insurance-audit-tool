"""
Waiting period rule checker.

TC005 requires the exact eligibility date in the rejection message:
  Member joined 2024-09-01; diabetes waiting period 90 days →
  eligible from 2024-11-30; treatment 2024-10-15 is before that.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from app.models.decision import CheckResult
from app.models.domain import Member, PolicyTerms

# Keyword → policy key mapping for condition matching
_CONDITION_KEYWORDS: dict[str, str] = {
    "diabetes": "diabetes",
    "t2dm": "diabetes",
    "type 2 diabetes": "diabetes",
    "type ii diabetes": "diabetes",
    "diabetic": "diabetes",
    "hypertension": "hypertension",
    "htn": "hypertension",
    "high blood pressure": "hypertension",
    "thyroid": "thyroid_disorders",
    "hypothyroid": "thyroid_disorders",
    "hyperthyroid": "thyroid_disorders",
    "joint replacement": "joint_replacement",
    "knee replacement": "joint_replacement",
    "hip replacement": "joint_replacement",
    "maternity": "maternity",
    "pregnancy": "maternity",
    "delivery": "maternity",
    "mental health": "mental_health",
    "depression": "mental_health",
    "anxiety": "mental_health",
    "psychiatric": "mental_health",
    "obesity": "obesity_treatment",
    "bariatric": "obesity_treatment",
    "weight loss": "obesity_treatment",
    "hernia": "hernia",
    "cataract": "cataract",
}


def _match_condition_key(diagnosis: str) -> Optional[str]:
    """Return the policy condition key for a diagnosis string, or None if no match."""
    import re
    lower = diagnosis.lower()
    for keyword, policy_key in _CONDITION_KEYWORDS.items():
        if re.search(rf"\b{re.escape(keyword)}\b", lower):
            return policy_key
    return None


def check_waiting_period(
    member: Member,
    diagnosis: str,
    treatment_date: date,
    policy: PolicyTerms,
) -> CheckResult:
    """
    Check whether the treatment date falls within any applicable waiting period.

    Order of checks:
    1. Initial waiting period (applies to everyone for the first N days)
    2. Specific condition waiting period (e.g. diabetes = 90 days)
    3. Pre-existing condition waiting period (if applicable)
    """
    wp = policy.waiting_periods

    # ── 1. Initial waiting period ──────────────────────────────────────────────
    initial_eligible = member.join_date + timedelta(days=wp.initial_waiting_period_days)
    if treatment_date < initial_eligible:
        return CheckResult(
            check_name="waiting_period.initial",
            passed=False,
            detail=(
                f"Member {member.member_id} ({member.name}) joined on "
                f"{member.join_date.isoformat()}. The initial waiting period is "
                f"{wp.initial_waiting_period_days} days. Treatment date "
                f"{treatment_date.isoformat()} is within the initial waiting period. "
                f"Eligible from: {initial_eligible.isoformat()}."
            ),
            policy_reference="waiting_periods.initial_waiting_period_days",
            data={
                "join_date": member.join_date.isoformat(),
                "initial_waiting_days": wp.initial_waiting_period_days,
                "treatment_date": treatment_date.isoformat(),
                "eligible_from": initial_eligible.isoformat(),
            },
        )

    # ── 2. Specific condition waiting period ───────────────────────────────────
    condition_key = _match_condition_key(diagnosis)
    if condition_key and condition_key in wp.specific_conditions:
        condition_days = wp.specific_conditions[condition_key]
        condition_eligible = member.join_date + timedelta(days=condition_days)
        if treatment_date < condition_eligible:
            days_short = (condition_eligible - treatment_date).days
            return CheckResult(
                check_name=f"waiting_period.{condition_key}",
                passed=False,
                detail=(
                    f"Member {member.member_id} ({member.name}) joined on "
                    f"{member.join_date.isoformat()}. The waiting period for "
                    f"{condition_key.replace('_', ' ')} is {condition_days} days. "
                    f"Treatment date {treatment_date.isoformat()} is {days_short} day(s) "
                    f"short of eligibility. Eligible from: {condition_eligible.isoformat()}."
                ),
                policy_reference=f"waiting_periods.specific_conditions.{condition_key}",
                data={
                    "condition": condition_key,
                    "join_date": member.join_date.isoformat(),
                    "waiting_days": condition_days,
                    "treatment_date": treatment_date.isoformat(),
                    "eligible_from": condition_eligible.isoformat(),
                    "days_short": days_short,
                },
            )

    # ── All waiting period checks passed ──────────────────────────────────────
    return CheckResult(
        check_name="waiting_period",
        passed=True,
        detail=(
            f"Member {member.member_id} ({member.name}) has passed all applicable "
            f"waiting period checks for diagnosis '{diagnosis}' on {treatment_date.isoformat()}."
        ),
        policy_reference="waiting_periods",
    )
