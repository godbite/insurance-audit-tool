"""
Unit tests for waiting period rules.

TC005 exact arithmetic is tested here:
  EMP005 Vikram Joshi, joined 2024-09-01
  Diabetes waiting period = 90 days
  Eligible from: 2024-11-30 (NOT 2024-11-29)
  Treatment date: 2024-10-15 → REJECTED, message states 2024-11-30
"""
from __future__ import annotations

import pytest
from datetime import date

from app.models.domain import Member
from app.policy.rules.waiting_periods import check_waiting_period


@pytest.fixture
def emp005(policy) -> Member:
    m = policy.get_member("EMP005")
    assert m is not None, "EMP005 must be in policy members"
    return m


@pytest.fixture
def emp001(policy) -> Member:
    m = policy.get_member("EMP001")
    assert m is not None
    return m


class TestWaitingPeriods:

    def test_tc005_diabetes_waiting_period_rejected(self, emp005, policy):
        """TC005: EMP005, diabetes treatment 2024-10-15 → within 90-day waiting period → REJECTED."""
        result = check_waiting_period(
            member=emp005,
            diagnosis="Type 2 Diabetes Mellitus",
            treatment_date=date(2024, 10, 15),
            policy=policy,
        )
        assert result.passed is False
        assert result.check_name == "waiting_period.diabetes"
        # TC005 requires the exact eligibility date in the message
        assert "2024-11-30" in result.detail, (
            f"Expected '2024-11-30' in rejection detail. Got: {result.detail}"
        )

    def test_tc005_eligible_date_is_exactly_90_days(self, emp005, policy):
        """Join 2024-09-01 + 90 days = 2024-11-30 (not 2024-11-29)."""
        result = check_waiting_period(
            member=emp005,
            diagnosis="diabetes",
            treatment_date=date(2024, 10, 15),
            policy=policy,
        )
        assert "2024-11-30" in result.detail
        assert result.data.get("eligible_from") == "2024-11-30"

    def test_tc005_after_waiting_period_passes(self, emp005, policy):
        """Treatment on eligibility date (2024-11-30) should PASS."""
        result = check_waiting_period(
            member=emp005,
            diagnosis="Type 2 Diabetes Mellitus",
            treatment_date=date(2024, 11, 30),
            policy=policy,
        )
        assert result.passed is True

    def test_tc005_one_day_before_eligibility_fails(self, emp005, policy):
        """Treatment 2024-11-29 (one day before 2024-11-30) → REJECTED."""
        result = check_waiting_period(
            member=emp005,
            diagnosis="diabetes",
            treatment_date=date(2024, 11, 29),
            policy=policy,
        )
        assert result.passed is False
        assert "2024-11-30" in result.detail

    def test_initial_waiting_period(self, emp005, policy):
        """Very early treatment (before initial 30-day period) → REJECTED."""
        result = check_waiting_period(
            member=emp005,
            diagnosis="viral fever",
            treatment_date=date(2024, 9, 10),  # only 9 days after join
            policy=policy,
        )
        assert result.passed is False
        assert "initial" in result.check_name

    def test_no_waiting_period_for_non_matched_diagnosis(self, emp001, policy):
        """Non-waiting-period diagnosis (viral fever) for tenured member → PASSES."""
        result = check_waiting_period(
            member=emp001,
            diagnosis="Viral Fever",
            treatment_date=date(2024, 11, 1),
            policy=policy,
        )
        assert result.passed is True

    def test_htn_shorthand_matched(self, emp005, policy):
        """HTN shorthand should match hypertension waiting period."""
        result = check_waiting_period(
            member=emp005,
            diagnosis="HTN",
            treatment_date=date(2024, 10, 15),
            policy=policy,
        )
        assert result.passed is False
        assert "hypertension" in result.check_name

    def test_detail_contains_member_info(self, emp005, policy):
        """Detail message should reference the member ID and name."""
        result = check_waiting_period(
            member=emp005,
            diagnosis="diabetes",
            treatment_date=date(2024, 10, 15),
            policy=policy,
        )
        assert "EMP005" in result.detail or "Vikram Joshi" in result.detail
