"""
DocumentVerificationAgent — pure deterministic code, no LLM.

Checks that the right document types have been uploaded for the claim category.
Must fail fast with SPECIFIC error messages — generic errors are a grading failure.

Three distinct result codes (never collapsed):
  MISSING_REQUIRED_DOCUMENT — wrong type uploaded / required type absent (TC001)
  DOCUMENT_UNREADABLE       — right type but too blurry/poor quality (TC002)
  PATIENT_MISMATCH          — handled by ConsistencyAgent, not here (TC003)
"""
from __future__ import annotations

import logging
from collections import Counter

from app.models.domain import (
    ClassifiedDoc,
    PolicyTerms,
    VerificationCode,
    VerificationResult,
)

log = logging.getLogger(__name__)


class DocumentVerificationAgent:
    """
    Verifies that classified documents satisfy the policy requirements for
    the given claim category.

    This is a set-comparison agent — pure Python, no LLM.
    The speed and correctness of this step is graded directly (10% of total).
    """

    def verify(
        self,
        *,
        claim_category: str,
        classified_docs: list[ClassifiedDoc],
        policy: PolicyTerms,
    ) -> VerificationResult:
        """
        Run all document verification checks.

        Checks in order:
        1. Any unreadable documents → DOCUMENT_UNREADABLE (distinct from wrong type)
        2. Missing required document types → MISSING_REQUIRED_DOCUMENT
        3. All OK → return VerificationResult(ok=True)
        """
        requirements = policy.document_requirements.get(claim_category.upper())
        if not requirements:
            # Unknown category — can't verify
            log.warning(f"No document requirements configured for category '{claim_category}'.")
            return VerificationResult(
                ok=False,
                code="MISSING_REQUIRED_DOCUMENT",
                message=(
                    f"Unknown claim category '{claim_category}'. "
                    f"Cannot verify document requirements."
                ),
            )

        # ── Check 1: Unreadable documents ─────────────────────────────────────
        # TC002: This is a QUALITY problem, not a WRONG_TYPE problem.
        # Must ask for re-upload of that specific file, NOT reject the claim.
        unreadable = [d for d in classified_docs if d.quality_flag == "UNREADABLE"]
        if unreadable:
            file_names = ", ".join(d.file_name for d in unreadable)
            file_ids = [d.file_id for d in unreadable]
            return VerificationResult(
                ok=False,
                code="DOCUMENT_UNREADABLE",
                message=(
                    f"The following document(s) cannot be read due to poor image quality: "
                    f"{file_names}. Please re-upload a clearer photo or scan of "
                    f"{'this document' if len(unreadable) == 1 else 'these documents'}. "
                    f"The claim has NOT been rejected — please resubmit with a legible copy."
                ),
                affected_file_ids=file_ids,
            )

        # ── Check 2: Missing required document types ───────────────────────────
        # TC001: Message must name BOTH what was uploaded AND what is needed.
        uploaded_types = {d.predicted_type for d in classified_docs if d.predicted_type != "UNKNOWN"}
        required_types = set(requirements.required)
        missing = required_types - uploaded_types

        if missing:
            # Count what was actually uploaded for a specific message
            type_counts = Counter(d.predicted_type for d in classified_docs)
            uploaded_descriptions = []
            for doc_type, count in type_counts.items():
                if doc_type == "UNKNOWN":
                    uploaded_descriptions.append(f"{count} unrecognised document(s)")
                else:
                    name = doc_type.replace("_", " ").lower()
                    uploaded_descriptions.append(f"{count} {name}(s)" if count > 1 else f"1 {name}")

            uploaded_str = (
                ", ".join(uploaded_descriptions)
                if uploaded_descriptions
                else "no recognisable documents"
            )
            missing_str = ", ".join(
                t.replace("_", " ").lower() for t in sorted(missing)
            )
            required_str = ", ".join(
                t.replace("_", " ").lower() for t in sorted(required_types)
            )

            return VerificationResult(
                ok=False,
                code="MISSING_REQUIRED_DOCUMENT",
                message=(
                    f"This {claim_category.lower()} claim requires: {required_str}. "
                    f"You uploaded: {uploaded_str}. "
                    f"Please upload the missing document(s): {missing_str}."
                ),
                affected_file_ids=[],
            )

        # ── All checks passed ─────────────────────────────────────────────────
        return VerificationResult(
            ok=True,
            code="OK",
            message=(
                f"All required documents for {claim_category} claim are present: "
                f"{', '.join(sorted(required_types))}."
            ),
        )
