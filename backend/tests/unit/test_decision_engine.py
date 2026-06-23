"""
Unit tests for the PolicyRulesEngine — arithmetic verification.

TC004: ₹1,500 consultation (no network), 10% co-pay → ₹1,350
TC010: ₹4,500 Apollo Hospitals, 20% network → ₹3,600, 10% co-pay → ₹3,240
TC006: Root canal ₹8,000 (covered) + teeth whitening ₹4,000 (excluded) → PARTIAL ₹8,000
TC008: ₹7,500 claimed vs ₹5,000 limit → REJECTED
TC005: EMP005 diabetes 2024-10-15 → REJECTED
TC007: MRI ₹15,000 no pre-auth → REJECTED
TC009: 4 same-day claims → MANUAL_REVIEW
TC011: degraded pipeline → confidence below clean-run baseline
TC012: bariatric/obesity → REJECTED (excluded)
"""
from __future__ import annotations

import pytest
from datetime import date

from app.agents.decision_engine import PolicyRulesEngine
from app.models.domain import ClaimHistoryEntry, ClaimInput, Member
from app.models.extraction_schemas import (
    HospitalBillExtract,
    LineItem,
    PrescriptionExtract,
)


def make_claim(
    member_id: str,
    category: str,
    treatment_date: str,
    claimed_amount: float,
    hospital_name: str | None = None,
    pre_auth_obtained: bool = False,
    simulate_failure: bool = False,
    claims_history: list[ClaimHistoryEntry] | None = None,
) -> ClaimInput:
    return ClaimInput(
        member_id=member_id,
        policy_id="PLUM_GHI_2024",
        claim_category=category,
        treatment_date=date.fromisoformat(treatment_date),
        claimed_amount=claimed_amount,
        hospital_name=hospital_name,
        pre_auth_obtained=pre_auth_obtained,
        simulate_component_failure=simulate_failure,
        claims_history=claims_history or [],
    )


class TestDecisionEngineArithmetic:

    def test_tc004_consultation_copay(self, policy):
        """TC004: ₹1,500 consultation, no network hospital, 10% co-pay → ₹1,350."""
        engine = PolicyRulesEngine()
        claim = make_claim("EMP001", "CONSULTATION", "2024-11-01", 1500.0)
        member = policy.get_member("EMP001")
        extracted = {
            "F007": PrescriptionExtract(
                patient_name="Rajesh Kumar",
                diagnosis="Viral Fever",
                date="2024-11-01",
            ),
            "F008": HospitalBillExtract(
                hospital_name="City Clinic, Bengaluru",  # Not a network hospital
                patient_name="Rajesh Kumar",
                total=1500.0,
                line_items=[
                    LineItem(description="Consultation Fee", amount=1000.0),
                    LineItem(description="CBC Test", amount=300.0),
                    LineItem(description="Dengue NS1 Test", amount=200.0),
                ],
            ),
        }

        result = engine.decide(
            claim_input=claim,
            member=member,
            policy=policy,
            extracted_data=extracted,
            claims_history=[],
            ytd_claims_amount=5000.0,
        )

        assert result.decision == "APPROVED"
        assert result.approved_amount == 1350.0, (
            f"Expected ₹1,350 (10% co-pay on ₹1,500). Got ₹{result.approved_amount}"
        )
        assert result.copay_deducted == pytest.approx(150.0, abs=0.01)
        assert result.network_discount_applied == 0.0
        assert result.confidence > 0.85

    def test_tc010_network_discount_then_copay(self, policy):
        """
        TC010: ₹4,500 Apollo Hospitals, 20% network discount FIRST, then 10% co-pay.
        ₹4,500 × 0.80 = ₹3,600 → ₹3,600 × 0.90 = ₹3,240.
        Wrong order (copay first) gives ₹3,330 — must produce ₹3,240 exactly.
        """
        engine = PolicyRulesEngine()
        claim = make_claim(
            "EMP010", "CONSULTATION", "2024-11-03", 4500.0,
            hospital_name="Apollo Hospitals"
        )
        member = policy.get_member("EMP010")
        extracted = {
            "F019": PrescriptionExtract(
                patient_name="Deepak Shah",
                diagnosis="Acute Bronchitis",
            ),
            "F020": HospitalBillExtract(
                hospital_name="Apollo Hospitals",
                patient_name="Deepak Shah",
                total=4500.0,
                line_items=[
                    LineItem(description="Consultation Fee", amount=1500.0),
                    LineItem(description="Medicines", amount=3000.0),
                ],
            ),
        }

        result = engine.decide(
            claim_input=claim,
            member=member,
            policy=policy,
            extracted_data=extracted,
            claims_history=[],
            ytd_claims_amount=8000.0,
        )

        assert result.decision == "APPROVED"
        assert result.approved_amount == 3240.0, (
            f"Expected ₹3,240 (network discount before co-pay). Got ₹{result.approved_amount}. "
            f"Wrong order (co-pay first) gives ₹3,330."
        )
        assert result.network_discount_applied == pytest.approx(900.0, abs=0.01)  # 20% of 4500
        assert result.copay_deducted == pytest.approx(360.0, abs=0.01)           # 10% of 3600
        assert result.is_network_hospital is True

    def test_tc008_per_claim_limit_exceeded(self, policy):
        """TC008: ₹7,500 claimed vs ₹5,000 per-claim limit → REJECTED with both amounts in message."""
        engine = PolicyRulesEngine()
        claim = make_claim("EMP003", "CONSULTATION", "2024-10-20", 7500.0)
        member = policy.get_member("EMP003")
        extracted = {
            "F015": PrescriptionExtract(diagnosis="Gastroenteritis"),
            "F016": HospitalBillExtract(
                total=7500.0,
                line_items=[
                    LineItem(description="Consultation Fee", amount=2000.0),
                    LineItem(description="Medicines", amount=5500.0),
                ],
            ),
        }

        result = engine.decide(
            claim_input=claim,
            member=member,
            policy=policy,
            extracted_data=extracted,
            claims_history=[],
        )

        assert result.decision == "REJECTED"
        assert "PER_CLAIM_EXCEEDED" in result.reasons

        # Message must state BOTH the limit (₹5,000) and claimed amount (₹7,500)
        limit_check = next(c for c in result.checks if "per_claim" in c.check_name)
        assert "7,500" in limit_check.detail or "7500" in limit_check.detail
        assert "5,000" in limit_check.detail or "5000" in limit_check.detail

    def test_tc006_dental_partial_approval(self, policy):
        """TC006: Root canal ₹8,000 (covered) + teeth whitening ₹4,000 (excluded) → PARTIAL ₹8,000."""
        engine = PolicyRulesEngine()
        claim = make_claim("EMP002", "DENTAL", "2024-10-15", 12000.0)
        member = policy.get_member("EMP002")
        extracted = {
            "F011": HospitalBillExtract(
                hospital_name="Smile Dental Clinic",
                patient_name="Priya Singh",
                total=12000.0,
                line_items=[
                    LineItem(description="Root Canal Treatment", amount=8000.0),
                    LineItem(description="Teeth Whitening", amount=4000.0),
                ],
            ),
        }

        result = engine.decide(
            claim_input=claim,
            member=member,
            policy=policy,
            extracted_data=extracted,
            claims_history=[],
        )

        assert result.decision == "PARTIAL"
        assert result.approved_amount == 8000.0, (
            f"Expected ₹8,000 (root canal only). Got ₹{result.approved_amount}"
        )

        # Itemized breakdown required
        assert len(result.line_item_breakdown) == 2
        root_canal = next(li for li in result.line_item_breakdown if "Root Canal" in li.description)
        whitening = next(li for li in result.line_item_breakdown if "Whitening" in li.description or "whitening" in li.description.lower())
        assert root_canal.status == "APPROVED"
        assert whitening.status == "EXCLUDED"
        assert whitening.reason is not None

    def test_tc005_waiting_period_rejection(self, policy):
        """TC005: EMP005 diabetes 2024-10-15 → REJECTED, message states 2024-11-30."""
        engine = PolicyRulesEngine()
        claim = make_claim("EMP005", "CONSULTATION", "2024-10-15", 3000.0)
        member = policy.get_member("EMP005")
        extracted = {
            "F009": PrescriptionExtract(
                patient_name="Vikram Joshi",
                diagnosis="Type 2 Diabetes Mellitus",
                diagnosis_raw="T2DM",
            ),
            "F010": HospitalBillExtract(
                patient_name="Vikram Joshi",
                total=3000.0,
            ),
        }

        result = engine.decide(
            claim_input=claim,
            member=member,
            policy=policy,
            extracted_data=extracted,
            claims_history=[],
        )

        assert result.decision == "REJECTED"
        assert "WAITING_PERIOD" in result.reasons

        # TC005: message must state the exact eligibility date
        wp_check = next(c for c in result.checks if "waiting_period" in c.check_name)
        assert "2024-11-30" in wp_check.detail

    def test_tc012_excluded_condition(self, policy):
        """TC012: Bariatric consultation + diet plan → REJECTED (excluded condition)."""
        engine = PolicyRulesEngine()
        claim = make_claim("EMP009", "CONSULTATION", "2024-10-18", 8000.0)
        member = policy.get_member("EMP009")
        extracted = {
            "F023": PrescriptionExtract(
                patient_name="Anita Desai",
                diagnosis="Morbid Obesity — BMI 37",
                diagnosis_raw="Morbid Obesity",
            ),
            "F024": HospitalBillExtract(
                total=8000.0,
                line_items=[
                    LineItem(description="Bariatric Consultation", amount=3000.0),
                    LineItem(description="Personalised Diet and Nutrition Program", amount=5000.0),
                ],
            ),
        }

        result = engine.decide(
            claim_input=claim,
            member=member,
            policy=policy,
            extracted_data=extracted,
            claims_history=[],
        )

        assert result.decision == "REJECTED"
        assert "EXCLUDED_CONDITION" in result.reasons
        assert result.confidence > 0.90

    def test_tc007_mri_no_pre_auth(self, policy):
        """TC007: MRI ₹15,000 without pre-auth → REJECTED, with resubmission instructions."""
        engine = PolicyRulesEngine()
        claim = make_claim(
            "EMP007", "DIAGNOSTIC", "2024-11-02", 15000.0,
            pre_auth_obtained=False
        )
        member = policy.get_member("EMP007")
        extracted = {
            "F012": PrescriptionExtract(
                diagnosis="Suspected Lumbar Disc Herniation",
                tests_ordered=["MRI Lumbar Spine"],
            ),
            "F013": HospitalBillExtract(
                total=15000.0,
                line_items=[LineItem(description="MRI Lumbar Spine", amount=15000.0)],
            ),
        }

        result = engine.decide(
            claim_input=claim,
            member=member,
            policy=policy,
            extracted_data=extracted,
            claims_history=[],
        )

        assert result.decision == "REJECTED"
        assert "PRE_AUTH_MISSING" in result.reasons

        pre_auth_check = next(c for c in result.checks if "pre_auth" in c.check_name)
        assert "resubmit" in pre_auth_check.detail.lower() or "pre-authorization" in pre_auth_check.detail.lower()

    def test_tc009_fraud_manual_review(self, policy):
        """TC009: 4th same-day claim for EMP008 → MANUAL_REVIEW with signals listed."""
        engine = PolicyRulesEngine()
        history = [
            ClaimHistoryEntry(
                claim_id="CLM_0081",
                member_id="EMP008",
                treatment_date=date(2024, 10, 30),
                claimed_amount=1200.0,
                status="APPROVED",
                provider="City Clinic A",
            ),
            ClaimHistoryEntry(
                claim_id="CLM_0082",
                member_id="EMP008",
                treatment_date=date(2024, 10, 30),
                claimed_amount=1800.0,
                status="APPROVED",
                provider="City Clinic B",
            ),
            ClaimHistoryEntry(
                claim_id="CLM_0083",
                member_id="EMP008",
                treatment_date=date(2024, 10, 30),
                claimed_amount=2100.0,
                status="APPROVED",
                provider="Wellness Center",
            ),
        ]
        claim = make_claim(
            "EMP008", "CONSULTATION", "2024-10-30", 4800.0,
            claims_history=history
        )
        member = policy.get_member("EMP008")
        extracted = {
            "F017": PrescriptionExtract(diagnosis="Migraine"),
            "F018": HospitalBillExtract(total=4800.0),
        }

        result = engine.decide(
            claim_input=claim,
            member=member,
            policy=policy,
            extracted_data=extracted,
            claims_history=history,
        )

        assert result.decision == "MANUAL_REVIEW"
        assert "FRAUD_SIGNAL" in result.reasons

        fraud_check = next(c for c in result.checks if "fraud" in c.check_name)
        assert fraud_check.passed is False
        # Message must list specific signals
        assert "3" in fraud_check.detail or "prior" in fraud_check.detail.lower()

    def test_tc011_degraded_pipeline_lower_confidence(self, policy):
        """TC011: Degraded pipeline → confidence below a clean run baseline."""
        engine = PolicyRulesEngine()
        claim = make_claim("EMP006", "ALTERNATIVE_MEDICINE", "2024-10-28", 4000.0)
        member = policy.get_member("EMP006")
        extracted = {
            "F021": PrescriptionExtract(
                diagnosis="Chronic Joint Pain",
            ),
            "F022": HospitalBillExtract(
                hospital_name="Ayur Wellness Centre",
                total=4000.0,
                line_items=[
                    LineItem(description="Panchakarma Therapy (5 sessions)", amount=3000.0),
                    LineItem(description="Consultation", amount=1000.0),
                ],
            ),
        }

        # Clean run
        clean_result = engine.decide(
            claim_input=claim,
            member=member,
            policy=policy,
            extracted_data=extracted,
            claims_history=[],
            degraded=False,
        )

        # Degraded run
        degraded_result = engine.decide(
            claim_input=claim,
            member=member,
            policy=policy,
            extracted_data=extracted,
            claims_history=[],
            degraded=True,
            degradation_notes=["CrossDocConsistencyAgent failed: simulated fault injection."],
        )

        assert degraded_result.degraded is True
        assert degraded_result.confidence < clean_result.confidence, (
            f"Degraded confidence ({degraded_result.confidence}) should be less than "
            f"clean confidence ({clean_result.confidence})."
        )
