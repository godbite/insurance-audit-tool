import json
import time
import base64
import structlog
from pydantic import BaseModel
from typing import Tuple, Optional

from openai import AsyncOpenAI
from langfuse import observe, get_client

from app.providers.base import ExtractionProvider, ProviderResult
from app.core.config import get_settings

log = structlog.get_logger(__name__)

class OpenRouterProvider(ExtractionProvider):
    """
    OpenRouter implementation of the extraction provider.
    Supports Vision models for classify/extract, and text models for decide.
    """

    def __init__(self):
        settings = get_settings()
        self.api_key = settings.openrouter_api_key
        self.model = settings.openrouter_model
        self.client = None
        if self.api_key:
            self.client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=self.api_key,
            )

    @property
    def name(self) -> str:
        return "openrouter"

    def _ensure_client(self):
        if not self.client:
            raise ValueError("OpenRouter API key not configured")

    @observe(as_type="generation")
    async def classify(
        self,
        document_bytes: bytes,
        mime_type: str,
    ) -> Tuple[str, float]:
        """Classify the document type using vision."""
        self._ensure_client()
        start_ms = int(time.time() * 1000)

        base64_image = base64.b64encode(document_bytes).decode("utf-8")
        
        prompt = (
            "You are a document classifier for a health insurance claims system. "
            "Look at the provided document image and classify it into exactly one of these types: "
            "PRESCRIPTION, HOSPITAL_BILL, PHARMACY_BILL, LAB_REPORT, DISCHARGE_SUMMARY, "
            "IDENTITY_PROOF, CLAIM_FORM. "
            "Respond with ONLY a JSON object in this exact format: "
            "{\"document_type\": \"TYPE\", \"confidence\": 0.95}"
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                response_format={"type": "json_object"}
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
                    model=self.model,
                    usage_details=usage
                )
            except Exception as le:
                log.warn("Failed to update Langfuse generation trace", error=str(le))
            
            try:
                data = json.loads(raw_text)
                doc_type = data.get("document_type", "UNKNOWN")
                confidence = float(data.get("confidence", 0.95))
                
                log.info(f"👁️ OpenRouter classified doc as {doc_type} in {latency_ms}ms")
                return doc_type, confidence
            except json.JSONDecodeError:
                log.error("Failed to parse OpenRouter classification JSON")
                return "UNKNOWN", 0.0
                
        except Exception as e:
            log.error("OpenRouter classification failed", extra={"error": str(e)}, exc_info=True)
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
        """Extract structured data from the document using vision."""
        self._ensure_client()
        start_ms = int(time.time() * 1000)

        base64_image = base64.b64encode(document_bytes).decode("utf-8")
        schema_json = schema.model_json_schema()
        
        prompt = (
            f"You are an expert data extractor for health insurance claims.\n"
            f"Extract information from this {document_type} image.\n"
            f"Context: {json.dumps(prompt_context)}\n\n"
            f"You MUST return ONLY a JSON object that strictly adheres to the following JSON schema:\n"
            f"{json.dumps(schema_json, indent=2)}"
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                response_format={"type": "json_object"}
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
                    model=self.model,
                    usage_details=usage
                )
            except Exception as le:
                log.warn("Failed to update Langfuse generation trace", error=str(le))
            
            try:
                parsed = schema.model_validate_json(raw_text)
                log.info(f"👁️ OpenRouter extracted {document_type} data in {latency_ms}ms")
                return ProviderResult(
                    raw_response=raw_text,
                    parsed=parsed,
                    confidence=0.95,
                    provider_name=self.name,
                    latency_ms=latency_ms,
                )
            except Exception as parse_e:
                log.error("Failed to parse OpenRouter extraction JSON", error=str(parse_e))
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
            log.error("OpenRouter extraction failed", extra={"error": str(e)}, exc_info=True)
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
        policy_terms_json: str,
    ) -> ProviderResult:
        """Make a claim decision using text inference. We forward this to Groq for speed!"""
        # We intentionally return an error here so the ProviderRouter 
        # automatically fails over to the next provider in the chain (Groq!)
        log.info("⏭️ OpenRouter skipping decision step, forwarding to Groq!")
        return ProviderResult(
            raw_response="",
            parsed=None,
            confidence=0.0,
            provider_name=self.name,
            latency_ms=0,
            error="Skipping decision on OpenRouter to use Groq fallback"
        )
