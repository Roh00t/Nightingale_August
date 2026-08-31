"""
Nightingale AI Service - FastAPI application entry point.

This microservice provides:
- PHI redaction using regex-based pattern matching
- Clinical summarization via Groq LLM
- Highlight extraction with self-learning importance scoring

All PHI is stripped before any content reaches the LLM. Redaction maps
are kept server-side and never exposed to external clients.

Usage:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
import os
import sys
import time

from pathlib import Path
from dotenv import load_dotenv

# Load environment from root .env file (one level up from ai-service/)
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from routers import (
    auth_otp,
    conflicts,
    highlights,
    messaging,
    patient_message,
    redact,
    scribe,
    summarize,
    transcribe,
)

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)

# Attach the PHI scrubber to the ROOT logger immediately after basicConfig and
# before anything else can log. Root, not "nightingale.ai": the records most
# likely to carry raw patient text are the ones this codebase did not write —
# uvicorn's access log rendering a query string, or a library traceback quoting
# the value that broke it. A filter on the application logger would miss both.
#
# This is defence in depth behind services/redaction.py, not a replacement for
# it. Redaction is precise and runs before the LLM; this is a blunt regex net on
# the way to a log sink, which has different retention and a wider audience than
# the database.
from services.log_scrubbing import install as install_log_scrubbing  # noqa: E402

install_log_scrubbing()

logger = logging.getLogger("nightingale.ai")


# ---------------------------------------------------------------------------
# Application lifespan (startup / shutdown hooks)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Runs on application startup and shutdown.

    Startup: validates required environment variables and pre-warms the
    Presidio analyzer so the first request is not penalised.

    Shutdown: cleanup tasks.
    """
    logger.info("Nightingale AI service starting up")

    # Load spaCy + Presidio now rather than on the first request. The model load
    # dominates cold-start; a request that pays it can time out.
    try:
        from services.redaction import warmup

        warmup()
        logger.info("Presidio + spaCy analyzer warmed")
    except Exception:
        logger.exception("Redaction warmup failed - PHI endpoints will error until fixed")

    # Validate critical env vars (warn but do not crash -- allows health checks)
    missing: list[str] = []
    for var in ["GROQ_API_KEY", "SUPABASE_SERVICE_ROLE_KEY"]:
        if not os.environ.get(var):
            missing.append(var)

    if missing:
        logger.warning(
            "Missing environment variables: %s. "
            "Some endpoints will return 503 until these are set.",
            ", ".join(missing),
        )

    yield  # Application runs here

    logger.info("Nightingale AI service shutting down")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Nightingale AI Service",
    description=(
        "AI microservice for the Nightingale home healthcare platform. "
        "Provides PHI-safe clinical summarization, highlight extraction, "
        "and regex-based text redaction powered by Groq."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------

ALLOWED_ORIGINS = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://localhost:8080",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# ---------------------------------------------------------------------------
# Request timing middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def add_timing_header(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
    """Add X-Process-Time header to every response for observability."""
    start = time.perf_counter()
    response: Response = await call_next(request)
    elapsed = time.perf_counter() - start
    response.headers["X-Process-Time"] = f"{elapsed:.4f}"
    return response


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled exceptions. Logs the full traceback."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal server error occurred. Please try again later.",
            "error_type": type(exc).__name__,
        },
    )


# ---------------------------------------------------------------------------
# Mount routers
# ---------------------------------------------------------------------------

app.include_router(summarize.router)
app.include_router(highlights.router)
app.include_router(redact.router)
app.include_router(patient_message.router)
app.include_router(scribe.router)
app.include_router(conflicts.router)
app.include_router(transcribe.router)
app.include_router(messaging.router)
app.include_router(auth_otp.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    tags=["system"],
    summary="Health check",
    response_model=dict[str, str],
)
async def health_check() -> dict[str, str]:
    """
    Basic health check endpoint.

    Returns 200 if the service is running. Does not validate downstream
    dependencies (use /ready for that).
    """
    return {"status": "healthy", "service": "nightingale-ai"}


@app.get(
    "/ready",
    tags=["system"],
    summary="Readiness check",
    response_model=dict[str, object],
)
async def readiness_check() -> dict[str, object]:
    """
    Readiness check that validates downstream dependencies.

    Checks:
    - GROQ_API_KEY is configured
    - Supabase credentials are configured (optional)
    """
    redaction_ready = False
    try:
        from services.redaction import get_analyzer

        get_analyzer()
        redaction_ready = True
    except Exception:
        logger.exception("Redaction engine unavailable")

    checks: dict[str, bool] = {
        "groq_api_key": bool(os.environ.get("GROQ_API_KEY")),
        "supabase_url": bool(
            os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        ),
        "supabase_service_key": bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY")),
        "jwt_verification": bool(
            os.environ.get("SUPABASE_JWT_JWK") or os.environ.get("SUPABASE_JWT_SECRET")
        ),
        "redaction_engine": redaction_ready,
    }

    # Redaction readiness is critical: without it the service cannot guarantee
    # PHI is stripped, so it must not report ready.
    all_critical = checks["groq_api_key"] and checks["redaction_engine"]
    status_str = "ready" if all_critical else "not_ready"

    return {"status": status_str, "checks": checks}
