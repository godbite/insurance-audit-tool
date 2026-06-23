"""
FastAPI application — startup, shutdown, and router assembly.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import claims, health, websocket
from app.core.config import get_settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    settings = get_settings()

    # ── Startup ───────────────────────────────────────────────────────────────
    log.info(f"Starting Plum Claims Backend (env={settings.app_env})")

    # Eagerly load and validate policy — fail fast if file is missing or invalid
    try:
        from app.policy.loader import load_policy
        policy = load_policy(settings.policy_file_path)
        log.info(f"Policy loaded: {policy.policy_id} ({len(policy.members)} members)")
    except Exception as e:
        log.error(f"FATAL: Policy failed to load: {e}")
        raise

    # Initialise Langfuse client
    try:
        from app.tracing.langfuse_client import LangfuseClient
        langfuse = LangfuseClient(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        app.state.langfuse = langfuse
        log.info("Langfuse client initialised.")
    except Exception as e:
        log.warning(f"Langfuse init failed (non-fatal): {e}")

    # Initialise provider router
    try:
        from app.providers.router import ProviderRouter
        router = ProviderRouter.from_settings(settings)
        app.state.provider_router = router
        log.info(f"Provider router initialised: {settings.get_provider_list()}")
    except Exception as e:
        log.warning(f"Provider router init failed (non-fatal): {e}")

    yield  # Application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("Shutting down Plum Claims Backend.")
    if hasattr(app.state, "langfuse"):
        app.state.langfuse.flush()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Plum Health Insurance Claims Processing API",
        description=(
            "Multi-agent AI pipeline for automated health insurance claim processing. "
            "Supports document verification, LLM extraction, policy rules, and full trace observability."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url="/redoc" if settings.app_env != "production" else None,
    )

    # CORS — allow frontend origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:5173", "*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # Strip 'input' and 'ctx' from the error details to avoid serializing file bytes
        errors = exc.errors()
        for error in errors:
            error.pop("input", None)
            error.pop("ctx", None)
        return JSONResponse(
            status_code=422,
            content={"detail": errors},
        )

    # Routers
    app.include_router(claims.router)
    app.include_router(websocket.router)
    app.include_router(health.router)

    return app


app = create_app()
