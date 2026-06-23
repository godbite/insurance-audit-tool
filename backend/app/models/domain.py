"""
Core domain models.

All business logic consumes these types — never raw dicts or untyped JSON.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

# ─── Document Types ────────────────────────────────────────────────────────────

DocumentType = Literal[
    "PRESCRIPTION",
    "HOSPITAL_BILL",
    "LAB_REPORT",
    "PHARMACY_BILL",
    "DENTAL_REPORT",
    "DISCHARGE_SUMMARY",
    "UNKNOWN",
]

ClaimCategory = Literal[
    "CONSULTATION",
    "DIAGNOSTIC",
    "PHARMACY",
    "DENTAL",
    "VISION",
    "ALTERNATIVE_MEDICINE",
]

ClaimStatus = Literal[
    "QUEUED",
    "CLASSIFYING",
    "VERIFYING",
    "EXTRACTING",
    "CHECKING_CONSISTENCY",
    "DECIDING",
    "COMPLETE",
    "FAILED",
]


# ─── Policy Terms (loaded from policy_terms.json) ──────────────────────────────

class DocumentRequirement(BaseModel):
    required: list[str]
    optional: list[str]


class OpdCategory(BaseModel):
    sub_limit: float
    copay_percent: float = 0.0
    network_discount_percent: float = 0.0
    requires_prescription: bool = False
    requires_pre_auth: bool = False
    pre_auth_threshold: Optional[float] = None
    high_value_tests_requiring_pre_auth: list[str] = Field(default_factory=list)
    covered: bool = True
    covered_procedures: list[str] = Field(default_factory=list)
    excluded_procedures: list[str] = Field(default_factory=list)
    covered_items: list[str] = Field(default_factory=list)
    excluded_items: list[str] = Field(default_factory=list)
    covered_systems: list[str] = Field(default_factory=list)
    branded_drug_copay_percent: float = 0.0
    generic_mandatory: bool = False
    max_sessions_per_year: Optional[int] = None
    requires_registered_practitioner: bool = False
    requires_dental_report: bool = False


class Coverage(BaseModel):
    sum_insured_per_employee: float
    annual_opd_limit: float
    per_claim_limit: float


class WaitingPeriods(BaseModel):
    initial_waiting_period_days: int
    pre_existing_conditions_days: int
    specific_conditions: dict[str, int] = Field(default_factory=dict)


class Exclusions(BaseModel):
    conditions: list[str] = Field(default_factory=list)
    dental_exclusions: list[str] = Field(default_factory=list)
    vision_exclusions: list[str] = Field(default_factory=list)


class FraudThresholds(BaseModel):
    same_day_claims_limit: int = 2
    monthly_claims_limit: int = 6
    high_value_claim_threshold: float = 25000.0
    auto_manual_review_above: float = 25000.0
    fraud_score_manual_review_threshold: float = 0.80


class SubmissionRules(BaseModel):
    deadline_days_from_treatment: int = 30
    minimum_claim_amount: float = 500.0
    currency: str = "INR"


class PreAuthorization(BaseModel):
    required_for: list[str] = Field(default_factory=list)
    validity_days: int = 30


class Member(BaseModel):
    member_id: str
    name: str
    date_of_birth: date
    gender: Literal["M", "F", "OTHER"]
    relationship: str
    join_date: Optional[date] = None
    dependents: list[str] = Field(default_factory=list)
    primary_member_id: Optional[str] = None


class PolicyTerms(BaseModel):
    policy_id: str
    policy_name: str
    insurer: str
    coverage: Coverage
    opd_categories: dict[str, OpdCategory]
    waiting_periods: WaitingPeriods
    exclusions: Exclusions
    pre_authorization: PreAuthorization
    network_hospitals: list[str]
    submission_rules: SubmissionRules
    document_requirements: dict[str, DocumentRequirement]
    fraud_thresholds: FraudThresholds
    members: list[Member]

    def get_member(self, member_id: str) -> Optional[Member]:
        for m in self.members:
            if m.member_id == member_id:
                return m
        return None

    def is_network_hospital(self, hospital_name: str) -> bool:
        """Case-insensitive partial match against network hospital list."""
        if not hospital_name:
            return False
        lower = hospital_name.lower()
        return any(nh.lower() in lower or lower in nh.lower() for nh in self.network_hospitals)

    def get_opd_category(self, category: str) -> Optional[OpdCategory]:
        return self.opd_categories.get(category.lower())


# ─── Document + Classification ────────────────────────────────────────────────

class ClassifiedDoc(BaseModel):
    file_id: str
    file_name: str
    predicted_type: DocumentType
    confidence: float  # 0.0–1.0
    quality_flag: Optional[Literal["GOOD", "DEGRADED", "UNREADABLE"]] = None
    storage_path: Optional[str] = None


# ─── Verification ─────────────────────────────────────────────────────────────

VerificationCode = Literal[
    "OK",
    "MISSING_REQUIRED_DOCUMENT",
    "DOCUMENT_UNREADABLE",
    "PATIENT_MISMATCH",
]


class VerificationResult(BaseModel):
    ok: bool
    code: VerificationCode
    message: str
    affected_file_ids: list[str] = Field(default_factory=list)


# ─── Claims History (for fraud signal lookups) ─────────────────────────────────

class ClaimHistoryEntry(BaseModel):
    claim_id: str
    member_id: str
    treatment_date: date
    claimed_amount: float
    status: str
    provider: Optional[str] = None


# ─── Component Result Wrapper ─────────────────────────────────────────────────
# Every risky call goes through this — no bare `except: pass` anywhere.

T = TypeVar("T")


class ComponentResult(BaseModel, Generic[T]):
    """
    Wraps any agent/component output with status + degradation info.

    Rules:
    - OK: value is set, no error, no confidence penalty.
    - DEGRADED: fallback value used, error logged, confidence docked.
    - FAILED: no fallback available, confidence docked maximally.
    """
    status: Literal["OK", "DEGRADED", "FAILED"]
    value: Optional[T] = None
    error: Optional[str] = None
    confidence_penalty: float = 0.0  # subtracted from claim-level confidence

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def ok(cls, value: T) -> "ComponentResult[T]":
        return cls(status="OK", value=value, confidence_penalty=0.0)

    @classmethod
    def degraded(cls, value: T, error: str, penalty: float = 0.15) -> "ComponentResult[T]":
        return cls(status="DEGRADED", value=value, error=error, confidence_penalty=penalty)

    @classmethod
    def failed(cls, error: str, penalty: float = 0.30) -> "ComponentResult[T]":
        return cls(status="FAILED", value=None, error=error, confidence_penalty=penalty)


# ─── Claim Input (what arrives at POST /claims) ───────────────────────────────

class ClaimInput(BaseModel):
    claim_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    member_id: str
    policy_id: str
    claim_category: ClaimCategory
    treatment_date: date
    claimed_amount: float
    hospital_name: Optional[str] = None
    pre_auth_obtained: bool = False
    idempotency_key: Optional[str] = None
    simulate_component_failure: bool = False
    # For integration tests — pre-seeded claims history
    claims_history: list[ClaimHistoryEntry] = Field(default_factory=list)
