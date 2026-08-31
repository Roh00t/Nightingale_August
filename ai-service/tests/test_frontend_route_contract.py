"""
The frontend's endpoint paths must exist on this service.

Nothing enforced that before. The two sides are in different languages and
different build pipelines, so renaming a FastAPI route or mistyping a path in a
component produces a 404 at runtime and nothing at all at build time — and a 404
reads as "that endpoint does not exist", which sends you looking for a missing
route or a broken deployment rather than at the string that was changed.

This walks the actual frontend source, extracts every AI path it calls, and
asserts each resolves to a route this app serves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import main

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

# callAI('/api/ai/x', ...) and aiUrl('x', ...) — the only two ways a path is
# turned into a request.
CALL_PATTERNS = (
    re.compile(r"callAI<[^>]*>\(\s*'([^']+)'"),
    re.compile(r"aiUrl\(\s*'([^']+)'"),
)


def _resolve(path: str) -> str:
    """Mirror of aiUrl()'s normalisation, kept deliberately simple."""
    clean = re.sub(r"^/+", "", path)
    clean = re.sub(r"^api/ai/", "", clean)
    return f"/api/ai/{clean}"


def _called_paths() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for file in FRONTEND.rglob("*.ts*"):
        if "node_modules" in file.parts or ".next" in file.parts:
            continue
        text = file.read_text(encoding="utf-8", errors="ignore")
        for pattern in CALL_PATTERNS:
            for m in pattern.finditer(text):
                found.add((m.group(1), str(file.relative_to(FRONTEND))))
    return found


@pytest.fixture(scope="module")
def served() -> set[str]:
    return set(main.app.openapi()["paths"])


def test_frontend_is_actually_scanned():
    """
    Guards the guard. If the glob stops finding files — a moved directory, a
    renamed helper — every assertion below would pass vacuously while checking
    nothing.
    """
    assert FRONTEND.is_dir(), f"frontend not found at {FRONTEND}"
    calls = _called_paths()
    assert len(calls) >= 5, f"expected several AI call sites, found {len(calls)}: {calls}"


def test_every_called_path_is_served(served):
    missing = [
        (path, resolved, where)
        for path, where in sorted(_called_paths())
        if (resolved := _resolve(path)) not in served
    ]
    assert not missing, "frontend calls endpoints this service does not serve: " + repr(missing)


@pytest.mark.parametrize(
    "endpoint",
    ["conflicts", "summarize", "draft-patient-message", "transcribe",
     "send-patient-message", "retract-patient-message"],
)
def test_named_endpoints_exist(endpoint, served):
    """
    The specific ones the UI depends on, named so a failure says which feature
    broke rather than just that a set differs.
    """
    assert f"/api/ai/{endpoint}" in served


def test_routers_are_mounted_under_the_expected_prefix(served):
    """
    aiUrl() forces every path under /api/ai/. If a router were remounted
    elsewhere, that normalisation would silently build 404s for a whole feature.
    """
    ai_routes = [p for p in served if p.startswith("/api/ai/")]
    assert len(ai_routes) >= 7, f"AI routes moved or disappeared: {sorted(ai_routes)}"
