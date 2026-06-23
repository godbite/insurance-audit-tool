"""
Stub provider — used for testing and as a template for new providers.

Demonstrates the pluggability of the provider architecture:
Adding a real provider = copy this file, replace the stub logic
with the real SDK calls, add to PROVIDER_REGISTRY in router.py.

In tests, configure PROVIDER_ORDER=stub to use this without API keys.
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel

from app.providers.base import ProviderResult

log = logging.getLogger(__name__)

# When PROVIDER_ORDER=stub,gemini — stub goes first.
# This lets tests inject predetermined responses.
_STUB_RESPONSES: dict[str, dict] = {}


def register_stub_response(document_type: str, response: dict) -> None:
    """Test helper — register a canned response for a document type."""
    _STUB_RESPONSES[document_type] = response


def clear_stub_responses() -> None:
    _STUB_RESPONSES.clear()


class StubProvider:
    """
    Stub provider for testing.
    Returns pre-registered responses or minimal defaults.

    Also serves as a template for new provider implementations.
    To add a real provider (e.g. Anthropic):
      1. Copy this file to anthropic_provider.py
      2. Replace the stub response logic with Anthropic API calls
      3. Register in router.py PROVIDER_REGISTRY
      4. Add "anthropic" to PROVIDER_ORDER in .env
    """

    @property
    def name(self) -> str:
        return "stub"

    async def extract(
        self,
        *,
        document_bytes: bytes,
        mime_type: str,
        document_type: str,
        schema: type[BaseModel],
        prompt_context: dict,
    ) -> ProviderResult:
        """Return pre-registered stub response or fail gracefully."""
        if document_type in _STUB_RESPONSES:
            try:
                parsed = schema.model_validate(_STUB_RESPONSES[document_type])
                return ProviderResult(
                    raw_response=json.dumps(_STUB_RESPONSES[document_type]),
                    parsed=parsed,
                    confidence=0.95,
                    provider_name=self.name,
                    latency_ms=10,
                )
            except Exception as e:
                return ProviderResult(
                    raw_response="",
                    parsed=None,
                    confidence=0.0,
                    provider_name=self.name,
                    latency_ms=10,
                    error=f"Stub schema validation failed: {e}",
                )

        # No registered response — return failure so router tries next provider
        return ProviderResult(
            raw_response="",
            parsed=None,
            confidence=0.0,
            provider_name=self.name,
            latency_ms=5,
            error="No stub response registered for this document type.",
        )

    async def classify(
        self,
        *,
        document_bytes: bytes,
        mime_type: str,
    ) -> tuple[str, float]:
        return "UNKNOWN", 0.0
