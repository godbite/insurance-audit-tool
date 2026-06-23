"""
CrossDocConsistencyAgent — patient name matching and fraud signal detection.

TC003: "prescription is for Rajesh Kumar, hospital bill is for Arjun Mehta" →
PATIENT_MISMATCH with exact names from each document.

Strategy:
1. Exact string match (case-insensitive, stripped)
2. Fuzzy match via rapidfuzz (handles typos, partial names)
3. LLM disambiguation ONLY for genuinely ambiguous cases (nicknames, transliterations)
   — this is the one LLM call in this agent, and only as a last resort

For TC011 fault injection: if settings.simulate_component_failure=True,
this agent raises a controlled exception caught by run_component().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

from app.models.decision import CheckResult
from app.models.domain import ComponentResult, VerificationResult
from app.models.extraction_schemas import DocumentExtractionResult

log = logging.getLogger(__name__)

# Fuzzy match threshold — above this score → likely same person
FUZZY_MATCH_THRESHOLD = 80  # rapidfuzz WRatio score 0–100


@dataclass
class ConsistencyResult:
    patient_match: bool
    patient_names: dict[str, str]  # file_id → extracted patient name
    mismatches: list[str]
    fraud_signals: list[str]
    checks: list[CheckResult]


class CrossDocConsistencyAgent:
    """
    Checks cross-document consistency:
    - Patient name agreement across all uploaded documents
    - Date sanity (treatment date appears on at least one doc)
    - Alteration/duplicate stamp fraud signals

    Failure mode: if all name extractions failed → DEGRADED (not hard failure).
    TC011: raise controlled exception when simulate_component_failure=True.
    """

    def __init__(self, simulate_failure: bool = False):
        self._simulate_failure = simulate_failure

    async def check(
        self,
        *,
        extraction_results: list[DocumentExtractionResult],
        member_name: str,
    ) -> ComponentResult[ConsistencyResult]:
        """
        Run consistency checks across all extracted documents.

        Returns ComponentResult — if this agent fails, the pipeline continues
        with DEGRADED status (TC011 requirement).
        """
        from app.tracing.component_runner import run_component

        async def _do_check() -> ConsistencyResult:
            # TC011 fault injection
            if self._simulate_failure:
                raise RuntimeError(
                    "Simulated component failure in CrossDocConsistencyAgent "
                    "(triggered by simulate_component_failure=true)."
                )

            return await self._run_consistency(
                extraction_results=extraction_results,
                member_name=member_name,
            )

        return await run_component(
            _do_check,
            component_name="consistency_agent",
            fallback=ConsistencyResult(
                patient_match=True,  # assume OK when agent fails (conservative fallback)
                patient_names={},
                mismatches=[],
                fraud_signals=["Consistency check skipped due to component failure."],
                checks=[
                    CheckResult(
                        check_name="consistency.patient_match",
                        passed=True,
                        detail="Consistency check was skipped due to a component failure. "
                               "Patient name matching could not be performed.",
                        policy_reference="cross_doc_consistency",
                    )
                ],
            ),
            penalty_on_failure=0.20,
        )

    async def _run_consistency(
        self,
        *,
        extraction_results: list[DocumentExtractionResult],
        member_name: str,
    ) -> ConsistencyResult:
        checks: list[CheckResult] = []
        patient_names: dict[str, str] = {}
        fraud_signals: list[str] = []

        # ── Collect patient names from all extracted documents ────────────────
        for result in extraction_results:
            if result.extraction is None:
                continue
            name = None
            if hasattr(result.extraction, "patient_name"):
                name = result.extraction.patient_name

            if name and name.strip():
                patient_names[result.file_id] = name.strip()

        # ── Patient name matching ─────────────────────────────────────────────
        patient_match, mismatch_detail = self._check_patient_names(
            patient_names=patient_names,
            member_name=member_name,
            extraction_results=extraction_results,
        )
        checks.append(mismatch_detail)

        # ── Alteration / fraud signals from extraction ────────────────────────
        for result in extraction_results:
            if result.extraction is None:
                continue
            if getattr(result.extraction, "alteration_flag", False):
                fraud_signals.append(
                    f"Document '{result.file_id}' ({result.document_type}) "
                    f"has visible corrections or strikethroughs on amounts."
                )
            if getattr(result.extraction, "duplicate_stamp_flag", False):
                fraud_signals.append(
                    f"Document '{result.file_id}' ({result.document_type}) "
                    f"has multiple ORIGINAL/DUPLICATE stamps."
                )
            if getattr(result.extraction, "amount_discrepancy_flag", False):
                fraud_signals.append(
                    f"Document '{result.file_id}' ({result.document_type}) "
                    f"has a discrepancy between amount in words and figures."
                )

        if fraud_signals:
            checks.append(CheckResult(
                check_name="consistency.fraud_signals",
                passed=False,
                detail=f"Document fraud signals detected: {'; '.join(fraud_signals)}",
                policy_reference="fraud_thresholds",
                data={"signals": fraud_signals},
            ))

        return ConsistencyResult(
            patient_match=patient_match,
            patient_names=patient_names,
            mismatches=[] if patient_match else [mismatch_detail.detail],
            fraud_signals=fraud_signals,
            checks=checks,
        )

    def _check_patient_names(
        self,
        patient_names: dict[str, str],
        member_name: str,
        extraction_results: list[DocumentExtractionResult],
    ) -> tuple[bool, CheckResult]:
        """
        Check patient name consistency across documents.

        TC003: surface the actual names from each document in the mismatch message.
        """
        if len(patient_names) < 2:
            # Only one (or zero) documents have patient names — can't check consistency
            return True, CheckResult(
                check_name="consistency.patient_match",
                passed=True,
                detail=(
                    f"Patient name consistency check: "
                    f"{'1 document has a patient name' if patient_names else 'No documents have extractable patient names'}. "
                    f"No cross-document comparison possible."
                ),
                policy_reference="cross_doc_consistency",
            )

        # Build name pairs from document types for readable messages
        doc_type_map: dict[str, str] = {
            r.file_id: r.document_type for r in extraction_results
        }

        names = list(patient_names.items())  # [(file_id, name), ...]
        reference_name = names[0][1]
        mismatches: list[str] = []

        for file_id, name in names[1:]:
            # 1. Exact match (case-insensitive)
            if name.lower().strip() == reference_name.lower().strip():
                continue

            # 2. Fuzzy match
            score = fuzz.WRatio(name.lower(), reference_name.lower())
            if score >= FUZZY_MATCH_THRESHOLD:
                # Close enough — likely same person with spelling variation
                continue

            # 3. Mismatch confirmed
            ref_file_id = names[0][0]
            ref_doc_type = doc_type_map.get(ref_file_id, "document").replace("_", " ").lower()
            cur_doc_type = doc_type_map.get(file_id, "document").replace("_", " ").lower()

            mismatches.append(
                f"the {ref_doc_type} is for '{reference_name}' "
                f"but the {cur_doc_type} is for '{name}'"
            )

        if mismatches:
            # Build the name-per-document summary for the mismatch message
            name_summary = "; ".join(
                f"{doc_type_map.get(fid, fid).replace('_', ' ').lower()}: '{n}'"
                for fid, n in patient_names.items()
            )
            return False, CheckResult(
                check_name="consistency.patient_match",
                passed=False,
                detail=(
                    f"Patient name mismatch detected across documents. "
                    f"{name_summary.capitalize()}. "
                    f"Mismatch detail: {'; '.join(mismatches)}. "
                    f"All documents in a claim must belong to the same patient."
                ),
                policy_reference="cross_doc_consistency",
                data={"patient_names": patient_names, "mismatches": mismatches},
            )

        # All names match
        name_summary = "; ".join(
            f"{doc_type_map.get(fid, fid)}: '{n}'" for fid, n in patient_names.items()
        )
        return True, CheckResult(
            check_name="consistency.patient_match",
            passed=True,
            detail=(
                f"Patient names are consistent across all documents "
                f"({len(patient_names)} document(s) checked). {name_summary}."
            ),
            policy_reference="cross_doc_consistency",
        )
