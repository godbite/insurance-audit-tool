"""
Groq provider adapter.
"""
from __future__ import annotations

import json
import logging
import time
import base64
from typing import Any

from pydantic import BaseModel

from app.providers.base import ExtractionProvider, ProviderResult
from app.models.decision import LLMDecisionExtract

import groq
from langfuse import Langfuse, observe, get_client

log = logging.getLogger(__name__)

class GroqProvider(ExtractionProvider):
    """
    Groq provider.
    Currently used only for decisioning. Vision classification and extraction can be supported via llama-3.2-vision if needed.
    """

    def __init__(self, api_key: str, model_name: str = "llama-3.3-70b-versatile"):
        self._client = groq.AsyncGroq(api_key=api_key)
        self._model_name = model_name
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
        return "groq"

    @observe(as_type="generation")
    async def classify(
        self,
        document_bytes: bytes,
        mime_type: str,
    ) -> tuple[str, float]:
        """Classify document type using local OCR text + Groq text model."""
        start_ms = int(time.time() * 1000)
        from app.providers.ocr import extract_text_from_document
        ocr_text = extract_text_from_document(document_bytes, mime_type)
        
        if not ocr_text:
            log.warning("No OCR text extracted, cannot classify using Groq text model")
            return "UNKNOWN", 0.0

        prompt = self._get_prompt_text(
            "classification_prompt",
            (
                "Classify this document as HOSPITAL_BILL, PRESCRIPTION, LAB_REPORT, PHARMACY_BILL, DISCHARGE_SUMMARY, DENTAL_REPORT, or UNKNOWN. "
                "Guidance: If the document is an invoice, bill, or receipt listing financial charges/costs, classify it as HOSPITAL_BILL (even if from a dental clinic or dentist). "
                "Only classify as DENTAL_REPORT if it is a clinical summary or case sheet without prices/charges. "
                "Output JSON like {'document_type': '...', 'confidence': 0.95}"
            ),
        )
        try:
            response = await self._client.chat.completions.create(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"Extracted Document Text:\n{ocr_text}"}
                ],
                response_format={"type": "json_object"},
            )
            latency_ms = int(time.time() * 1000) - start_ms
            raw_text = response.choices[0].message.content or ""

            # Log model and token usage to Langfuse
            try:
                lf = get_client()
                usage = None
                if hasattr(response, "usage") and response.usage:
                    usage = {
                        "input_tokens": response.usage.prompt_tokens,
                        "output_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
                    }
                lf.update_current_generation(
                    model=self._model_name,
                    usage_details=usage
                )
            except Exception as le:
                log.warning(f"Failed to update Langfuse generation trace: {le}")

            data = json.loads(raw_text)
            doc_type = data.get("document_type", "UNKNOWN")
            confidence = float(data.get("confidence", 0.95))
            log.info(f"👁️ Groq classified doc as {doc_type} in {latency_ms}ms")
            return doc_type, confidence
        except Exception as e:
            log.error("Groq classification failed", extra={"error": str(e)}, exc_info=True)
            return "UNKNOWN", 0.0

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
        """Extract structured data using local OCR text + Groq text model."""
        start_ms = int(time.time() * 1000)
        from app.providers.ocr import extract_text_from_document
        ocr_text = extract_text_from_document(document_bytes, mime_type)
        
        if not ocr_text:
            return ProviderResult(
                raw_response="",
                parsed=None,
                confidence=0.0,
                provider_name=self.name,
                latency_ms=0,
                error="Local OCR extracted no text from the document.",
            )

        system_prompt = self._get_prompt_text(
            "extraction_prompt",
            "Extract structured data from the {document_type}. Output strict JSON matching the schema.",
            document_type=document_type
        )
        json_schema = json.dumps(schema.model_json_schema())
        
        try:
            response = await self._client.chat.completions.create(
                model=self._model_name,
                messages=[
                    {
                        "role": "user",
                        "content": f"{system_prompt}\nSchema: {json_schema}\nContext: {prompt_context}\n\nExtracted Document Text:\n{ocr_text}"
                    }
                ],
                response_format={"type": "json_object"},
            )
            latency_ms = int(time.time() * 1000) - start_ms
            raw_text = response.choices[0].message.content or ""

            # Log model and token usage to Langfuse
            try:
                lf = get_client()
                usage = None
                if hasattr(response, "usage") and response.usage:
                    usage = {
                        "input_tokens": response.usage.prompt_tokens,
                        "output_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
                    }
                lf.update_current_generation(
                    model=self._model_name,
                    usage_details=usage
                )
            except Exception as le:
                log.warning(f"Failed to update Langfuse generation trace: {le}")
            
            try:
                parsed = schema.model_validate_json(raw_text)
                log.info(f"👁️ Groq extracted {document_type} data in {latency_ms}ms")
                return ProviderResult(
                    raw_response=raw_text,
                    parsed=parsed,
                    confidence=0.95,
                    provider_name=self.name,
                    latency_ms=latency_ms,
                )
            except Exception as parse_e:
                log.error("Failed to parse Groq extraction JSON", extra={"error": str(parse_e)}, exc_info=True)
                return ProviderResult(
                    raw_response=raw_text,
                    parsed=None,
                    confidence=0.0,
                    provider_name=self.name,
                    latency_ms=latency_ms,
                    error=str(parse_e),
                )
        except Exception as e:
            latency_ms = int(time.time() * 1000) - start_ms
            log.error("Groq extraction failed", extra={"error": str(e)}, exc_info=True)
            return ProviderResult(
                raw_response="",
                parsed=None,
                confidence=0.0,
                provider_name=self.name,
                latency_ms=latency_ms,
                error=str(e),
            )

    @observe(as_type="generation")
    async def decide(
        self,
        *,
        context_json: str,
    ) -> ProviderResult:
        """Use Groq to decide a claim based on extracted JSON context."""
        start_ms = int(time.time() * 1000)
        try:
            system_prompt = self._get_prompt_text(
                "decision_prompt",
                "You are an insurance claims decision engine. Evaluate the following JSON context and output a strict JSON decision.",
            )

            # Inform Groq about the required schema directly in the prompt
            json_instruction = (
                "\n\nYou MUST output a valid JSON object with the following schema: "
                "{"
                "'decision': 'APPROVED' | 'PARTIAL' | 'REJECTED' | 'MANUAL_REVIEW', "
                "'approved_amount': float, "
                "'line_item_breakdown': [{'description': str, 'claimed_amount': float, 'approved_amount': float, 'status': 'APPROVED'|'REJECTED'|'EXCLUDED', 'reason': str}], "
                "'reasons': [str]"
                "}"
            )
            
            response = await self._client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt + json_instruction},
                    {"role": "user", "content": context_json}
                ],
                model=self._model_name,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            
            latency_ms = int(time.time() * 1000) - start_ms
            raw_text = response.choices[0].message.content or ""
            
            # Log model and token usage to Langfuse
            try:
                lf = get_client()
                usage = None
                if hasattr(response, "usage") and response.usage:
                    usage = {
                        "input_tokens": response.usage.prompt_tokens,
                        "output_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
                    }
                lf.update_current_generation(
                    model=self._model_name,
                    usage_details=usage
                )
            except Exception as le:
                log.warning(f"Failed to update Langfuse generation trace: {le}")

            # Explicitly log so we know it came from the LLM!
            log.info(f"🧠 LLM Engine ({self.name}) successfully generated a decision in {latency_ms}ms!")
            
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
                    "Groq decision failed schema validation",
                    extra={"error": str(parse_err), "raw_text": raw_text},
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
            log.error("Groq decision failed", extra={"error": str(e)})
            return ProviderResult(
                raw_response="",
                parsed=None,
                confidence=0.0,
                provider_name=self.name,
                latency_ms=latency_ms,
                error=str(e),
            )
