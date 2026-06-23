"""
Celery claim processing pipeline.

Explicit state machine (not LangGraph/CrewAI) for explainability:
  submit_claim
    └─▶ DOC_CLASSIFICATION  (parallel per file, extraction queue)
          └─▶ DOC_VERIFICATION (decisioning queue) ── FAIL ──▶ stop, return error
                └─▶ EXTRACTION (parallel per file, extraction queue)
                      └─▶ CONSISTENCY_CHECK (decisioning queue)
                            └─▶ DECISIONING (decisioning queue)
                                  └─▶ COMPLETE (persist, notify WS)

Each stage publishes events to Redis Pub/Sub channel claim:{claim_id}
so the FastAPI WebSocket handler can forward them live.

The fault injection path for TC011 lives in the consistency check stage.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone
import os
from typing import Any, Optional

import redis
from celery import shared_task
from langfuse import observe

from app.core.celery_app import celery_app
from app.core.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


# ─── Redis Pub/Sub helper ─────────────────────────────────────────────────────

def _get_redis() -> redis.Redis:
    kwargs = {"ssl_cert_reqs": "none"} if settings.redis_url.startswith("rediss://") else {}
    return redis.from_url(settings.redis_url, **kwargs)


def _publish_event(claim_id: str, event: dict) -> None:
    """Publish a pipeline stage event to the WebSocket relay channel."""
    try:
        r = _get_redis()
        r.publish(f"claim:{claim_id}", json.dumps(event, default=str))
    except Exception as e:
        log.warning(f"Failed to publish event for claim {claim_id}: {e}")


# ─── Main pipeline task ───────────────────────────────────────────────────────

@shared_task(name="app.tasks.pipeline.run_claim_pipeline", bind=True, max_retries=0)
@observe()
def run_claim_pipeline(self, claim_id: str, claim_data: dict) -> dict:
    """
    Main Celery task that runs the full claims processing pipeline.
    """
    import os
    from app.core.config import get_settings
    
    settings = get_settings()
    # Force set environment variables for the Langfuse client used by @observe
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
    os.environ["LANGFUSE_HOST"] = settings.langfuse_host

    from app.agents.classifier_agent import DocumentClassifierAgent
    from app.agents.consistency_agent import CrossDocConsistencyAgent
    from app.agents.decision_engine import PolicyRulesEngine
    from app.agents.extraction_agent import ExtractionAgent
    from app.agents.verification_agent import DocumentVerificationAgent
    from app.models.domain import ClaimHistoryEntry, ClaimInput, ClassifiedDoc
    from app.policy.loader import get_policy
    from app.providers.router import ProviderRouter
    from app.tracing.trace_builder import TraceBuilder

    # Run async pipeline in sync Celery task
    return asyncio.get_event_loop().run_until_complete(
        _run_pipeline_async(claim_id, claim_data)
    )


async def _run_pipeline_async(claim_id: str, claim_data: dict) -> dict:
    """Async implementation of the claim pipeline."""
    from app.agents.classifier_agent import DocumentClassifierAgent
    from app.agents.consistency_agent import CrossDocConsistencyAgent
    from app.agents.decision_engine import PolicyRulesEngine
    from app.agents.extraction_agent import ExtractionAgent
    from app.agents.verification_agent import DocumentVerificationAgent
    from app.models.domain import ClaimHistoryEntry, ClaimInput, ClassifiedDoc
    from app.policy.loader import get_policy
    from app.providers.router import ProviderRouter
    from app.tracing.trace_builder import TraceBuilder

    policy = get_policy()
    router = ProviderRouter.from_settings(settings)

    claim_input = _parse_claim_input(claim_data)
    documents_meta = claim_data.get("documents", [])

    # Initialise trace builder
    trace_builder = TraceBuilder(
        claim_id=claim_id,
        member_id=claim_input.member_id,
        claim_category=claim_input.claim_category,
        claimed_amount=claim_input.claimed_amount,
    )

    overall_degraded = False
    confidence_penalties: list[float] = []

    try:
        # ─── STAGE 1: DOC_CLASSIFICATION ─────────────────────────────────────
        trace_builder.start_stage("DOC_CLASSIFICATION")
        _publish_event(claim_id, {"stage": "DOC_CLASSIFICATION", "status": "IN_PROGRESS", "ts": _now()})

        classifier = DocumentClassifierAgent(router=router, settings=settings)
        classified_docs: list[ClassifiedDoc] = []

        classify_tasks = []
        for doc in documents_meta:
            doc_bytes = b""
            if "storage_path" in doc and os.path.exists(doc["storage_path"]):
                with open(doc["storage_path"], "rb") as f:
                    doc_bytes = f.read()
                    
            classify_tasks.append(
                classifier.classify_document(
                    file_id=doc["file_id"],
                    file_name=doc.get("file_name", doc["file_id"]),
                    document_bytes=doc_bytes,
                    test_mode_type=doc.get("actual_type"),
                    test_mode_quality=doc.get("quality"),
                )
            )
            
        classify_results = await asyncio.gather(*classify_tasks)

        for cr in classify_results:
            if cr.status == "DEGRADED":
                overall_degraded = True
                confidence_penalties.append(cr.confidence_penalty)
                trace_builder.record_degradation(cr.error or "Classifier degraded")
            if cr.value:
                classified_docs.append(cr.value)

        trace_builder.complete_stage(
            "DOC_CLASSIFICATION",
            status="PASSED" if classified_docs else "FAILED",
            outputs_summary={"classified_count": len(classified_docs),
                             "types": [d.predicted_type for d in classified_docs]},
        )
        _publish_event(claim_id, {
            "stage": "DOC_CLASSIFICATION", "status": "COMPLETE",
            "detail": f"{len(classified_docs)} document(s) classified", "ts": _now()
        })

        # ─── STAGE 2: DOC_VERIFICATION ────────────────────────────────────────
        trace_builder.start_stage("DOC_VERIFICATION")
        _publish_event(claim_id, {"stage": "DOC_VERIFICATION", "status": "IN_PROGRESS", "ts": _now()})

        verifier = DocumentVerificationAgent()
        verification_result = verifier.verify(
            claim_category=claim_input.claim_category,
            classified_docs=classified_docs,
            policy=policy,
        )

        if not verification_result.ok:
            trace_builder.complete_stage(
                "DOC_VERIFICATION",
                status="FAILED",
                outputs_summary={
                    "code": verification_result.code,
                    "message": verification_result.message,
                },
            )
            _publish_event(claim_id, {
                "stage": "DOC_VERIFICATION", "status": "FAILED",
                "code": verification_result.code,
                "message": verification_result.message,
                "ts": _now()
            })
            _publish_event(claim_id, {
                "stage": "COMPLETE",
                "status": "VERIFICATION_FAILED",
                "error": verification_result.message,
                "ts": _now()
            })
            # Pipeline stops here — return error to caller
            trace = trace_builder.build()
            result = _build_verification_error_response(claim_id, verification_result, trace)
            await _persist_result(claim_id, result)
            return result

        trace_builder.complete_stage("DOC_VERIFICATION", status="PASSED",
                                     outputs_summary={"message": verification_result.message})
        _publish_event(claim_id, {"stage": "DOC_VERIFICATION", "status": "PASSED", "ts": _now()})

        # ─── STAGE 3: EXTRACTION ──────────────────────────────────────────────
        trace_builder.start_stage("EXTRACTION")
        _publish_event(claim_id, {"stage": "EXTRACTION", "status": "IN_PROGRESS", "ts": _now()})

        extractor = ExtractionAgent(router=router)

        # Build test mode content map and load bytes from local storage
        test_content_map: dict[str, dict] = {}
        document_bytes_map: dict[str, bytes] = {}
        
        for doc in documents_meta:
            if "content" in doc and doc["content"]:
                test_content_map[doc["file_id"]] = doc["content"]
            
            if "storage_path" in doc and os.path.exists(doc["storage_path"]):
                with open(doc["storage_path"], "rb") as f:
                    document_bytes_map[doc["file_id"]] = f.read()

        extraction_results = await extractor.extract_all(
            classified_docs=classified_docs,
            document_bytes_map=document_bytes_map,
            test_mode_content_map=test_content_map if test_content_map else None,
        )

        extraction_confidence = extractor.compute_overall_confidence(extraction_results)
        degraded_extractions = [r for r in extraction_results if r.status != "OK"]
        for dr in degraded_extractions:
            overall_degraded = True
            confidence_penalties.append(0.15)
            trace_builder.record_degradation(
                f"Extraction DEGRADED for {dr.file_id}: {dr.error}"
            )

        trace_builder.complete_stage(
            "EXTRACTION", status="PASSED" if not degraded_extractions else "DEGRADED",
            outputs_summary={
                "extracted_count": len(extraction_results),
                "degraded_count": len(degraded_extractions),
                "confidence": extraction_confidence,
            },
            confidence_delta=extraction_confidence - 1.0,
        )
        _publish_event(claim_id, {
            "stage": "EXTRACTION",
            "status": "DEGRADED" if degraded_extractions else "COMPLETE",
            "confidence": extraction_confidence, "ts": _now()
        })

        # ─── STAGE 4: CONSISTENCY_CHECK ───────────────────────────────────────
        trace_builder.start_stage("CONSISTENCY_CHECK")
        _publish_event(claim_id, {"stage": "CONSISTENCY_CHECK", "status": "IN_PROGRESS", "ts": _now()})

        member = policy.get_member(claim_input.member_id)
        member_name = member.name if member else ""

        consistency_agent = CrossDocConsistencyAgent(
            simulate_failure=claim_input.simulate_component_failure
        )
        consistency_cr = await consistency_agent.check(
            extraction_results=extraction_results,
            member_name=member_name,
        )

        if consistency_cr.status == "DEGRADED":
            overall_degraded = True
            confidence_penalties.append(consistency_cr.confidence_penalty)
            trace_builder.record_degradation(consistency_cr.error or "Consistency check degraded")

        consistency_result = consistency_cr.value

        if consistency_result and not consistency_result.patient_match:
            trace_builder.complete_stage(
                "CONSISTENCY_CHECK", status="FAILED",
                checks=consistency_result.checks,
                outputs_summary={"mismatches": consistency_result.mismatches},
            )
            _publish_event(claim_id, {
                "stage": "CONSISTENCY_CHECK", "status": "FAILED",
                "detail": consistency_result.mismatches[0] if consistency_result.mismatches else "",
                "ts": _now()
            })
            _publish_event(claim_id, {
                "stage": "COMPLETE",
                "status": "VERIFICATION_FAILED",
                "error": consistency_result.mismatches[0] if consistency_result.mismatches else "Patient details mismatch",
                "ts": _now()
            })
            trace = trace_builder.build()
            result = _build_mismatch_error_response(claim_id, consistency_result, trace)
            await _persist_result(claim_id, result)
            return result

        trace_builder.complete_stage(
            "CONSISTENCY_CHECK",
            status="DEGRADED" if consistency_cr.status == "DEGRADED" else "PASSED",
            checks=consistency_result.checks if consistency_result else [],
        )
        _publish_event(claim_id, {"stage": "CONSISTENCY_CHECK", "status": "PASSED", "ts": _now()})

        # ─── STAGE 5: DECISIONING ─────────────────────────────────────────────
        trace_builder.start_stage("DECISIONING")
        _publish_event(claim_id, {"stage": "DECISIONING", "status": "IN_PROGRESS", "ts": _now()})

        # Build extracted_data dict for the rules engine
        extracted_data = {
            r.file_id: r.extraction
            for r in extraction_results
            if r.extraction is not None
        }

        if not member:
            trace_builder.record_degradation(f"Member {claim_input.member_id} not found in policy.")
            overall_degraded = True

        # Run Rules Engine (Deterministic)
        from app.agents.decision_engine import PolicyRulesEngine
        rules_engine = PolicyRulesEngine()
        rules_decision = rules_engine.decide(
            claim_input=claim_input,
            member=member or _dummy_member(claim_input.member_id),
            policy=policy,
            extracted_data=extracted_data,
            claims_history=claim_input.claims_history,
            ytd_claims_amount=claim_data.get("ytd_claims_amount", 0.0),
            degraded=overall_degraded,
            degradation_notes=trace_builder._degradation_notes,
        )

        # Run LLM Decision Engine
        from app.agents.llm_decision_engine import LLMDecisionEngine
        llm_engine = LLMDecisionEngine(router=router)
        decision_cr = await llm_engine.decide_async(
            claim_input=claim_input,
            member=member or _dummy_member(claim_input.member_id),
            policy=policy,
            extracted_data=extracted_data,
            claims_history=claim_input.claims_history,
            ytd_claims_amount=claim_data.get("ytd_claims_amount", 0.0),
            degraded=overall_degraded,
            degradation_notes=trace_builder._degradation_notes,
        )
        llm_decision = decision_cr.value

        # Compare decisions
        mismatch_reasons = []
        if rules_decision.decision != llm_decision.decision:
            mismatch_reasons.append(
                f"Decision mismatch: Rules Engine resolved '{rules_decision.decision}', "
                f"but LLM Engine resolved '{llm_decision.decision}'."
            )
        if rules_decision.approved_amount != llm_decision.approved_amount:
            mismatch_reasons.append(
                f"Approved amount mismatch: Rules Engine approved ₹{rules_decision.approved_amount}, "
                f"but LLM Engine approved ₹{llm_decision.approved_amount}'."
            )

        if mismatch_reasons:
            mismatch_detail = " ".join(mismatch_reasons)
            log.warning(f"Pipeline decision mismatch for claim {claim_id}: {mismatch_detail}")
            
            # Record mismatch check in the trace
            from app.models.decision import CheckResult, DecisionResult
            mismatch_check = CheckResult(
                check_name="decision_comparison",
                passed=False,
                detail=f"{mismatch_detail} Further verification is needed.",
                policy_reference="pipeline.comparison"
            )
            
            # Combine all checks from Rules Engine and append the mismatch check
            all_decision_checks = rules_decision.checks + [mismatch_check]
            
            decision = DecisionResult(
                decision="MANUAL_REVIEW",
                approved_amount=rules_decision.approved_amount,  # Rules engine is mathematically precise, use it as baseline
                claimed_amount=claim_input.claimed_amount,
                line_item_breakdown=rules_decision.line_item_breakdown,
                reasons=["DECISION_MISMATCH"],
                checks=all_decision_checks,
                confidence=min(rules_decision.confidence, llm_decision.confidence, 0.5),  # Lower confidence due to disagreement
                degraded=True,
                degradation_notes=list(set(
                    rules_decision.degradation_notes + 
                    llm_decision.degradation_notes + 
                    [f"{mismatch_detail} Further verification is needed."]
                )),
                network_discount_applied=rules_decision.network_discount_applied,
                copay_deducted=rules_decision.copay_deducted,
                is_network_hospital=rules_decision.is_network_hospital,
            )
        else:
            # Decisions match completely! Use the rules_decision as it contains exact checks and copay math
            decision = rules_decision

        # Apply confidence penalties from degraded components
        if confidence_penalties:
            total_penalty = sum(confidence_penalties)
            decision.confidence = max(0.0, decision.confidence - total_penalty)

        trace_builder.set_final_decision(decision)
        trace_builder.complete_stage(
            "DECISIONING", status="PASSED",
            outputs_summary={
                "decision": decision.decision,
                "approved_amount": decision.approved_amount,
                "confidence": decision.confidence,
            },
            checks=decision.checks,
        )
        _publish_event(claim_id, {"stage": "DECISIONING", "status": "PASSED", "ts": _now()})

        # ─── STAGE 6: COMPLETE ────────────────────────────────────────────────
        trace = trace_builder.build()
        result = {
            "claim_id": claim_id,
            "status": "COMPLETE",
            "decision": decision.model_dump(),
            "trace": trace.model_dump(),
        }
        await _persist_result(claim_id, result)

        _publish_event(claim_id, {
            "stage": "COMPLETE",
            "status": "COMPLETE",
            "decision": decision.decision,
            "approved_amount": decision.approved_amount,
            "confidence": decision.confidence,
            "degraded": decision.degraded,
            "trace_url": f"/claims/{claim_id}/trace",
            "ts": _now()
        })

        return result

    except Exception as e:
        # Top-level safety net — pipeline MUST NOT crash and return nothing
        log.error(f"Pipeline crashed for claim {claim_id}: {e}", exc_info=True)
        trace = trace_builder.build()
        error_result = {
            "claim_id": claim_id,
            "status": "FAILED",
            "error": str(e),
            "trace": trace.model_dump(),
        }
        _publish_event(claim_id, {
            "stage": "COMPLETE", "status": "FAILED",
            "error": str(e), "ts": _now()
        })
        await _persist_result(claim_id, error_result)
        return error_result


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_claim_input(data: dict):
    from app.models.domain import ClaimHistoryEntry, ClaimInput
    from datetime import date

    history = []
    for h in data.get("claims_history", []):
        try:
            history.append(ClaimHistoryEntry(
                claim_id=h.get("claim_id", ""),
                member_id=data.get("member_id", ""),
                treatment_date=date.fromisoformat(h["date"]),
                claimed_amount=float(h.get("amount", 0)),
                status="APPROVED",
                provider=h.get("provider"),
            ))
        except Exception:
            pass

    return ClaimInput(
        claim_id=data.get("claim_id", ""),
        member_id=data["member_id"],
        policy_id=data.get("policy_id", "PLUM_GHI_2024"),
        claim_category=data["claim_category"],
        treatment_date=date.fromisoformat(data["treatment_date"]),
        claimed_amount=float(data["claimed_amount"]),
        hospital_name=data.get("hospital_name"),
        pre_auth_obtained=data.get("pre_auth_obtained", False),
        simulate_component_failure=data.get("simulate_component_failure", False),
        claims_history=history,
    )


def _dummy_member(member_id: str):
    """Fallback member when member is not found in policy (degraded path)."""
    from datetime import date
    from app.models.domain import Member
    return Member(
        member_id=member_id,
        name="Unknown Member",
        date_of_birth=date(1990, 1, 1),
        gender="M",
        relationship="SELF",
        join_date=date(2024, 4, 1),
    )


def _build_verification_error_response(claim_id: str, verification_result, trace) -> dict:
    return {
        "claim_id": claim_id,
        "status": "VERIFICATION_FAILED",
        "verification_error": {
            "code": verification_result.code,
            "message": verification_result.message,
            "affected_file_ids": verification_result.affected_file_ids,
        },
        "trace": trace.model_dump(),
    }


def _build_mismatch_error_response(claim_id: str, consistency_result, trace) -> dict:
    from app.models.domain import VerificationResult
    return {
        "claim_id": claim_id,
        "status": "VERIFICATION_FAILED",
        "verification_error": {
            "code": "PATIENT_MISMATCH",
            "message": consistency_result.mismatches[0] if consistency_result.mismatches else "Patient mismatch",
            "affected_file_ids": [],
        },
        "trace": trace.model_dump(),
    }


async def _persist_result(claim_id: str, result: dict) -> None:
    """Persist pipeline result to Redis (quick) and DB (async)."""
    # 1. Persist to Redis
    try:
        r = _get_redis()
        r.setex(f"claim_result:{claim_id}", 86400, json.dumps(result, default=str))
    except Exception as e:
        log.warning(f"Failed to cache result for {claim_id} in Redis: {e}")

    # 2. Persist to PostgreSQL database
    try:
        from app.db.session import AsyncSessionLocal
        from app.db.models import ClaimModel, DecisionModel, ClaimTraceModel
        from sqlalchemy import select
        
        # Coerce the entire result into a JSON-serializable dict (handles datetime objects)
        serializable_result = json.loads(json.dumps(result, default=str))
        
        async with AsyncSessionLocal() as session:
            # Fetch the claim record
            stmt = select(ClaimModel).where(ClaimModel.id == claim_id)
            db_res = await session.execute(stmt)
            claim = db_res.scalar_one_or_none()
            
            if claim:
                # Update status
                claim.status = serializable_result.get("status", "COMPLETE")
                
                # Check if decision needs to be persisted
                decision_data = serializable_result.get("decision")
                if decision_data:
                    # Let's delete existing decision if any to avoid duplication
                    del_stmt = select(DecisionModel).where(DecisionModel.claim_id == claim_id)
                    existing_dec_res = await session.execute(del_stmt)
                    if existing_dec := existing_dec_res.scalar_one_or_none():
                        await session.delete(existing_dec)
                        
                    db_decision = DecisionModel(
                        claim_id=claim_id,
                        decision=decision_data.get("decision", "MANUAL_REVIEW"),
                        approved_amount=float(decision_data.get("approved_amount", 0.0)),
                        reasons_json=decision_data.get("reasons", []),
                        checks_json=decision_data.get("checks", []),
                        confidence_score=float(decision_data.get("confidence", 0.0)),
                        degraded=bool(decision_data.get("degraded", False)),
                        degradation_notes=decision_data.get("degradation_notes", []),
                    )
                    session.add(db_decision)
                
                # Check if trace needs to be persisted
                trace_data = serializable_result.get("trace")
                if trace_data:
                    del_trace_stmt = select(ClaimTraceModel).where(ClaimTraceModel.claim_id == claim_id)
                    existing_trace_res = await session.execute(del_trace_stmt)
                    if existing_trace := existing_trace_res.scalar_one_or_none():
                        await session.delete(existing_trace)
                        
                    db_trace = ClaimTraceModel(
                        claim_id=claim_id,
                        trace_json=trace_data,
                    )
                    session.add(db_trace)
                    
                await session.commit()
                log.info(f"Successfully persisted claim {claim_id} to database with status {claim.status}.")
            else:
                log.error(f"Claim {claim_id} not found in database, skipping DB persistence.")
                
    except Exception as db_e:
        log.error(f"Failed to persist claim {claim_id} to database: {db_e}", exc_info=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
