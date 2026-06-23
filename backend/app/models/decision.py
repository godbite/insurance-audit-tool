"""
Decision and tracing models.

ClaimTrace is the primary observability artifact — graded directly.
Every CheckResult.detail must read like a human ops reviewer wrote it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ─── Per-check result (lowest-level unit of the trace) ────────────────────────

class CheckResult(BaseModel):
    """
    One atomic check performed by the PolicyRulesEngine or any agent.

    The `detail` field is the key observability artifact — it must contain
    enough information to reconstruct the decision without reading code.
    Examples:
      - "Member joined 2024-09-01; diabetes waiting period is 90 days;
         treatment date 2024-10-15 is 14 days short of eligibility
         (eligible from 2024-11-30)."
      - "Hospital Apollo Hospitals is in network; 20% discount applied:
         ₹4,500 × 0.80 = ₹3,600."
      - "Co-pay 10% applied on discounted amount: ₹3,600 × 0.90 = ₹3,240."
    """
    check_name: str           # e.g. "waiting_period.diabetes"
    passed: bool
    detail: str               # human-readable explanation
    policy_reference: str     # e.g. "waiting_periods.specific_conditions.diabetes"
    data: dict[str, Any] = Field(default_factory=dict)  # machine-readable supplement


# ─── Line-item breakdown (for PARTIAL decisions) ───────────────────────────────

class LineItemDecision(BaseModel):
    description: str
    claimed_amount: float
    approved_amount: float
    status: Literal["APPROVED", "REJECTED", "EXCLUDED"]
    reason: str = ""


class LLMDecisionExtract(BaseModel):
    decision: Literal["APPROVED", "PARTIAL", "REJECTED", "MANUAL_REVIEW"]
    approved_amount: float
    line_item_breakdown: list[LineItemDecision]
    reasons: list[str]


# ─── Decision result ──────────────────────────────────────────────────────────

class DecisionResult(BaseModel):
    """
    The final claim decision output.

    approved_amount is always present (0 for REJECTED).
    line_item_breakdown is required when decision=PARTIAL.
    confidence reflects extraction quality + consistency + degradation.
    """
    decision: Literal["APPROVED", "PARTIAL", "REJECTED", "MANUAL_REVIEW"]
    approved_amount: float = 0.0
    claimed_amount: float = 0.0
    line_item_breakdown: list[LineItemDecision] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)   # e.g. ["WAITING_PERIOD", "PER_CLAIM_EXCEEDED"]
    checks: list[CheckResult] = Field(default_factory=list)
    confidence: float = 1.0   # 0.0–1.0
    degraded: bool = False
    degradation_notes: list[str] = Field(default_factory=list)
    # Financial breakdown (for APPROVED/PARTIAL)
    network_discount_applied: float = 0.0
    copay_deducted: float = 0.0
    is_network_hospital: bool = False


# ─── Per-stage trace ──────────────────────────────────────────────────────────

class StageTrace(BaseModel):
    """
    One stage in the pipeline (DOC_CLASSIFICATION, DOC_VERIFICATION, etc.).
    """
    stage_name: str   # "DOC_CLASSIFICATION" | "DOC_VERIFICATION" | "EXTRACTION" | etc.
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: Literal["PASSED", "FAILED", "DEGRADED", "SKIPPED", "IN_PROGRESS"]
    inputs_summary: dict[str, Any] = Field(default_factory=dict)   # small summary, not full payloads
    outputs_summary: dict[str, Any] = Field(default_factory=dict)
    checks: list[CheckResult] = Field(default_factory=list)
    confidence_delta: Optional[float] = None   # how this stage moved overall confidence


# ─── Full claim trace ─────────────────────────────────────────────────────────

class ClaimTrace(BaseModel):
    """
    The complete audit trail for a claim.

    This is persisted as JSON in Postgres and served at GET /claims/{id}/trace.
    The eval report is produced by dumping this object per test case.
    A reviewer must be able to reconstruct the exact decision from this
    object alone, without reading source code.
    """
    claim_id: str
    member_id: str
    claim_category: str
    claimed_amount: float
    submitted_at: datetime
    completed_at: Optional[datetime] = None
    stages: list[StageTrace] = Field(default_factory=list)
    final_decision: Optional[DecisionResult] = None
    degraded: bool = False
    degradation_notes: list[str] = Field(default_factory=list)
    # Pipeline version for prompt regression tracking
    pipeline_version: str = "1.0.0"
