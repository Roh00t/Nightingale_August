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

from routers import highlights, patient_message, redact, summarize

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

    # Validate critical env vars (warn but do not crash -- allows health checks)
    missing: list[str] = []
    for var in ["GROQ_API_KEY"]:
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
    checks: dict[str, bool] = {
        "groq_api_key": bool(os.environ.get("GROQ_API_KEY")),
        "supabase_url": bool(os.environ.get("SUPABASE_URL")),
        "supabase_key": bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY")),
    }

    all_critical = checks["groq_api_key"]
    status_str = "ready" if all_critical else "not_ready"

    return {"status": status_str, "checks": checks}
