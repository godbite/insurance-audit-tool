"""
Gemini provider adapter — primary LLM provider.

Uses gemini-2.0-flash multimodal input (images/PDFs accepted directly).
Requests structured JSON output bound to a Pydantic schema.
Never does retry/timeout logic here — that's the router's job.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import google.generativeai as genai
from pydantic import BaseModel

from app.providers.base import ProviderResult

log = logging.getLogger(__name__)

# ─── Prompt templates per document type ──────────────────────────────────────
# These are the authoritative extraction prompts. In production, these would
# be stored in Langfuse prompt management for version control — the code
# falls back to these in-code defaults if Langfuse is unavailable.

SYSTEM_PROMPT_TEMPLATE = """You are a structured-data extraction engine for Indian medical documents. \
You extract information exactly as written on the document. You never infer values that are not present. \
You never guess a diagnosis, amount, or name. If a field is illegible, missing, torn, obscured by a stamp, \
or in a script you cannot read, you set that field to null and add an entry to `unextracted_fields` \
naming the field and the reason. You are extracting from a {document_type}.

You must respond with JSON matching this schema exactly: {schema_json}

Field-specific rules:
- Diagnoses may use medical shorthand (HTN, T2DM, etc.) — expand to full form AND keep the shorthand in `diagnosis_raw`.
- Doctor registration numbers follow STATE/NNNNN/YYYY or AYUR/STATE/NNNNN/YYYY. If the format doesn't match \
a known state pattern, still extract the raw string and set `registration_format_valid: false` rather than discarding it.
- If amounts appear both in words and figures and they disagree, extract the figure value, \
set `amount_discrepancy_flag: true`, and put both in `notes`.
- If you see multiple "ORIGINAL"/"DUPLICATE" stamps or visible corrections/strikethroughs on amounts, \
set `alteration_flag: true`.
- Rate every extracted field's confidence 0.0-1.0 in `field_confidence`. A field obscured by a stamp or \
handwriting you are guessing at should never score above 0.5.
- For multilingual content (Hindi/Tamil/Telugu mixed with English): extract English fields normally; \
non-English-only fields go to `unextracted_fields` with a note — do not attempt translation.
- If the overall document quality is very poor (dark, blurry, heavily skewed), still extract what you can \
and set low confidence scores appropriately."""

USER_PROMPT = "Extract all fields for this {document_type} per the schema and rules above."

CLASSIFICATION_SYSTEM_PROMPT = """You are a document classifier for Indian medical insurance claims. \
Classify the uploaded document into exactly one of these types:
PRESCRIPTION, HOSPITAL_BILL, LAB_REPORT, PHARMACY_BILL, DENTAL_REPORT, DISCHARGE_SUMMARY, UNKNOWN

Respond with JSON: {"document_type": "<TYPE>", "confidence": <0.0-1.0>, "reason": "<brief reason>"}

If you cannot determine the type or the document is too unclear to classify, use UNKNOWN with low confidence."""


from langfuse import observe, Langfuse
from langfuse.decorators import langfuse_context

class GeminiProvider:
    """
    Gemini multimodal extraction and classification provider.

    Primary provider in the ProviderRouter chain.
    Accepts images (JPEG, PNG, WEBP) and PDFs natively — no OCR pre-processing needed.
    """

    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash"):
        genai.configure(api_key=api_key)
        self._model_name = model_name
        self._model = genai.GenerativeModel(model_name)
        self._classify_model = genai.GenerativeModel(model_name)
        try:
            self.langfuse = Langfuse()
        except Exception as e:
            log.warning(f"Could not initialize Langfuse client for prompts: {e}")
            self.langfuse = None

    def _get_prompt_text(self, name: str, fallback: str, **kwargs) -> str:
        if self.langfuse:
            try:
                prompt_obj = self.langfuse.get_prompt(name)
                return prompt_obj.compile(**kwargs)
            except Exception as e:
                log.warning(f"Failed to fetch prompt '{name}' from Langfuse, using fallback. Error: {e}")
        return fallback.format(**kwargs) if kwargs else fallback

    @property
    def name(self) -> str:
        return "gemini"

    @observe(as_type="generation")
    async def extract(
        self,
        *,
        document_bytes: bytes,
        mime_type: str,
        document_type: str,
        schema: type[BaseModel],
        prompt_context: dict,
    ) -> ProviderResult:
        """Extract structured data from a document using Gemini multimodal."""
        start_ms = int(time.time() * 1000)

        try:
            schema_json = json.dumps(schema.model_json_schema(), indent=2)
            system_prompt = self._get_prompt_text(
                "extraction_system_prompt",
                SYSTEM_PROMPT_TEMPLATE,
                document_type=document_type,
                schema_json=schema_json,
            )
            user_prompt = self._get_prompt_text(
                "extraction_user_prompt",
                USER_PROMPT,
                document_type=document_type,
            )

            from app.providers.ocr import extract_text_from_document
            ocr_text = extract_text_from_document(document_bytes, mime_type)
            
            if ocr_text:
                log.info(f"Using local OCR text for Gemini extraction of {document_type}")
                user_prompt = f"Here is the text extracted from the document:\n{ocr_text}\n\n{user_prompt}"
                inputs = [system_prompt, user_prompt]
            else:
                log.info(f"Falling back to Gemini vision for {document_type}")
                doc_part = {"inline_data": {"mime_type": mime_type, "data": document_bytes}}
                inputs = [system_prompt, doc_part, user_prompt]

            response = await self._model.generate_content_async(
                inputs,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.0,  # deterministic extraction
                ),
            )

            raw_text = response.text.strip()
            latency_ms = int(time.time() * 1000) - start_ms

            # Log model and token usage to Langfuse
            try:
                usage = None
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    usage = {
                        "input": response.usage_metadata.prompt_token_count,
                        "output": response.usage_metadata.candidates_token_count,
                        "total": response.usage_metadata.total_token_count
                    }
                langfuse_context.update_current_observation(
                    model=self._model_name,
                    usage=usage
                )
            except Exception as le:
                log.warning(f"Failed to update Langfuse generation trace: {le}")

            # Parse JSON → validate against Pydantic schema
            try:
                data = json.loads(raw_text)
                parsed = schema.model_validate(data)
                confidence = self._compute_confidence(parsed)
                return ProviderResult(
                    raw_response=raw_text,
                    parsed=parsed,
                    confidence=confidence,
                    provider_name=self.name,
                    latency_ms=latency_ms,
                )
            except Exception as parse_err:
                log.warning(
                    "Gemini response failed schema validation",
                    extra={"error": str(parse_err), "document_type": document_type},
                )
                return ProviderResult(
                    raw_response=raw_text,
                    parsed=None,
                    confidence=0.0,
                    provider_name=self.name,
                    latency_ms=latency_ms,
                    error=f"Schema validation failed: {parse_err}",
                )

        except Exception as e:
            latency_ms = int(time.time() * 1000) - start_ms
            log.error("Gemini extraction failed", extra={"error": str(e)})
            return ProviderResult(
                raw_response="",
                parsed=None,
                confidence=0.0,
                provider_name=self.name,
                latency_ms=latency_ms,
                error=str(e),
            )

    @observe(as_type="generation")
    async def classify(
        self,
        *,
        document_bytes: bytes,
        mime_type: str,
    ) -> tuple[str, float]:
        """Classify document type using Gemini vision or local OCR text."""
        try:
            system_prompt = self._get_prompt_text(
                "classification_system_prompt",
                CLASSIFICATION_SYSTEM_PROMPT
            )
            
            from app.providers.ocr import extract_text_from_document
            ocr_text = extract_text_from_document(document_bytes, mime_type)
            
            if ocr_text:
                log.info("Using local OCR text for Gemini classification")
                user_prompt = f"Here is the text extracted from the document:\n{ocr_text}\n\nPlease classify it."
                inputs = [system_prompt, user_prompt]
            else:
                log.info("Falling back to Gemini vision for classification")
                doc_part = {"inline_data": {"mime_type": mime_type, "data": document_bytes}}
                inputs = [system_prompt, doc_part]

            response = await self._classify_model.generate_content_async(
                inputs,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )

            # Log model and token usage to Langfuse
            try:
                usage = None
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    usage = {
                        "input": response.usage_metadata.prompt_token_count,
                        "output": response.usage_metadata.candidates_token_count,
                        "total": response.usage_metadata.total_token_count
                    }
                langfuse_context.update_current_observation(
                    model=self._model_name,
                    usage=usage
                )
            except Exception as le:
                log.warning(f"Failed to update Langfuse generation trace: {le}")

            data = json.loads(response.text.strip())
            return data.get("document_type", "UNKNOWN"), float(data.get("confidence", 0.5))
        except Exception as e:
            log.error("Gemini classification failed", extra={"error": str(e)})
            return "UNKNOWN", 0.0

    @observe(as_type="generation")
    async def decide(
        self,
        *,
        context_json: str,
    ) -> ProviderResult:
        """Use Gemini to decide a claim based on extracted JSON context."""
        start_ms = int(time.time() * 1000)
        try:
            from app.models.decision import LLMDecisionExtract
            schema_json = json.dumps(LLMDecisionExtract.model_json_schema(), indent=2)
            system_prompt = self._get_prompt_text(
                "decision_prompt",
                (
                    "You are an insurance claims decision engine. Evaluate the following JSON context and "
                    f"output a strict JSON decision matching this schema:\n{schema_json}"
                ),
            )
            
            response = await self._model.generate_content_async(
                [system_prompt, context_json],
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            
            latency_ms = int(time.time() * 1000) - start_ms
            raw_text = response.text.strip()
            
            try:
                parsed = LLMDecisionExtract.model_validate_json(raw_text)
                return ProviderResult(
                    raw_response=raw_text,
                    parsed=parsed,
                    confidence=1.0,
                    provider_name=self.name,
                    latency_ms=latency_ms,
                    error=None,
                )
            except Exception as parse_err:
                log.error(
                    "Gemini decision failed schema validation",
                    extra={"error": str(parse_err)},
                )
                return ProviderResult(
                    raw_response=raw_text,
                    parsed=None,
                    confidence=0.0,
                    provider_name=self.name,
                    latency_ms=latency_ms,
                    error=f"Schema validation failed: {parse_err}",
                )

        except Exception as e:
            latency_ms = int(time.time() * 1000) - start_ms
            log.error("Gemini decision failed", extra={"error": str(e)})
            return ProviderResult(
                raw_response="",
                parsed=None,
                confidence=0.0,
                provider_name=self.name,
                latency_ms=latency_ms,
                error=str(e),
            )

    @staticmethod
    def _compute_confidence(parsed: BaseModel) -> float:
        """
        Compute per-document confidence from:
        1. Model-reported field_confidence (average across fields)
        2. Schema completeness (fraction of non-null fields)
        """
        confidence_scores: list[float] = []

        # Signal 1: model-reported field confidence
        if hasattr(parsed, "field_confidence") and parsed.field_confidence:
            avg_field_confidence = sum(parsed.field_confidence.values()) / len(parsed.field_confidence)
            confidence_scores.append(avg_field_confidence)

        # Signal 2: schema completeness
        data = parsed.model_dump()
        exclude_keys = {"document_type", "field_confidence", "unextracted_fields"}
        total_fields = 0
        populated_fields = 0
        for k, v in data.items():
            if k in exclude_keys:
                continue
            total_fields += 1
            if v is not None and v != [] and v != {}:
                populated_fields += 1

        if total_fields > 0:
            completeness = populated_fields / total_fields
            confidence_scores.append(completeness)

        # Signal 3: unextracted fields penalty
        if hasattr(parsed, "unextracted_fields") and parsed.unextracted_fields:
            penalty = min(0.3, len(parsed.unextracted_fields) * 0.05)
            confidence_scores.append(max(0.0, 1.0 - penalty))

        if not confidence_scores:
            return 0.7  # reasonable default if no signals

        return round(sum(confidence_scores) / len(confidence_scores), 3)
