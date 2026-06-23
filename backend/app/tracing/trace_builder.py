"""
TraceBuilder — incrementally builds ClaimTrace as pipeline stages complete.

This is not really an "agent" — it's a service that collects stage outputs.
Never raises — any internal error is logged silently.

The final ClaimTrace is:
- Persisted as JSON in Postgres (claim_traces table)
- Served at GET /claims/{id}/trace
- Used for the eval report (Deliverable 4)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.models.decision import CheckResult, ClaimTrace, DecisionResult, StageTrace

log = logging.getLogger(__name__)


class TraceBuilder:
    """
    Builds a ClaimTrace incrementally as pipeline stages complete.

    Usage:
        builder = TraceBuilder(claim_id="...", member_id="...", ...)
        builder.start_stage("DOC_CLASSIFICATION")
        # ... stage runs ...
        builder.complete_stage("DOC_CLASSIFICATION", status="PASSED", checks=[...])
        trace = builder.build()
    """

    def __init__(
        self,
        *,
        claim_id: str,
        member_id: str,
        claim_category: str,
        claimed_amount: float,
    ):
        self._claim_id = claim_id
        self._member_id = member_id
        self._claim_category = claim_category
        self._claimed_amount = claimed_amount
        self._submitted_at = datetime.now(timezone.utc)
        self._stages: list[StageTrace] = []
        self._active_stage: StageTrace | None = None
        self._final_decision: DecisionResult | None = None
        self._degraded = False
        self._degradation_notes: list[str] = []

    def start_stage(self, stage_name: str, inputs_summary: dict | None = None) -> None:
        """Mark a pipeline stage as started."""
        try:
            self._active_stage = StageTrace(
                stage_name=stage_name,
                started_at=datetime.now(timezone.utc),
                status="IN_PROGRESS",
                inputs_summary=inputs_summary or {},
                outputs_summary={},
                checks=[],
            )
        except Exception as e:
            log.error(f"TraceBuilder.start_stage failed: {e}")

    def complete_stage(
        self,
        stage_name: str,
        *,
        status: str,
        outputs_summary: dict | None = None,
        checks: list[CheckResult] | None = None,
        confidence_delta: float | None = None,
    ) -> None:
        """Mark the active stage as complete."""
        try:
            if self._active_stage and self._active_stage.stage_name == stage_name:
                self._active_stage.completed_at = datetime.now(timezone.utc)
                self._active_stage.status = status  # type: ignore[assignment]
                self._active_stage.outputs_summary = outputs_summary or {}
                self._active_stage.checks = checks or []
                self._active_stage.confidence_delta = confidence_delta
                self._stages.append(self._active_stage)
                self._active_stage = None
            else:
                # Stage completed without being started — create a complete record
                self._stages.append(StageTrace(
                    stage_name=stage_name,
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                    status=status,  # type: ignore[arg-type]
                    outputs_summary=outputs_summary or {},
                    checks=checks or [],
                    confidence_delta=confidence_delta,
                ))
        except Exception as e:
            log.error(f"TraceBuilder.complete_stage failed: {e}")

    def record_degradation(self, note: str) -> None:
        """Record a degradation event in the trace."""
        try:
            self._degraded = True
            self._degradation_notes.append(note)
        except Exception as e:
            log.error(f"TraceBuilder.record_degradation failed: {e}")

    def set_final_decision(self, decision: DecisionResult) -> None:
        """Set the final claim decision."""
        try:
            self._final_decision = decision
            if decision.degraded:
                self._degraded = True
                self._degradation_notes.extend(decision.degradation_notes)
        except Exception as e:
            log.error(f"TraceBuilder.set_final_decision failed: {e}")

    def build(self) -> ClaimTrace:
        """Build and return the complete ClaimTrace."""
        try:
            return ClaimTrace(
                claim_id=self._claim_id,
                member_id=self._member_id,
                claim_category=self._claim_category,
                claimed_amount=self._claimed_amount,
                submitted_at=self._submitted_at,
                completed_at=datetime.now(timezone.utc),
                stages=self._stages,
                final_decision=self._final_decision,
                degraded=self._degraded,
                degradation_notes=list(set(self._degradation_notes)),
            )
        except Exception as e:
            log.error(f"TraceBuilder.build failed: {e}")
            # Return a minimal trace rather than raising
            return ClaimTrace(
                claim_id=self._claim_id,
                member_id=self._member_id,
                claim_category=self._claim_category,
                claimed_amount=self._claimed_amount,
                submitted_at=self._submitted_at,
                degraded=True,
                degradation_notes=[f"Trace build error: {e}"],
            )
