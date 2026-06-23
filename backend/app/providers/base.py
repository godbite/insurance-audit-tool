"""
Provider abstraction layer.

Every LLM provider implements ExtractionProvider.
The rest of the system only imports from this module — never a vendor SDK directly.

Adding a new provider:
  1. Create app/providers/my_provider.py
  2. Implement ExtractionProvider protocol
  3. Add it to PROVIDER_REGISTRY in router.py
  4. Add its name to PROVIDER_ORDER in .env

That's it. No other files need to change.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel


@dataclass
class ProviderResult:
    """
    Standardised output from any provider.

    parsed=None means the provider failed or returned unparseable output.
    The caller (ProviderRouter) treats parsed=None as a provider failure
    and tries the next provider in the chain.
    """
    raw_response: str
    parsed: Optional[BaseModel]  # None if parsing failed
    confidence: float            # 0.0–1.0
    provider_name: str
    latency_ms: int
    error: Optional[str] = None
    token_count: Optional[int] = None


@runtime_checkable
class ExtractionProvider(Protocol):
    """
    Protocol that every LLM provider adapter must implement.

    Design intent:
    - The caller never imports a vendor SDK (google-generativeai, openai, etc.) directly.
    - All timeout/retry/fallback logic lives in ProviderRouter, not here.
    - Each adapter is purely: vendor SDK call → translate to ProviderResult.
    """

    @property
    def name(self) -> str:
        """Unique provider identifier, matches PROVIDER_ORDER config value."""
        ...

    async def extract(
        self,
        *,
        document_bytes: bytes,
        mime_type: str,
        document_type: str,
        schema: type[BaseModel],
        prompt_context: dict,
    ) -> ProviderResult:
        """
        Extract structured data from a document.

        Args:
            document_bytes  — raw bytes of the document (image or PDF)
            mime_type       — MIME type (e.g. "image/jpeg", "application/pdf")
            document_type   — DocumentType string (e.g. "PRESCRIPTION")
            schema          — Pydantic model class the response must conform to
            prompt_context  — dict passed to the prompt template (document_type, etc.)

        Returns:
            ProviderResult with parsed=<instance of schema> on success,
            parsed=None on failure (parse error, timeout, network error).
        """
        ...

    async def classify(
        self,
        *,
        document_bytes: bytes,
        mime_type: str,
    ) -> tuple[str, float]:
        """
        Classify a document's type.

        Returns:
            (document_type: str, confidence: float)
        """
        ...

    async def decide(
        self,
        *,
        context_json: str,
    ) -> ProviderResult:
        """
        Evaluate a claim and output a decision via LLM.
        """
        ...


def calculate_cost(model_name: str, input_tokens: int, output_tokens: int) -> dict:
    """Calculate input, output, and total cost in USD for a given model and token count."""
    model_lower = model_name.lower()
    
    # Defaults (in case model is not found)
    input_rate = 0.0
    output_rate = 0.0
    
    # Define pricing per 1 million tokens (in USD)
    if "llama-3.3-70b" in model_lower:
        input_rate = 0.59
        output_rate = 0.79
    elif "llama-3.1-70b" in model_lower:
        input_rate = 0.59
        output_rate = 0.79
    elif "llama-3" in model_lower:  # general fallback for other llama3 models
        input_rate = 0.20
        output_rate = 0.60
    elif "gemini-2.5-flash" in model_lower:
        input_rate = 0.075
        output_rate = 0.30
    elif "gemini-2.0-flash" in model_lower:
        input_rate = 0.075
        output_rate = 0.30
    elif "gemini-1.5-flash" in model_lower:
        input_rate = 0.075
        output_rate = 0.30
    elif "gemini-1.5-pro" in model_lower:
        input_rate = 1.25
        output_rate = 5.00
    elif "gpt-4o" in model_lower:
        input_rate = 2.50
        output_rate = 10.00
    elif "gpt-4-turbo" in model_lower:
        input_rate = 10.00
        output_rate = 30.00
    elif "claude-3-5-sonnet" in model_lower:
        input_rate = 3.00
        output_rate = 15.00
    else:
        # Default fallback (e.g. general cheap model)
        input_rate = 0.15
        output_rate = 0.60

    input_cost = (input_tokens / 1_000_000.0) * input_rate
    output_cost = (output_tokens / 1_000_000.0) * output_rate
    total_cost = input_cost + output_cost
    
    return {
        "input": round(input_cost, 7),
        "output": round(output_cost, 7),
        "total": round(total_cost, 7),
        "currency": "USD"
    }
