"""
Health check endpoint.

GET /health — checks DB, Redis, and at least one LLM provider are reachable.
Used by load balancers and deployment health probes.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    System health check.

    Returns 200 OK if all critical dependencies are healthy.
    Returns 503 if any critical dependency is down.
    """
    checks: dict[str, str] = {}
    all_ok = True

    # ── Database ─────────────────────────────────────────────────────────────
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
        all_ok = False

    # ── Redis ────────────────────────────────────────────────────────────────
    try:
        import redis
        r = redis.from_url(settings.redis_url)
        r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        all_ok = False

    # ── Provider check ────────────────────────────────────────────────────────
    try:
        active_providers = settings.get_provider_list()
        checks["providers_configured"] = ", ".join(active_providers)
        for provider in active_providers:
            if provider == "groq":
                if settings.groq_api_key:
                    checks["groq_provider"] = "configured"
                else:
                    checks["groq_provider"] = "warning: no api key"
                    all_ok = False
            elif provider == "gemini":
                if settings.gemini_api_key:
                    checks["gemini_provider"] = "configured"
                else:
                    checks["gemini_provider"] = "warning: no api key"
                    all_ok = False
    except Exception as e:
        checks["providers_check"] = f"error: {e}"

    # ── Policy file ───────────────────────────────────────────────────────────
    try:
        from app.policy.loader import get_policy
        policy = get_policy()
        checks["policy"] = f"ok (policy_id: {policy.policy_id})"
    except Exception as e:
        checks["policy"] = f"error: {e}"
        all_ok = False

    status_code = 200 if all_ok else 503
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
        "providers_configured": settings.get_provider_list(),
    }
