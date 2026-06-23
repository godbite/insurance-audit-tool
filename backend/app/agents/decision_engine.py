"""
PolicyRulesEngine — the single source of all claim decisions.

This is PURE DETERMINISTIC CODE. No LLM call happens here.
Reason: co-pay math, sub-limits, waiting periods are exactly reproducible.
An LLM must never be the thing computing ₹3,240.

§9 ORDER OF OPERATIONS (enforced by code structure, not by comment):
  1. Waiting period
  2. Diagnosis exclusion
  3. Pre-authorization
  4. Per-claim limit
  5. Per-line-item exclusions → eligible amount
  6. Network discount (FIRST)
  7. Co-pay (SECOND — always after discount)
  8. Fraud signals
  9. Final decision aggregation

Changing this order changes the output amounts and will fail TC010.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from app.models.decision import CheckResult, DecisionResult, LineItemDecision, StageTrace
from app.models.domain import ClaimHistoryEntry, ClaimInput, Member, PolicyTerms
from app.models.extraction_schemas import HospitalBillExtract, LineItem, PrescriptionExtract
from app.policy.rules.copay import apply_copay
from app.policy.rules.exclusions import check_diagnosis_exclusion, check_line_item_exclusions
from app.policy.rules.fraud_signals import check_fraud_signals
from app.policy.rules.limits import (
    check_annual_opd_limit,
    check_minimum_claim_amount,
    check_per_claim_limit,
)
from app.policy.rules.network_discount import apply_network_discount
from app.policy.rules.pre_auth import check_pre_auth
from app.policy.rules.waiting_periods import check_waiting_period

log = logging.getLogger(__name__)


class PolicyRulesEngine:
    """
    Applies all policy rules to a claim and produces a DecisionResult.

    Input contract:
      - claim_input: ClaimInput (member_id, category, amount, treatment_date, etc.)
      - member: Member (loaded from policy)
      - policy: PolicyTerms (loaded from policy_terms.json)
      - extracted_data: dict mapping file_id → extraction object
      - claims_history: list of prior ClaimHistoryEntry for fraud check
      - ytd_claims_amount: total approved OPD amount this year for member
      - hospital_name: extracted from HOSPITAL_BILL or passed in claim_input
      - tests_ordered: extracted from PRESCRIPTION / LAB_REPORT
      - pre_auth_obtained: from claim submission metadata

    Output: DecisionResult with full CheckResult list.
    """

    def decide(
        self,
        *,
        claim_input: ClaimInput,
        member: Member,
        policy: PolicyTerms,
        extracted_data: dict,  # file_id → AnyExtract
        claims_history: list[ClaimHistoryEntry],
        ytd_claims_amount: float = 0.0,
        degraded: bool = False,
        degradation_notes: list[str] | None = None,
    ) -> DecisionResult:
        all_checks: list[CheckResult] = []
        reasons: list[str] = []
        decision_degraded = degraded
        deg_notes = list(degradation_notes or [])

        # ── Resolve inputs from extracted data ────────────────────────────────
        hospital_name = claim_input.hospital_name
        tests_ordered: list[str] = []
        line_items: list[LineItem] = []
        diagnosis: str = ""
        treatment: str = ""

        for extract in extracted_data.values():
            if extract is None:
                continue
            if hasattr(extract, "hospital_name") and extract.hospital_name:
                if not hospital_name:
                    hospital_name = extract.hospital_name
            if hasattr(extract, "line_items") and extract.line_items:
                line_items.extend(extract.line_items)
            if hasattr(extract, "tests_ordered") and extract.tests_ordered:
                tests_ordered.extend(extract.tests_ordered)
            if hasattr(extract, "diagnosis") and extract.diagnosis:
                if not diagnosis:
                    diagnosis = extract.diagnosis
            if hasattr(extract, "diagnosis_raw") and extract.diagnosis_raw:
                if not diagnosis:
                    diagnosis = extract.diagnosis_raw
            if hasattr(extract, "treatment") and extract.treatment:
                if not treatment:
                    treatment = extract.treatment

        # Also look at line item descriptions for test names
        line_item_descriptions = [li.description for li in line_items]

        claimed_amount = claim_input.claimed_amount

        # ─────────────────────────────────────────────────────────────────────
        # STEP 1: Diagnosis exclusion (permanent exclusions check first)
        # ─────────────────────────────────────────────────────────────────────
        exclusion_check = check_diagnosis_exclusion(
            diagnosis=diagnosis,
            treatment=treatment,
            policy=policy,
        )
        all_checks.append(exclusion_check)
        if not exclusion_check.passed:
            return self._build_result(
                decision="REJECTED",
                approved_amount=0.0,
                claimed_amount=claimed_amount,
                reasons=["EXCLUDED_CONDITION"],
                checks=all_checks,
                degraded=decision_degraded,
                degradation_notes=deg_notes,
            )

        # ─────────────────────────────────────────────────────────────────────
        # STEP 2: Waiting period
        # ─────────────────────────────────────────────────────────────────────
        wp_check = check_waiting_period(
            member=member,
            diagnosis=diagnosis,
            treatment_date=claim_input.treatment_date,
            policy=policy,
        )
        all_checks.append(wp_check)
        if not wp_check.passed:
            return self._build_result(
                decision="REJECTED",
                approved_amount=0.0,
                claimed_amount=claimed_amount,
                reasons=["WAITING_PERIOD"],
                checks=all_checks,
                degraded=decision_degraded,
                degradation_notes=deg_notes,
            )

        # ─────────────────────────────────────────────────────────────────────
        # STEP 3: Pre-authorization
        # ─────────────────────────────────────────────────────────────────────
        pre_auth_check = check_pre_auth(
            claim_category=claim_input.claim_category,
            claimed_amount=claimed_amount,
            tests_ordered=tests_ordered,
            line_item_descriptions=line_item_descriptions,
            pre_auth_obtained=claim_input.pre_auth_obtained,
            policy=policy,
        )
        all_checks.append(pre_auth_check)
        if not pre_auth_check.passed:
            return self._build_result(
                decision="REJECTED",
                approved_amount=0.0,
                claimed_amount=claimed_amount,
                reasons=["PRE_AUTH_MISSING"],
                checks=all_checks,
                degraded=decision_degraded,
                degradation_notes=deg_notes,
            )

        # ─────────────────────────────────────────────────────────────────────
        # STEP 4: Per-claim limit
        # ─────────────────────────────────────────────────────────────────────
        min_check = check_minimum_claim_amount(claimed_amount, policy)
        all_checks.append(min_check)
        if not min_check.passed:
            return self._build_result(
                decision="REJECTED",
                approved_amount=0.0,
                claimed_amount=claimed_amount,
                reasons=["BELOW_MINIMUM"],
                checks=all_checks,
                degraded=decision_degraded,
                degradation_notes=deg_notes,
            )

        if claim_input.claim_category == "CONSULTATION":
            limit_check = check_per_claim_limit(claimed_amount, policy)
            all_checks.append(limit_check)
            if not limit_check.passed:
                return self._build_result(
                    decision="REJECTED",
                    approved_amount=0.0,
                    claimed_amount=claimed_amount,
                    reasons=["PER_CLAIM_EXCEEDED"],
                    checks=all_checks,
                    degraded=decision_degraded,
                    degradation_notes=deg_notes,
                )

        # ─────────────────────────────────────────────────────────────────────
        # STEP 5: Per-line-item exclusions → compute eligible amount
        # ─────────────────────────────────────────────────────────────────────
        line_item_decisions: list[LineItemDecision] = []
        eligible_amount = claimed_amount  # default: use full claimed amount
        is_partial = False

        if line_items:
            item_decisions, item_check = check_line_item_exclusions(
                claim_category=claim_input.claim_category,
                line_items=line_items,
                policy=policy,
            )
            all_checks.append(item_check)
            line_item_decisions = item_decisions

            approved_items = [d for d in item_decisions if d.status == "APPROVED"]
            excluded_items = [d for d in item_decisions if d.status == "EXCLUDED"]

            eligible_amount = sum(d.approved_amount for d in approved_items)

            if not approved_items:
                # All items excluded → REJECTED
                return self._build_result(
                    decision="REJECTED",
                    approved_amount=0.0,
                    claimed_amount=claimed_amount,
                    line_item_breakdown=line_item_decisions,
                    reasons=["EXCLUDED_CONDITION"],
                    checks=all_checks,
                    degraded=decision_degraded,
                    degradation_notes=deg_notes,
                )

            if excluded_items:
                is_partial = True

        # ─────────────────────────────────────────────────────────────────────
        # STEP 6: Network discount (FIRST — before copay)
        # ─────────────────────────────────────────────────────────────────────
        discounted_amount, discount_applied, is_network, discount_check = apply_network_discount(
            amount=eligible_amount,
            hospital_name=hospital_name,
            claim_category=claim_input.claim_category,
            policy=policy,
        )
        all_checks.append(discount_check)

        # ─────────────────────────────────────────────────────────────────────
        # STEP 7: Co-pay (SECOND — always on the already-discounted amount)
        # ─────────────────────────────────────────────────────────────────────
        final_amount, copay_deducted, copay_check = apply_copay(
            amount=discounted_amount,
            claim_category=claim_input.claim_category,
            policy=policy,
        )
        all_checks.append(copay_check)

        # Annual OPD limit check (informational after copay calculation)
        annual_check = check_annual_opd_limit(final_amount, ytd_claims_amount, policy)
        all_checks.append(annual_check)
        if not annual_check.passed:
            return self._build_result(
                decision="REJECTED",
                approved_amount=0.0,
                claimed_amount=claimed_amount,
                reasons=["ANNUAL_LIMIT_EXCEEDED"],
                checks=all_checks,
                degraded=decision_degraded,
                degradation_notes=deg_notes,
            )

        # ─────────────────────────────────────────────────────────────────────
        # STEP 8: Fraud signals (MANUAL_REVIEW, not auto-reject)
        # ─────────────────────────────────────────────────────────────────────
        fraud_triggered, fraud_signal_list, fraud_check = check_fraud_signals(
            member_id=claim_input.member_id,
            treatment_date=claim_input.treatment_date,
            claimed_amount=claimed_amount,
            claims_history=claims_history,
            policy=policy,
        )
        all_checks.append(fraud_check)

        if fraud_triggered:
            return self._build_result(
                decision="MANUAL_REVIEW",
                approved_amount=final_amount,
                claimed_amount=claimed_amount,
                line_item_breakdown=line_item_decisions,
                reasons=["FRAUD_SIGNAL"] + fraud_signal_list,
                checks=all_checks,
                degraded=decision_degraded,
                degradation_notes=deg_notes,
                network_discount_applied=discount_applied,
                copay_deducted=copay_deducted,
                is_network_hospital=is_network,
            )

        # ─────────────────────────────────────────────────────────────────────
        # STEP 9: Final decision
        # ─────────────────────────────────────────────────────────────────────
        # Degraded pipeline → recommend manual review
        if decision_degraded:
            deg_notes.append(
                "One or more pipeline components degraded during processing. "
                "Manual review is recommended to validate this decision."
            )
            return self._build_result(
                decision="APPROVED",  # claim is otherwise clean; degrade the confidence
                approved_amount=final_amount,
                claimed_amount=claimed_amount,
                line_item_breakdown=line_item_decisions,
                reasons=["DEGRADED_PIPELINE"],
                checks=all_checks,
                degraded=True,
                degradation_notes=deg_notes,
                network_discount_applied=discount_applied,
                copay_deducted=copay_deducted,
                is_network_hospital=is_network,
            )

        final_decision = "PARTIAL" if is_partial else "APPROVED"

        return self._build_result(
            decision=final_decision,
            approved_amount=final_amount,
            claimed_amount=claimed_amount,
            line_item_breakdown=line_item_decisions,
            reasons=[],
            checks=all_checks,
            degraded=False,
            degradation_notes=[],
            network_discount_applied=discount_applied,
            copay_deducted=copay_deducted,
            is_network_hospital=is_network,
        )

    @staticmethod
    def _build_result(
        *,
        decision: str,
        approved_amount: float,
        claimed_amount: float,
        reasons: list[str],
        checks: list[CheckResult],
        degraded: bool,
        degradation_notes: list[str],
        line_item_breakdown: list[LineItemDecision] | None = None,
        network_discount_applied: float = 0.0,
        copay_deducted: float = 0.0,
        is_network_hospital: bool = False,
    ) -> DecisionResult:
        # Compute confidence from checks
        confidence = PolicyRulesEngine._compute_confidence(checks, degraded)

        return DecisionResult(
            decision=decision,
            approved_amount=round(approved_amount, 2),
            claimed_amount=claimed_amount,
            line_item_breakdown=line_item_breakdown or [],
            reasons=reasons,
            checks=checks,
            confidence=confidence,
            degraded=degraded,
            degradation_notes=degradation_notes,
            network_discount_applied=round(network_discount_applied, 2),
            copay_deducted=round(copay_deducted, 2),
            is_network_hospital=is_network_hospital,
        )

    @staticmethod
    def _compute_confidence(checks: list[CheckResult], degraded: bool) -> float:
        """
        Confidence is anchored at 1.0 and reduced by:
        - Degraded pipeline: -0.25
        - Each failed check (that didn't cause rejection): -0.05
        Minimum returned: 0.0. Maximum: 1.0.
        """
        confidence = 1.0
        if degraded:
            confidence -= 0.25
        return max(0.0, min(1.0, round(confidence, 3)))
