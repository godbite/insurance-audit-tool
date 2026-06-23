"""
ProviderRouter — failover chain for LLM extraction.

The caller (ExtractionAgent) never knows which provider answered.
All retry/timeout/fallback logic lives here, not in individual providers.

To add a new provider:
  1. Implement ExtractionProvider protocol in a new file
  2. Add to PROVIDER_REGISTRY below
  3. Add its name to PROVIDER_ORDER in .env
  4. Done — no other code changes needed
"""
from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel

from app.providers.base import ExtractionProvider, ProviderResult

log = logging.getLogger(__name__)

# Registry: provider name → factory function
# Add new providers here to make them available via PROVIDER_ORDER config.
def _build_provider_registry(settings) -> dict[str, "ExtractionProvider"]:
    registry: dict[str, ExtractionProvider] = {}

    # Gemini (primary)
    if settings.gemini_api_key:
        from app.providers.gemini_provider import GeminiProvider
        registry["gemini"] = GeminiProvider(
            api_key=settings.gemini_api_key,
            model_name=settings.gemini_model,
        )

    # OpenAI (optional fallback — add api key to .env to activate)
    if settings.openai_api_key:
        try:
            from app.providers.openai_provider import OpenAIProvider
            registry["openai"] = OpenAIProvider(
                api_key=settings.openai_api_key,
                model_name=settings.openai_model,
            )
        except ImportError:
            log.warning("OpenAI provider requested but openai package not installed.")

    # Groq
    if settings.groq_api_key:
        from app.providers.groq_provider import GroqProvider
        registry["groq"] = GroqProvider(
            api_key=settings.groq_api_key,
            model_name=settings.groq_model,
        )

    # OpenRouter
    from app.providers.openrouter_provider import OpenRouterProvider
    try:
        registry["openrouter"] = OpenRouterProvider()
    except Exception as e:
        log.warning(f"OpenRouter provider unavailable: {e}")

    # Stub (always available — for testing without API keys)
    from app.providers.stub_provider import StubProvider
    registry["stub"] = StubProvider()

    return registry


class ProviderRouter:
    """
    Routes extraction calls through an ordered provider chain with failover.

    Ordering is from config (PROVIDER_ORDER), not hardcoded here.
    Each provider gets one attempt; if it fails (parsed=None, timeout, exception),
    the router moves to the next.

    If all providers fail, returns a ProviderResult with parsed=None — the caller
    (ExtractionAgent) handles this as DEGRADED status.
    """

    def __init__(self, providers: list[ExtractionProvider], timeout_s: int = 45):
        self._providers = providers
        self._timeout_s = timeout_s

    @classmethod
    def from_settings(cls, settings) -> "ProviderRouter":
        registry = _build_provider_registry(settings)
        ordered = []
        for name in settings.get_provider_list():
            if name in registry:
                ordered.append(registry[name])
            else:
                log.warning(f"Provider '{name}' listed in PROVIDER_ORDER but not available.")
        if not ordered:
            log.error("No providers available! Check API keys and PROVIDER_ORDER in .env.")
        return cls(providers=ordered, timeout_s=settings.provider_timeout_s)

    async def extract_with_failover(
        self,
        *,
        document_bytes: bytes,
        mime_type: str,
        document_type: str,
        schema: type[BaseModel],
        prompt_context: dict,
    ) -> ProviderResult:
        """
        Try each provider in order. Return first successful result.
        Falls through to next provider on: timeout, exception, or parsed=None.
        """
        last_error: str | None = None

        for provider in self._providers:
            try:
                result = await asyncio.wait_for(
                    provider.extract(
                        document_bytes=document_bytes,
                        mime_type=mime_type,
                        document_type=document_type,
                        schema=schema,
                        prompt_context=prompt_context,
                    ),
                    timeout=self._timeout_s,
                )
                if result.parsed is not None:
                    log.info(
                        f"Provider '{provider.name}' successfully extracted "
                        f"{document_type} in {result.latency_ms}ms."
                    )
                    return result

                last_error = result.error
                log.warning(
                    f"Provider '{provider.name}' returned unparseable response for "
                    f"{document_type}: {last_error}. Trying next provider."
                )

            except asyncio.TimeoutError:
                last_error = f"Provider '{provider.name}' timed out after {self._timeout_s}s."
                log.warning(last_error)
            except Exception as e:
                last_error = f"Provider '{provider.name}' raised exception: {e}"
                log.error(last_error, exc_info=True)

        # All providers failed
        log.error(
            f"All providers failed for {document_type}. Last error: {last_error}"
        )
        return ProviderResult(
            raw_response="",
            parsed=None,
            confidence=0.0,
            provider_name="none",
            latency_ms=0,
            error=last_error or "All providers failed.",
        )

    async def classify_with_failover(
        self,
        *,
        document_bytes: bytes,
        mime_type: str,
    ) -> tuple[str, float, str]:
        """
        Classify document type using provider chain.

        Returns: (document_type, confidence, provider_name)
        """
        for provider in self._providers:
            try:
                doc_type, confidence = await asyncio.wait_for(
                    provider.classify(document_bytes=document_bytes, mime_type=mime_type),
                    timeout=self._timeout_s,
                )
                if doc_type != "UNKNOWN" or confidence > 0.3:
                    return doc_type, confidence, provider.name
            except (asyncio.TimeoutError, Exception) as e:
                log.warning(f"Classification failed on provider '{provider.name}': {e}")

        return "UNKNOWN", 0.0, "none"

    async def decide_with_failover(
        self,
        *,
        context_json: str,
    ) -> ProviderResult:
        """
        Route decision making to provider chain.
        """
        last_error: str | None = None
        for provider in self._providers:
            try:
                # Add hasattr check just in case stub provider isn't updated
                if not hasattr(provider, "decide"):
                    continue
                result = await asyncio.wait_for(
                    provider.decide(context_json=context_json),
                    timeout=self._timeout_s,
                )
                if result.parsed is not None:
                    return result
                last_error = result.error
            except asyncio.TimeoutError:
                last_error = f"Provider '{provider.name}' timed out."
            except Exception as e:
                last_error = str(e)
                
        return ProviderResult(
            raw_response="",
            parsed=None,
            confidence=0.0,
            provider_name="none",
            latency_ms=0,
            error=last_error or "All providers failed.",
        )
