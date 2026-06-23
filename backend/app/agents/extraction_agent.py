"""
ExtractionAgent — extracts structured data from each document via LLM.

One LLM call per document, in parallel (asyncio.gather).
Returns typed Pydantic objects (not generic blobs).
On provider failure → DEGRADED status, pipeline continues.
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
from typing import Optional

from app.models.domain import ClassifiedDoc, ComponentResult
from app.models.extraction_schemas import (
    EXTRACT_SCHEMA_MAP,
    AnyExtract,
    DocumentExtractionResult,
)
from app.providers.router import ProviderRouter
from app.tracing.component_runner import run_component

log = logging.getLogger(__name__)


class ExtractionAgent:
    """
    Extracts structured fields from classified documents.

    Uses ProviderRouter for LLM calls with automatic failover.
    All documents within a claim extracted in parallel.
    """

    def __init__(self, router: ProviderRouter):
        self._router = router

    async def extract_all(
        self,
        *,
        classified_docs: list[ClassifiedDoc],
        document_bytes_map: dict[str, bytes],  # file_id → bytes
        # For test mode: inject pre-extracted content directly
        test_mode_content_map: Optional[dict[str, dict]] = None,
    ) -> list[DocumentExtractionResult]:
        """
        Extract all documents in parallel.
        Returns list of DocumentExtractionResult, one per document.
        """
        tasks = [
            self._extract_one(
                doc=doc,
                document_bytes=document_bytes_map.get(doc.file_id, b""),
                test_mode_content=test_mode_content_map.get(doc.file_id) if test_mode_content_map else None,
            )
            for doc in classified_docs
            if doc.predicted_type not in ("UNKNOWN",)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=False)
        return list(results)

    async def _extract_one(
        self,
        *,
        doc: ClassifiedDoc,
        document_bytes: bytes,
        test_mode_content: Optional[dict] = None,
    ) -> DocumentExtractionResult:
        """Extract a single document. Handles failures gracefully."""
        schema_class = EXTRACT_SCHEMA_MAP.get(doc.predicted_type)
        if not schema_class:
            return DocumentExtractionResult(
                file_id=doc.file_id,
                document_type=doc.predicted_type,
                extraction=None,
                status="FAILED",
                provider_used="none",
                confidence=0.0,
                error=f"No extraction schema for document type '{doc.predicted_type}'.",
            )

        async def _do_extract() -> DocumentExtractionResult:
            # Test mode: use pre-provided content JSON
            if test_mode_content:
                try:
                    parsed = schema_class.model_validate(test_mode_content)
                    return DocumentExtractionResult(
                        file_id=doc.file_id,
                        document_type=doc.predicted_type,
                        extraction=parsed,
                        status="OK",
                        provider_used="test_stub",
                        confidence=0.95,
                    )
                except Exception as e:
                    log.warning(f"Test mode content validation failed for {doc.file_id}: {e}")

            # Real mode: call provider router
            mime_type = self._detect_mime(doc.file_name, document_bytes)
            result = await self._router.extract_with_failover(
                document_bytes=document_bytes,
                mime_type=mime_type,
                document_type=doc.predicted_type,
                schema=schema_class,
                prompt_context={"document_type": doc.predicted_type},
            )

            if result.parsed is None:
                return DocumentExtractionResult(
                    file_id=doc.file_id,
                    document_type=doc.predicted_type,
                    extraction=None,
                    status="DEGRADED",
                    provider_used=result.provider_name,
                    confidence=0.0,
                    error=result.error,
                )

            # Combine extraction confidence with classifier confidence
            combined_confidence = (result.confidence + doc.confidence) / 2.0

            return DocumentExtractionResult(
                file_id=doc.file_id,
                document_type=doc.predicted_type,
                extraction=result.parsed,  # type: ignore[arg-type]
                status="OK",
                provider_used=result.provider_name,
                confidence=round(combined_confidence, 3),
            )

        component_result = await run_component(
            _do_extract,
            component_name=f"extraction.{doc.file_id}",
            fallback=DocumentExtractionResult(
                file_id=doc.file_id,
                document_type=doc.predicted_type,
                extraction=None,
                status="DEGRADED",
                provider_used="none",
                confidence=0.0,
                error="Extraction component failed unexpectedly.",
            ),
            penalty_on_failure=0.15,
        )

        return component_result.value or DocumentExtractionResult(
            file_id=doc.file_id,
            document_type=doc.predicted_type,
            status="FAILED",
            provider_used="none",
            confidence=0.0,
        )

    @staticmethod
    def _detect_mime(file_name: str, document_bytes: bytes) -> str:
        guessed, _ = mimetypes.guess_type(file_name)
        if guessed:
            return guessed
        if document_bytes[:4] == b"%PDF":
            return "application/pdf"
        return "image/jpeg"

    @staticmethod
    def compute_overall_confidence(extraction_results: list[DocumentExtractionResult]) -> float:
        """
        Compute claim-level extraction confidence as weighted average.
        Degraded/failed documents drag confidence down.
        """
        if not extraction_results:
            return 1.0

        scores = []
        for r in extraction_results:
            if r.status == "OK":
                scores.append(r.confidence)
            elif r.status == "DEGRADED":
                scores.append(r.confidence * 0.3)  # heavy penalty
            else:  # FAILED
                scores.append(0.0)

        return round(sum(scores) / len(scores), 3)
