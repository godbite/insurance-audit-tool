"""
Claims API endpoints.

POST /claims         — submit a new claim (returns 202 + claim_id)
GET  /claims/{id}    — get current status + decision
GET  /claims/{id}/trace — full ClaimTrace JSON
GET  /claims         — list claims with pagination
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Optional
import os

import redis as redis_lib
from fastapi import APIRouter, Depends, File, Form, HTTPException, Header, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.db.models import ClaimModel, ClaimTraceModel, DecisionModel, DocumentModel
from app.db.session import get_db
import logging
from app.tasks.pipeline import run_claim_pipeline

log = logging.getLogger(__name__)
router = APIRouter(prefix="/claims", tags=["claims"])


# ─── Submit claim ─────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def submit_claim(
    # Form fields
    member_id: str = Form(...),
    policy_id: str = Form(...),
    claim_category: str = Form(...),
    treatment_date: str = Form(...),
    claimed_amount: float = Form(...),
    hospital_name: Optional[str] = Form(None),
    pre_auth_obtained: bool = Form(False),
    simulate_component_failure: bool = Form(False),
    # File uploads
    files: list[UploadFile] = File([]),
    # Idempotency
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    # Dependencies
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Submit a new insurance claim.

    Returns 202 Accepted immediately. Use GET /claims/{id} or
    WebSocket /ws/claims/{id} for status updates.

    Idempotency: provide Idempotency-Key header to prevent duplicate claims
    from retried network requests.
    """
    # Idempotency check
    if idempotency_key:
        existing = await db.execute(
            select(ClaimModel).where(ClaimModel.idempotency_key == idempotency_key)
        )
        if existing_claim := existing.scalar_one_or_none():
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"claim_id": existing_claim.id, "status": existing_claim.status,
                         "note": "Duplicate request; returning existing claim."},
            )

    # Create claim record
    claim_id = str(uuid.uuid4())
    claim = ClaimModel(
        id=claim_id,
        member_id=member_id,
        policy_id=policy_id,
        category=claim_category,
        claimed_amount=claimed_amount,
        treatment_date=date.fromisoformat(treatment_date),
        hospital_name=hospital_name,
        pre_auth_obtained=pre_auth_obtained,
        simulate_component_failure=simulate_component_failure,
        idempotency_key=idempotency_key,
        status="QUEUED",
    )
    db.add(claim)

    # Store uploaded files locally for celery worker
    documents_meta = []
    for f in files:
        file_id = str(uuid.uuid4())
        contents = await f.read()
        
        storage_dir = os.path.join("/tmp", "plum_claims", claim_id, file_id)
        os.makedirs(storage_dir, exist_ok=True)
        storage_path = os.path.join(storage_dir, f.filename or "uploaded_file")
        
        with open(storage_path, "wb") as out_f:
            out_f.write(contents)

        # Upload file to S3/MinIO for the celery worker to access in production
        try:
            from app.core.storage import upload_file_to_s3
            upload_file_to_s3(
                claim_id=claim_id,
                file_id=file_id,
                filename=f.filename or file_id,
                content=contents
            )
        except Exception as e:
            log.warning(f"S3 upload failed for file {file_id}: {e}")

        doc = DocumentModel(
            claim_id=claim_id,
            file_id=file_id,
            file_name=f.filename or file_id,
            storage_path=storage_path,
        )
        db.add(doc)
        documents_meta.append({
            "file_id": file_id,
            "file_name": f.filename or file_id,
            "storage_path": storage_path,
        })

    await db.commit()

    # Enqueue Celery pipeline
    claim_data = {
        "claim_id": claim_id,
        "member_id": member_id,
        "policy_id": policy_id,
        "claim_category": claim_category,
        "treatment_date": treatment_date,
        "claimed_amount": claimed_amount,
        "hospital_name": hospital_name,
        "pre_auth_obtained": pre_auth_obtained,
        "simulate_component_failure": simulate_component_failure,
        "documents": documents_meta,
    }
    run_claim_pipeline.delay(claim_id, claim_data)

    return {"claim_id": claim_id, "status": "QUEUED"}


# ─── Get claim status ─────────────────────────────────────────────────────────

@router.get("/{claim_id}")
async def get_claim(
    claim_id: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Get current claim status and decision (if complete)."""
    # Try Redis cache first (fast path)
    try:
        kwargs = {"ssl_cert_reqs": "none"} if settings.redis_url.startswith("rediss://") else {}
        r = redis_lib.from_url(settings.redis_url, **kwargs)
        cached = r.get(f"claim_result:{claim_id}")
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    # Fall back to DB
    result = await db.execute(
        select(ClaimModel)
        .options(selectinload(ClaimModel.decision), selectinload(ClaimModel.trace))
        .where(ClaimModel.id == claim_id)
    )
    claim = result.scalar_one_or_none()
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim '{claim_id}' not found.")

    response: dict = {
        "claim_id": claim_id,
        "status": claim.status,
        "member_id": claim.member_id,
        "category": claim.category,
        "claimed_amount": claim.claimed_amount,
        "submitted_at": claim.submitted_at.isoformat(),
    }

    if claim.decision:
        # Load from trace if present to get full details (checks, breakdown, etc.)
        decision_details = {}
        if claim.trace and claim.trace.trace_json:
            decision_details = claim.trace.trace_json.get("final_decision") or {}

        response["decision"] = {
            "decision": decision_details.get("decision") or claim.decision.decision,
            "approved_amount": decision_details.get("approved_amount") if decision_details.get("approved_amount") is not None else claim.decision.approved_amount,
            "confidence": decision_details.get("confidence") if decision_details.get("confidence") is not None else claim.decision.confidence_score,
            "reasons": decision_details.get("reasons") or claim.decision.reasons_json,
            "checks": decision_details.get("checks") or claim.decision.checks_json,
            "degraded": decision_details.get("degraded") if decision_details.get("degraded") is not None else claim.decision.degraded,
            "degradation_notes": decision_details.get("degradation_notes") or claim.decision.degradation_notes,
            "line_item_breakdown": decision_details.get("line_item_breakdown") or [],
            "network_discount_applied": decision_details.get("network_discount_applied") or 0.0,
            "copay_deducted": decision_details.get("copay_deducted") or 0.0,
            "is_network_hospital": decision_details.get("is_network_hospital") or False,
        }

    if claim.status == "VERIFICATION_FAILED":
        # Attempt to extract verification_error from trace_json
        if claim.trace and claim.trace.trace_json:
            trace_json = claim.trace.trace_json
            failed_stage = None
            for stage in trace_json.get("stages", []):
                if stage.get("status") == "FAILED":
                    failed_stage = stage
                    break

            if failed_stage:
                outputs = failed_stage.get("outputs_summary", {})
                code = outputs.get("code") or "PATIENT_MISMATCH"
                message = outputs.get("message")
                if not message and "mismatches" in outputs:
                    mismatches = outputs.get("mismatches", [])
                    message = mismatches[0] if mismatches else "Patient details mismatch across documents"

                response["verification_error"] = {
                    "code": code,
                    "message": message or f"Failed at stage {failed_stage.get('stage_name')}",
                    "affected_file_ids": outputs.get("affected_file_ids", []),
                }

    return response



# ─── Get full trace ───────────────────────────────────────────────────────────

@router.get("/{claim_id}/trace")
async def get_claim_trace(
    claim_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the full ClaimTrace JSON — the complete audit trail for the claim."""
    result = await db.execute(
        select(ClaimTraceModel).where(ClaimTraceModel.claim_id == claim_id)
    )
    trace = result.scalar_one_or_none()
    if not trace:
        # Try Redis
        try:
            kwargs = {"ssl_cert_reqs": "none"} if get_settings().redis_url.startswith("rediss://") else {}
            r = redis_lib.from_url(get_settings().redis_url, **kwargs)
            cached = r.get(f"claim_result:{claim_id}")
            if cached:
                data = json.loads(cached)
                if "trace" in data:
                    return data["trace"]
        except Exception:
            pass
        raise HTTPException(status_code=404, detail=f"Trace for claim '{claim_id}' not found.")

    return trace.trace_json


# ─── List claims ──────────────────────────────────────────────────────────────

@router.get("")
async def list_claims(
    member_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List claims with optional member_id filter."""
    query = select(ClaimModel).order_by(ClaimModel.submitted_at.desc()).limit(limit).offset(offset)
    if member_id:
        query = query.where(ClaimModel.member_id == member_id)

    result = await db.execute(query)
    claims = result.scalars().all()

    return [
        {
            "claim_id": c.id,
            "member_id": c.member_id,
            "category": c.category,
            "claimed_amount": c.claimed_amount,
            "status": c.status,
            "submitted_at": c.submitted_at.isoformat(),
        }
        for c in claims
    ]


# ─── Test/Integration endpoint — submit claim with pre-extracted content ──────

@router.post("/test/submit", status_code=status.HTTP_202_ACCEPTED)
async def submit_test_claim(
    claim_data: dict,
    settings: Settings = Depends(get_settings),
):
    """
    Test endpoint: submit a claim with pre-provided document content.
    Mirrors the test_cases.json format for integration testing.

    Body: full test case input dict (member_id, claim_category, documents with content, etc.)
    """
    claim_id = str(uuid.uuid4())
    claim_data["claim_id"] = claim_id

    # Ensure documents have actual_type set for classifier test mode
    for doc in claim_data.get("documents", []):
        if "file_id" not in doc:
            doc["file_id"] = str(uuid.uuid4())

    run_claim_pipeline.delay(claim_id, claim_data)
    return {"claim_id": claim_id, "status": "QUEUED"}
