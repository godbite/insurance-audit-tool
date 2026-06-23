"""
SQLAlchemy 2 async models — mirrors the Pydantic domain models for persistence.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ClaimModel(Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_claims_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    member_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    claimed_amount: Mapped[float] = mapped_column(Float, nullable=False)
    treatment_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="QUEUED")
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    hospital_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    pre_auth_obtained: Mapped[bool] = mapped_column(Boolean, default=False)
    simulate_component_failure: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True)

    # Relationships
    documents: Mapped[list["DocumentModel"]] = relationship(back_populates="claim", cascade="all, delete")
    decision: Mapped[Optional["DecisionModel"]] = relationship(back_populates="claim", uselist=False)
    trace: Mapped[Optional["ClaimTraceModel"]] = relationship(back_populates="claim", uselist=False)


class DocumentModel(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    claim_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_id: Mapped[str] = mapped_column(String(100), nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    declared_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    classified_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    classification_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quality_flag: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    claim: Mapped["ClaimModel"] = relationship(back_populates="documents")
    extraction: Mapped[Optional["ExtractionModel"]] = relationship(
        back_populates="document", uselist=False
    )


class ExtractionModel(Base):
    __tablename__ = "extractions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    document_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    extracted_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    provider_name: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    document: Mapped["DocumentModel"] = relationship(back_populates="extraction")


class DecisionModel(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    claim_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    approved_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reasons_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    checks_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    degradation_notes: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    claim: Mapped["ClaimModel"] = relationship(back_populates="decision")


class ClaimTraceModel(Base):
    __tablename__ = "claim_traces"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    claim_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )
    # Full ClaimTrace JSON — JSONB for queryability
    trace_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    claim: Mapped["ClaimModel"] = relationship(back_populates="trace")
