"""
Unit tests for DocumentVerificationAgent.

TC001: Wrong document type (2 prescriptions for consultation) → MISSING_REQUIRED_DOCUMENT
TC002: Unreadable document → DOCUMENT_UNREADABLE (distinct from TC001 path)
"""
from __future__ import annotations

import pytest

from app.agents.verification_agent import DocumentVerificationAgent
from app.models.domain import ClassifiedDoc


def make_doc(file_id: str, doc_type: str, confidence: float = 0.9, quality: str = "GOOD") -> ClassifiedDoc:
    return ClassifiedDoc(
        file_id=file_id,
        file_name=f"{file_id}.jpg",
        predicted_type=doc_type,
        confidence=confidence,
        quality_flag=quality,
    )


class TestVerificationAgent:
    agent = DocumentVerificationAgent()

    def test_tc001_wrong_document_type(self, policy):
        """TC001: Two prescriptions for CONSULTATION → MISSING_REQUIRED_DOCUMENT."""
        docs = [
            make_doc("F001", "PRESCRIPTION"),
            make_doc("F002", "PRESCRIPTION"),
        ]
        result = self.agent.verify(
            claim_category="CONSULTATION",
            classified_docs=docs,
            policy=policy,
        )

        assert result.ok is False
        assert result.code == "MISSING_REQUIRED_DOCUMENT"
        # Message must name what was uploaded AND what is missing
        assert "prescription" in result.message.lower()
        assert "hospital bill" in result.message.lower() or "hospital_bill" in result.message.lower()

    def test_tc001_message_names_both_uploaded_and_missing(self, policy):
        """TC001: Message must name BOTH what was uploaded and what's needed."""
        docs = [make_doc("F001", "PRESCRIPTION"), make_doc("F002", "PRESCRIPTION")]
        result = self.agent.verify(
            claim_category="CONSULTATION",
            classified_docs=docs,
            policy=policy,
        )
        # Should mention the quantity of prescriptions uploaded
        assert "2" in result.message or "two" in result.message.lower() or "prescription" in result.message.lower()

    def test_tc002_unreadable_document(self, policy):
        """TC002: Unreadable PHARMACY_BILL → DOCUMENT_UNREADABLE, specific file named."""
        docs = [
            make_doc("F003", "PRESCRIPTION", confidence=0.9, quality="GOOD"),
            make_doc("F004", "PHARMACY_BILL", confidence=0.1, quality="UNREADABLE"),
        ]
        result = self.agent.verify(
            claim_category="PHARMACY",
            classified_docs=docs,
            policy=policy,
        )

        assert result.ok is False
        assert result.code == "DOCUMENT_UNREADABLE"
        # Must identify the specific file, not reject the claim
        assert "F004" in result.affected_file_ids
        assert "re-upload" in result.message.lower()
        # Must NOT say "rejected"
        assert "not been rejected" in result.message.lower() or "has not" in result.message.lower()

    def test_tc002_is_distinct_from_tc001(self, policy):
        """TC002 code is DOCUMENT_UNREADABLE, not MISSING_REQUIRED_DOCUMENT."""
        docs = [
            make_doc("F003", "PRESCRIPTION", confidence=0.9, quality="GOOD"),
            make_doc("F004", "PHARMACY_BILL", confidence=0.05, quality="UNREADABLE"),
        ]
        result = self.agent.verify(
            claim_category="PHARMACY",
            classified_docs=docs,
            policy=policy,
        )
        assert result.code == "DOCUMENT_UNREADABLE"
        assert result.code != "MISSING_REQUIRED_DOCUMENT"

    def test_valid_consultation_passes(self, policy):
        """Correct docs for CONSULTATION → OK."""
        docs = [
            make_doc("F007", "PRESCRIPTION"),
            make_doc("F008", "HOSPITAL_BILL"),
        ]
        result = self.agent.verify(
            claim_category="CONSULTATION",
            classified_docs=docs,
            policy=policy,
        )
        assert result.ok is True
        assert result.code == "OK"

    def test_valid_pharmacy_passes(self, policy):
        """Correct docs for PHARMACY → OK."""
        docs = [
            make_doc("F001", "PRESCRIPTION"),
            make_doc("F002", "PHARMACY_BILL"),
        ]
        result = self.agent.verify(
            claim_category="PHARMACY",
            classified_docs=docs,
            policy=policy,
        )
        assert result.ok is True

    def test_dental_only_needs_hospital_bill(self, policy):
        """DENTAL only requires HOSPITAL_BILL (no prescription required)."""
        docs = [make_doc("F001", "HOSPITAL_BILL")]
        result = self.agent.verify(
            claim_category="DENTAL",
            classified_docs=docs,
            policy=policy,
        )
        assert result.ok is True

    def test_missing_multiple_required_docs(self, policy):
        """DIAGNOSTIC requires PRESCRIPTION + LAB_REPORT + HOSPITAL_BILL."""
        docs = [make_doc("F001", "PRESCRIPTION")]  # missing LAB_REPORT and HOSPITAL_BILL
        result = self.agent.verify(
            claim_category="DIAGNOSTIC",
            classified_docs=docs,
            policy=policy,
        )
        assert result.ok is False
        assert result.code == "MISSING_REQUIRED_DOCUMENT"

    def test_empty_upload_fails(self, policy):
        """No documents uploaded → MISSING_REQUIRED_DOCUMENT."""
        result = self.agent.verify(
            claim_category="CONSULTATION",
            classified_docs=[],
            policy=policy,
        )
        assert result.ok is False
        assert result.code == "MISSING_REQUIRED_DOCUMENT"
