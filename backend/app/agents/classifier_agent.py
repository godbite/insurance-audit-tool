"""
DocumentClassifierAgent — identifies document types.

Two modes:
1. TEST MODE (X-Test-Mode: true header or TEST_MODE env flag):
   Reads `actual_type` from test metadata injected alongside the file.
   Used for TC001-TC003 which test verification logic, not classification.

2. REAL MODE:
   Calls ProviderRouter.classify_with_failover() with Gemini vision.
   Low confidence (<0.5) → UNKNOWN, quality below threshold → DOCUMENT_UNREADABLE path.
"""
from __future__ import annotations

import logging
import mimetypes
from typing import Optional

from app.core.config import get_settings
from app.models.domain import ClassifiedDoc, ComponentResult
from app.providers.router import ProviderRouter
from app.tracing.component_runner import run_component

log = logging.getLogger(__name__)

VALID_DOCUMENT_TYPES = {
    "PRESCRIPTION",
    "HOSPITAL_BILL",
    "LAB_REPORT",
    "PHARMACY_BILL",
    "DENTAL_REPORT",
    "DISCHARGE_SUMMARY",
    "UNKNOWN",
}


class DocumentClassifierAgent:
    """
    Classifies each uploaded document into a known DocumentType.

    Failure mode: low confidence → UNKNOWN (never silently guess).
    Quality below threshold → triggers DOCUMENT_UNREADABLE path in VerificationAgent.
    """

    def __init__(self, router: ProviderRouter, settings=None):
        self._router = router
        self._settings = settings or get_settings()

    async def classify_document(
        self,
        *,
        file_id: str,
        file_name: str,
        document_bytes: bytes,
        # Test mode: inject known type + quality
        test_mode_type: Optional[str] = None,
        test_mode_quality: Optional[str] = None,
    ) -> ComponentResult[ClassifiedDoc]:
        """
        Classify a single document.

        In test mode, uses test_mode_type directly (bypasses LLM).
        In real mode, calls ProviderRouter.
        """
        mime_type = self._detect_mime(file_name, document_bytes)

        async def _do_classify() -> ClassifiedDoc:
            if test_mode_type:
                # Test mode: deterministic classification
                doc_type = test_mode_type.upper()
                if doc_type not in VALID_DOCUMENT_TYPES:
                    doc_type = "UNKNOWN"

                confidence = 0.95
                quality_flag = "GOOD"

                if test_mode_quality == "UNREADABLE":
                    confidence = 0.1  # below threshold → triggers DOCUMENT_UNREADABLE
                    quality_flag = "UNREADABLE"
                elif test_mode_quality == "DEGRADED":
                    confidence = 0.55
                    quality_flag = "DEGRADED"

                return ClassifiedDoc(
                    file_id=file_id,
                    file_name=file_name,
                    predicted_type=doc_type,  # type: ignore[arg-type]
                    confidence=confidence,
                    quality_flag=quality_flag,  # type: ignore[arg-type]
                )

            # Real mode: call vision LLM
            doc_type, confidence, provider_name = await self._router.classify_with_failover(
                document_bytes=document_bytes,
                mime_type=mime_type,
            )

            # Map to valid type
            if doc_type not in VALID_DOCUMENT_TYPES:
                doc_type = "UNKNOWN"
                confidence = min(confidence, 0.3)

            # Low confidence → treat as UNKNOWN
            if confidence < 0.5:
                doc_type = "UNKNOWN"

            # Quality signal: very low confidence → document is unreadable
            quality_flag = "GOOD"
            if confidence < self._settings.doc_quality_threshold:
                quality_flag = "UNREADABLE"
            elif confidence < 0.6:
                quality_flag = "DEGRADED"

            return ClassifiedDoc(
                file_id=file_id,
                file_name=file_name,
                predicted_type=doc_type,  # type: ignore[arg-type]
                confidence=confidence,
                quality_flag=quality_flag,  # type: ignore[arg-type]
            )

        return await run_component(
            _do_classify,
            component_name=f"classifier.{file_id}",
            fallback=ClassifiedDoc(
                file_id=file_id,
                file_name=file_name,
                predicted_type="UNKNOWN",
                confidence=0.0,
                quality_flag="UNREADABLE",
            ),
            penalty_on_failure=0.15,
        )

    @staticmethod
    def _detect_mime(file_name: str, document_bytes: bytes) -> str:
        """Detect MIME type from filename or magic bytes."""
        # Try filename extension first
        guessed, _ = mimetypes.guess_type(file_name)
        if guessed:
            return guessed

        # Magic bytes fallback
        if document_bytes[:4] == b"%PDF":
            return "application/pdf"
        if document_bytes[:2] in (b"\xff\xd8", b"\x89P"):
            return "image/jpeg" if document_bytes[:2] == b"\xff\xd8" else "image/png"

        return "application/octet-stream"
