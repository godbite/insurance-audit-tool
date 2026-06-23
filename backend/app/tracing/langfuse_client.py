"""
Langfuse client wrapper — LLM call observability for engineers.

Two audiences:
  Engineers → Langfuse dashboard (every LLM call, token counts, latencies, prompt versions)
  Ops team  → ClaimTrace JSON (served at /claims/{id}/trace)

This client wraps the Langfuse SDK. All tracing calls are fire-and-forget
(best-effort). A Langfuse failure must NEVER affect claim processing.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


class LangfuseClient:
    """
    Wraps the Langfuse SDK for LLM observability.

    Initialised once at app startup. If Langfuse is unavailable or keys
    are missing, all methods are no-ops — never raises.
    """

    def __init__(
        self,
        public_key: str = "",
        secret_key: str = "",
        host: str = "https://cloud.langfuse.com",
    ):
        self._enabled = bool(public_key and secret_key)
        self._client = None

        if self._enabled:
            try:
                from langfuse import Langfuse
                self._client = Langfuse(
                    public_key=public_key,
                    secret_key=secret_key,
                    host=host,
                )
                log.info("Langfuse client initialised.")
            except ImportError:
                log.warning("langfuse package not installed. Tracing disabled.")
                self._enabled = False
            except Exception as e:
                log.warning(f"Langfuse init failed: {e}. Tracing disabled.")
                self._enabled = False

    def start_trace(self, *, claim_id: str, member_id: str, category: str) -> Optional[str]:
        """Start a Langfuse trace for a claim. Returns trace_id."""
        if not self._enabled or not self._client:
            return None
        try:
            trace = self._client.trace(
                name="claim_pipeline",
                id=claim_id,
                metadata={"member_id": member_id, "category": category},
            )
            return trace.id
        except Exception as e:
            log.warning(f"Langfuse start_trace failed: {e}")
            return None

    def log_generation(
        self,
        *,
        trace_id: str,
        name: str,
        model: str,
        input_text: str,
        output_text: str,
        stage: str,
        document_type: str,
        provider_name: str,
        latency_ms: int,
        usage: Optional[dict] = None,
    ) -> None:
        """Log a single LLM generation (extraction call) to Langfuse."""
        if not self._enabled or not self._client:
            return
        try:
            self._client.generation(
                trace_id=trace_id,
                name=name,
                model=model,
                input=input_text,
                output=output_text,
                metadata={
                    "stage": stage,
                    "document_type": document_type,
                    "provider_name": provider_name,
                },
                usage=usage,
                latency=latency_ms / 1000,  # langfuse expects seconds
            )
        except Exception as e:
            log.warning(f"Langfuse log_generation failed: {e}")

    def log_score(
        self,
        *,
        trace_id: str,
        name: str,
        value: float,
        comment: Optional[str] = None,
    ) -> None:
        """Push a score (e.g. test case pass/fail) back to Langfuse."""
        if not self._enabled or not self._client:
            return
        try:
            self._client.score(
                trace_id=trace_id,
                name=name,
                value=value,
                comment=comment,
            )
        except Exception as e:
            log.warning(f"Langfuse log_score failed: {e}")

    def flush(self) -> None:
        """Flush any pending events (call at shutdown)."""
        if self._enabled and self._client:
            try:
                self._client.flush()
            except Exception:
                pass


def get_langfuse_client() -> LangfuseClient:
    """FastAPI dependency — returns singleton LangfuseClient."""
    from app.core.config import get_settings
    settings = get_settings()
    return LangfuseClient(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
