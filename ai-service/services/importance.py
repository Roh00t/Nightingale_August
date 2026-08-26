"""
Self-learning importance scoring for clinical highlights.

Computes a composite importance score by blending:
- Recency weight (0.3): more recent entries score higher
- Risk level weight (0.3): critical > high > medium > low
- Unresolved action weight (0.2): items without resolution get a boost
- Learned weight (0.2): boosted by historical clinician engagement
  with similar content, queried from the interaction_log table in Supabase

The learned weight enables the system to adapt over time as clinicians
interact with (click, acknowledge, act on) certain types of highlights.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Weight configuration
# ---------------------------------------------------------------------------

RECENCY_WEIGHT = 0.3
RISK_LEVEL_WEIGHT = 0.3
UNRESOLVED_ACTION_WEIGHT = 0.2
LEARNED_WEIGHT = 0.2

RISK_LEVEL_SCORES: dict[str, float] = {
    "critical": 1.0,
    "high": 0.8,
    "medium": 0.5,
    "low": 0.2,
}

# Action type weights matching the actual interaction_log schema
ACTION_TYPE_WEIGHTS: dict[str, float] = {
    "accept": 1.0,
    "manual_highlight": 0.8,
    "comment": 0.7,
    "pin": 0.7,
    "edit": 0.5,
    "view": 0.3,
    "reject": -0.3,
    "dismiss": -0.2,
    "unpin": 0.0,
}

# Keywords that indicate unresolved actions in clinical text
_UNRESOLVED_KEYWORDS = {
    "pending", "monitor", "follow up", "follow-up", "reassess",
    "unresolved", "continue", "review", "escalate", "refer",
    "outstanding", "awaiting", "to be", "tbd", "scheduled",
}

# ---------------------------------------------------------------------------
# Supabase client (lazy singleton)
# ---------------------------------------------------------------------------

_supabase_client: Any | None = None


def _get_supabase() -> Any:
    """Lazy-initialize the Supabase client."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        logger.warning(
            "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set. "
            "Learned weight scoring will be disabled."
        )
        return None

    from supabase import create_client

    _supabase_client = create_client(url, key)
    logger.info("Supabase client initialized for importance scoring")
    return _supabase_client


# ---------------------------------------------------------------------------
# Component scoring functions
# ---------------------------------------------------------------------------


def _compute_recency_score(created_at: str | datetime | None) -> float:
    """
    Score based on how recent the entry is.
    Entries within 24h get 1.0, decaying to 0.1 over 30 days.
    """
    if created_at is None:
        return 0.5

    if isinstance(created_at, str):
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return 0.5
    else:
        dt = created_at

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    age_hours = max(0, (now - dt).total_seconds() / 3600)

    if age_hours <= 24:
        return 1.0
    elif age_hours <= 72:
        return 0.8
    elif age_hours <= 168:  # 7 days
        return 0.6
    elif age_hours <= 336:  # 14 days
        return 0.4
    elif age_hours <= 720:  # 30 days
        return 0.2
    else:
        return 0.1


def _compute_risk_score(risk_level: str) -> float:
    """Map risk level string to a numeric score."""
    return RISK_LEVEL_SCORES.get(risk_level.lower().strip(), 0.5)


def _compute_unresolved_score(content: str) -> float:
    """
    Check whether the content contains indicators of unresolved actions.
    Returns 1.0 if unresolved signals are found, 0.0 otherwise.
    """
    if not content:
        return 0.0

    content_lower = content.lower()
    matches = sum(1 for kw in _UNRESOLVED_KEYWORDS if kw in content_lower)

    if matches >= 3:
        return 1.0
    elif matches >= 2:
        return 0.8
    elif matches >= 1:
        return 0.5
    return 0.0


def _extract_keywords(text: str) -> set[str]:
    """Extract simple lowercase keywords from text for topic overlap matching."""
    words = re.findall(r"[a-z]{3,}", text.lower())
    # Filter out very common words
    stopwords = {
        "the", "and", "was", "for", "that", "with", "this", "from",
        "are", "were", "been", "have", "has", "had", "not", "but",
        "what", "all", "can", "her", "his", "one", "our", "out",
        "also", "into", "its", "may", "than", "then", "them",
        "some", "she", "him", "how", "did", "who", "will",
    }
    return set(words) - stopwords


async def _compute_learned_score(
    content: str,
    patient_id: str | None = None,
) -> float:
    """
    Query interaction_log in Supabase for similar content patterns.

    Uses the actual schema fields: action_type, target_type, target_id,
    target_metadata (JSONB with optional keywords field).
    Groups interactions by target and computes weighted scores based on
    action_type weights.
    """
    client = _get_supabase()
    if client is None:
        return 0.5  # Neutral default when Supabase is unavailable

    keywords = _extract_keywords(content)
    if not keywords:
        return 0.5

    try:
        # Query recent interaction logs for highlight engagement data
        query = (
            client.table("interaction_log")
            .select("action_type, target_type, target_id, target_metadata")
            .eq("target_type", "highlight")
            .order("created_at", desc=True)
            .limit(200)
        )

        response = query.execute()
        rows = response.data if response.data else []

        if not rows:
            return 0.5

        # Group interactions by target and compute weighted engagement
        target_scores: dict[str, float] = {}
        target_keyword_overlap: dict[str, float] = {}

        for row in rows:
            action_type = row.get("action_type", "view")
            target_id = row.get("target_id", "")
            metadata = row.get("target_metadata") or {}

            # Extract keywords from target_metadata
            stored_keywords_raw = metadata.get("keywords", [])
            if isinstance(stored_keywords_raw, list):
                stored_keywords = set(stored_keywords_raw)
            elif isinstance(stored_keywords_raw, str):
                stored_keywords = set(stored_keywords_raw.split(","))
            else:
                stored_keywords = set()

            # If no stored keywords, use a small default overlap
            if stored_keywords:
                overlap = keywords & stored_keywords
                if not overlap:
                    continue
                overlap_ratio = len(overlap) / max(len(keywords), 1)
            else:
                overlap_ratio = 0.2

            # Get action weight
            type_multiplier = ACTION_TYPE_WEIGHTS.get(action_type, 0.3)

            # Accumulate per-target
            if target_id not in target_scores:
                target_scores[target_id] = 0.0
                target_keyword_overlap[target_id] = 0.0

            target_scores[target_id] += type_multiplier
            target_keyword_overlap[target_id] = max(
                target_keyword_overlap[target_id], overlap_ratio
            )

        if not target_scores:
            return 0.5

        # Compute overlap-weighted total score
        total_score = 0.0
        total_weight = 0.0

        for target_id, score in target_scores.items():
            overlap = target_keyword_overlap[target_id]
            total_score += overlap * score
            total_weight += overlap

        if total_weight == 0:
            return 0.5

        raw = total_score / total_weight
        # Normalize to 0.0-1.0 range using a soft cap
        normalized = min(1.0, raw / 5.0)
        return max(0.0, normalized)

    except Exception:
        logger.exception("Failed to query interaction_log for learned weight")
        return 0.5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_importance_score(
    content: str,
    risk_level: str = "medium",
    created_at: str | datetime | None = None,
    patient_id: str | None = None,
) -> float:
    """
    Compute the composite importance score for a clinical highlight.

    Formula:
        score = recency_weight(0.3) + risk_level_weight(0.3)
              + unresolved_action_weight(0.2) + learned_weight(0.2)

    Args:
        content: The highlight text (can be redacted).
        risk_level: One of 'critical', 'high', 'medium', 'low'.
        created_at: ISO timestamp or datetime of the source entry.
        patient_id: Optional patient ID for patient-specific learning.

    Returns:
        Float between 0.0 and 1.0.
    """
    recency = _compute_recency_score(created_at)
    risk = _compute_risk_score(risk_level)
    unresolved = _compute_unresolved_score(content)
    learned = await _compute_learned_score(content, patient_id)

    score = (
        RECENCY_WEIGHT * recency
        + RISK_LEVEL_WEIGHT * risk
        + UNRESOLVED_ACTION_WEIGHT * unresolved
        + LEARNED_WEIGHT * learned
    )

    # Clamp to [0.0, 1.0]
    final = max(0.0, min(1.0, score))

    logger.debug(
        "Importance score=%.3f (recency=%.2f, risk=%.2f, unresolved=%.2f, learned=%.2f)",
        final,
        recency,
        risk,
        unresolved,
        learned,
    )

    return round(final, 3)


async def batch_score(
    highlights: list[dict[str, Any]],
    patient_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Compute importance scores for a batch of highlights in place.

    Each highlight dict should have 'content_snippet', 'risk_level', and
    optionally 'created_at'. The function adds/overwrites 'importance_score'.

    Returns the same list with updated scores.
    """
    for highlight in highlights:
        score = await compute_importance_score(
            content=highlight.get("content_snippet", ""),
            risk_level=highlight.get("risk_level", "medium"),
            created_at=highlight.get("created_at"),
            patient_id=patient_id,
        )
        highlight["importance_score"] = score

    return highlights
