"""
run_component() — the universal failure boundary.

Every risky call (LLM, OCR, DB, external API) goes through this.
No bare `except: pass` anywhere in the codebase.

Rules enforced here:
- Every catch logs the error.
- Every catch records in the trace (via returned ComponentResult).
- Every catch applies a confidence penalty.
- FAILED (no fallback) → caller pushes decision toward MANUAL_REVIEW.
- No HTTP 500 ever surfaces from this boundary.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional, TypeVar

from app.models.domain import ComponentResult

T = TypeVar("T")
log = logging.getLogger(__name__)


async def run_component(
    fn: Callable[[], Coroutine[Any, Any, T]],
    *,
    component_name: str,
    fallback: Optional[T] = None,
    penalty_on_failure: float = 0.15,
) -> ComponentResult[T]:
    """
    Run an async callable as a protected component.

    Args:
        fn               — async callable to run
        component_name   — identifier for logging + trace
        fallback         — value to use on failure (None → FAILED status)
        penalty_on_failure — confidence deduction on failure

    Returns:
        ComponentResult with status OK, DEGRADED, or FAILED.
    """
    try:
        value = await fn()
        return ComponentResult.ok(value)

    except asyncio.TimeoutError as e:
        error_msg = f"Component '{component_name}' timed out: {e}"
        log.warning(error_msg)
        return _handle_failure(component_name, error_msg, fallback, penalty_on_failure)

    except Exception as e:
        error_msg = f"Component '{component_name}' failed: {type(e).__name__}: {e}"
        log.warning(error_msg, exc_info=True)
        return _handle_failure(component_name, error_msg, fallback, penalty_on_failure)


def _handle_failure(
    component_name: str,
    error_msg: str,
    fallback: Optional[T],
    penalty: float,
) -> ComponentResult[T]:
    if fallback is not None:
        return ComponentResult.degraded(
            value=fallback,
            error=error_msg,
            penalty=penalty,
        )
    return ComponentResult.failed(
        error=error_msg,
        penalty=penalty,
    )
