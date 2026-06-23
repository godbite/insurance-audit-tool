"""
Typed Pydantic extraction models — one per document type.

These are what ExtractionAgent produces and PolicyRulesEngine consumes.
Using typed models (not generic blobs) is what enables per-line-item
exclusion logic in TC006 and TC012.
"""
from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


# ─── Shared building blocks ───────────────────────────────────────────────────

class LineItem(BaseModel):
    description: str = ""
    amount: float = 0.0
    excluded: bool = False           # set by decision engine, not extraction
    exclusion_reason: Optional[str] = None

    @field_validator("description", mode="before")
    @classmethod
    def default_description(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)

    @field_validator("amount", mode="before")
    @classmethod
    def default_amount(cls, v: Any) -> float:
        if v is None:
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0


class MedicineItem(BaseModel):
    name: str = ""
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    duration: Optional[str] = None
    batch: Optional[str] = None
    expiry: Optional[str] = None
    quantity: Optional[float] = None
    mrp: Optional[float] = None
    amount: Optional[float] = None

    @field_validator("name", mode="before")
    @classmethod
    def default_name(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)


class TestResult(BaseModel):
    test_name: str
    result: Optional[str] = None
    unit: Optional[str] = None
    normal_range: Optional[str] = None
    is_abnormal: Optional[bool] = None

    @field_validator("test_name", mode="before")
    @classmethod
    def default_test_name(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)


class UnextractedField(BaseModel):
    field_name: str
    reason: str   # e.g. "obscured by stamp", "illegible handwriting", "non-English script"

    @field_validator("field_name", "reason", mode="before")
    @classmethod
    def default_strings(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)



# ─── Per-document-type extraction schemas ─────────────────────────────────────

class PrescriptionExtract(BaseModel):
    """Extracted fields from a doctor's prescription."""
    document_type: Literal["PRESCRIPTION"] = "PRESCRIPTION"

    doctor_name: Optional[str] = None
    doctor_registration: Optional[str] = None
    registration_format_valid: Optional[bool] = None   # True/False/None (unknown)
    clinic_name: Optional[str] = None
    patient_name: Optional[str] = None
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    date: Optional[str] = None    # ISO format preferred
    diagnosis: Optional[str] = None      # expanded form (e.g. "Type 2 Diabetes Mellitus")
    diagnosis_raw: Optional[str] = None  # shorthand as written (e.g. "T2DM")
    medicines: list[MedicineItem] = Field(default_factory=list)
    tests_ordered: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    # Confidence per-field (0.0–1.0)
    field_confidence: dict[str, float] = Field(default_factory=dict)
    unextracted_fields: list[UnextractedField] = Field(default_factory=list)
    # Overall document-level confidence (computed by ExtractionAgent)
    document_confidence: float = 1.0


class HospitalBillExtract(BaseModel):
    """Extracted fields from a hospital / clinic invoice."""
    document_type: Literal["HOSPITAL_BILL"] = "HOSPITAL_BILL"

    hospital_name: Optional[str] = None
    gstin: Optional[str] = None
    bill_number: Optional[str] = None
    date: Optional[str] = None
    patient_name: Optional[str] = None
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Optional[float] = None
    gst_amount: float = 0.0
    total: Optional[float] = None

    # Fraud/alteration signals
    amount_discrepancy_flag: bool = False
    alteration_flag: bool = False
    duplicate_stamp_flag: bool = False

    field_confidence: dict[str, float] = Field(default_factory=dict)
    unextracted_fields: list[UnextractedField] = Field(default_factory=list)
    document_confidence: float = 1.0


class LabReportExtract(BaseModel):
    """Extracted fields from a lab / diagnostic report."""
    document_type: Literal["LAB_REPORT"] = "LAB_REPORT"

    lab_name: Optional[str] = None
    nabl_accredited: Optional[bool] = None
    patient_name: Optional[str] = None
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    referring_doctor: Optional[str] = None
    sample_date: Optional[str] = None
    report_date: Optional[str] = None
    tests: list[TestResult] = Field(default_factory=list)
    pathologist_name: Optional[str] = None
    pathologist_registration: Optional[str] = None
    remarks: Optional[str] = None

    field_confidence: dict[str, float] = Field(default_factory=dict)
    unextracted_fields: list[UnextractedField] = Field(default_factory=list)
    document_confidence: float = 1.0


class PharmacyBillExtract(BaseModel):
    """Extracted fields from a pharmacy / chemist bill."""
    document_type: Literal["PHARMACY_BILL"] = "PHARMACY_BILL"

    pharmacy_name: Optional[str] = None
    drug_license_number: Optional[str] = None
    bill_number: Optional[str] = None
    date: Optional[str] = None
    patient_name: Optional[str] = None
    prescribing_doctor: Optional[str] = None
    medicines: list[MedicineItem] = Field(default_factory=list)
    subtotal: Optional[float] = None
    discount: float = 0.0
    net_amount: Optional[float] = None

    amount_discrepancy_flag: bool = False
    alteration_flag: bool = False

    field_confidence: dict[str, float] = Field(default_factory=dict)
    unextracted_fields: list[UnextractedField] = Field(default_factory=list)
    document_confidence: float = 1.0


class DentalReportExtract(BaseModel):
    """Extracted fields from a dental report."""
    document_type: Literal["DENTAL_REPORT"] = "DENTAL_REPORT"

    dentist_name: Optional[str] = None
    clinic_name: Optional[str] = None
    patient_name: Optional[str] = None
    date: Optional[str] = None
    procedures: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    field_confidence: dict[str, float] = Field(default_factory=dict)
    unextracted_fields: list[UnextractedField] = Field(default_factory=list)
    document_confidence: float = 1.0


class DischargeSummaryExtract(BaseModel):
    """Extracted fields from a hospital discharge summary."""
    document_type: Literal["DISCHARGE_SUMMARY"] = "DISCHARGE_SUMMARY"

    hospital_name: Optional[str] = None
    patient_name: Optional[str] = None
    admission_date: Optional[str] = None
    discharge_date: Optional[str] = None
    diagnosis: Optional[str] = None
    procedures: list[str] = Field(default_factory=list)
    treating_doctor: Optional[str] = None
    notes: Optional[str] = None

    field_confidence: dict[str, float] = Field(default_factory=dict)
    unextracted_fields: list[UnextractedField] = Field(default_factory=list)
    document_confidence: float = 1.0


# ─── Union type used by ExtractionAgent ──────────────────────────────────────

AnyExtract = Union[
    PrescriptionExtract,
    HospitalBillExtract,
    LabReportExtract,
    PharmacyBillExtract,
    DentalReportExtract,
    DischargeSummaryExtract,
]

# Map document type → Pydantic model class (used by ExtractionAgent for schema binding)
EXTRACT_SCHEMA_MAP: dict[str, type[AnyExtract]] = {
    "PRESCRIPTION": PrescriptionExtract,
    "HOSPITAL_BILL": HospitalBillExtract,
    "LAB_REPORT": LabReportExtract,
    "PHARMACY_BILL": PharmacyBillExtract,
    "DENTAL_REPORT": DentalReportExtract,
    "DISCHARGE_SUMMARY": DischargeSummaryExtract,
}


class DocumentExtractionResult(BaseModel):
    """Wraps an extraction result with its document metadata."""
    file_id: str
    document_type: str
    extraction: Optional[AnyExtract] = None
    status: Literal["OK", "DEGRADED", "FAILED"] = "OK"
    provider_used: str = "none"
    confidence: float = 0.0
    error: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}
