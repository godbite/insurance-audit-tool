"""
LLMDecisionEngine — replacing the deterministic Python PolicyRulesEngine with LLM decisioning.
"""
from __future__ import annotations

import json
import logging

from app.models.decision import DecisionResult, CheckResult, LineItemDecision, LLMDecisionExtract
from app.models.domain import ClaimHistoryEntry, ClaimInput, Member, PolicyTerms
from app.providers.router import ProviderRouter
from app.tracing.component_runner import run_component

log = logging.getLogger(__name__)

class LLMDecisionEngine:
    """
    Evaluates a claim by passing all extracted context to an LLM via the ProviderRouter.
    """

    def __init__(self, router: ProviderRouter):
        self._router = router

    async def decide_async(
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
        
        async def _do_decide() -> DecisionResult:
            # 1. Build context JSON
            extracts_serializable = {}
            for fid, ext in extracted_data.items():
                if ext is not None:
                    extracts_serializable[fid] = ext.model_dump()

            context = {
                "claim_input": claim_input.model_dump(),
                "member": member.model_dump(),
                "policy": policy.model_dump(),
                "extracted_data": extracts_serializable,
                "claims_history": [ch.model_dump() for ch in claims_history],
                "ytd_claims_amount": ytd_claims_amount,
                "previous_pipeline_degraded": degraded,
            }
            
            context_json = json.dumps(context, default=str)

            # 2. Call LLM
            result = await self._router.decide_with_failover(context_json=context_json)
            
            if result.parsed is None or not isinstance(result.parsed, LLMDecisionExtract):
                raise RuntimeError(f"LLM decision failed or returned invalid schema: {result.error}")
                
            llm_decision: LLMDecisionExtract = result.parsed
            
            # 3. Build CheckResults from LLM reasons (since LLM doesn't output precise checks)
            checks = []
            for reason in llm_decision.reasons:
                checks.append(CheckResult(
                    check_name="llm_evaluation",
                    passed=False if llm_decision.decision == "REJECTED" else True,
                    detail=reason,
                    policy_reference="llm.dynamic"
                ))
            if not checks:
                checks.append(CheckResult(
                    check_name="llm_evaluation",
                    passed=True,
                    detail="LLM approved claim without specific notes.",
                    policy_reference="llm.dynamic"
                ))

            deg_notes = list(degradation_notes or [])
            is_degraded = degraded
            
            if result.confidence < 0.5:
                is_degraded = True
                deg_notes.append("LLM Decision Engine returned low confidence.")

            # 4. Map to DecisionResult
            return DecisionResult(
                decision=llm_decision.decision,
                approved_amount=llm_decision.approved_amount,
                claimed_amount=claim_input.claimed_amount,
                line_item_breakdown=llm_decision.line_item_breakdown,
                reasons=llm_decision.reasons,
                checks=checks,
                confidence=result.confidence,
                degraded=is_degraded,
                degradation_notes=deg_notes,
            )

        return await run_component(
            _do_decide,
            component_name=f"llm_decision.{claim_input.claim_id}",
            fallback=DecisionResult(
                decision="MANUAL_REVIEW",
                approved_amount=0.0,
                claimed_amount=claim_input.claimed_amount,
                reasons=["LLM_DECISION_FAILED"],
                checks=[],
                confidence=0.0,
                degraded=True,
                degradation_notes=["LLM decision engine failed, falling back to manual review."],
            ),
            penalty_on_failure=0.5,
        )
