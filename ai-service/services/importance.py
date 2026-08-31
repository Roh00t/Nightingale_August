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

# ABSOLUTE floor by severity. Applied after scoring; a higher score is kept.
#
# Only `critical`. A flat floor destroys ordering information at the boundary —
# every floored item lands on exactly the same number, so learning can no longer
# distinguish between them — and that is an acceptable price for `critical`,
# which is rare and must always be visible, but not for `high`, which is common
# enough that flattening it would make the loop inert across much of the corpus.
# `high` gets the relative floor below instead.
# The value _compute_learned_score returns when a clinic has no history for a
# topic — neither promoted nor buried. Used as the reference point for the
# no-demotion rule, so "unlearned" means the same thing as "never seen before".
NEUTRAL_LEARNED_SCORE = 0.5

ABSOLUTE_FLOOR: dict[str, float] = {
    "critical": 0.90,
}

# Severities where learning may raise a score but never lower it.
#
# The guarantee is different in kind from the absolute floor and is the more
# useful one: the score can never fall below what severity, recency and
# unresolved status already justify on their own. Engagement history is allowed
# to promote such an item and is structurally unable to demote it — so repeated
# dismissal stops being able to bury an allergy warning, while pinning still
# moves it up and relative ordering among high-risk items survives intact.
NO_DEMOTION_SEVERITIES: frozenset[str] = frozenset({"critical", "high"})

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
# Interaction source (injectable)
# ---------------------------------------------------------------------------
# The learned weight is derived from interaction_log. Production reads it from
# Supabase with the service-role key; tests inject a reader backed by the
# ephemeral Postgres cluster. Injecting the source is what lets
# test_self_learning_importance exercise the real scoring path instead of
# asserting on seed rows.
#
# The reader is ALWAYS called with a clinic_id. The previous implementation
# accepted a patient_id, ignored it, and queried the 200 most recent rows
# globally through a service-role client that bypasses RLS — so clinician
# behaviour at one clinic shifted scores at another (guardrails D4).

from typing import Callable, Protocol


class InteractionSource(Protocol):
    """Returns recent highlight interactions for one clinic."""

    def __call__(self, clinic_id: str | None, limit: int = 200) -> list[dict[str, Any]]:
        ...


_supabase_client: Any | None = None
_interaction_source: InteractionSource | None = None


def set_interaction_source(source: InteractionSource | None) -> None:
    """Override where learned weights are read from. Pass None to restore default."""
    global _interaction_source
    _interaction_source = source


def _get_supabase() -> Any:
    """Lazy-initialize the Supabase client."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
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


def _default_source(clinic_id: str | None, limit: int = 200) -> list[dict[str, Any]]:
    """Read interaction_log from Supabase, scoped to one clinic."""
    client = _get_supabase()
    if client is None:
        return []

    query = (
        client.table("interaction_log")
        .select("action_type, target_type, target_id, target_metadata, user_id")
        .eq("target_type", "highlight")
        .order("created_at", desc=True)
        .limit(limit)
    )
    rows = query.execute().data or []

    if clinic_id is None:
        return rows

    # Tenant boundary, re-applied by hand because the service-role key bypasses
    # RLS. Only interactions by members of this clinic may influence its scores.
    member_rows = (
        client.table("profiles").select("id").eq("clinic_id", clinic_id).execute().data
    ) or []
    members = {r["id"] for r in member_rows}
    return [r for r in rows if r.get("user_id") in members]


def _read_interactions(clinic_id: str | None, limit: int = 200) -> list[dict[str, Any]]:
    source = _interaction_source or _default_source
    try:
        return source(clinic_id, limit) or []
    except Exception:
        logger.exception("Failed to read interaction_log for learned weight")
        return []


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
    clinic_id: str | None = None,
) -> float:
    """
    Score how strongly this clinic has historically engaged with similar content.

    Returns 0.0-1.0, with 0.5 as the neutral prior when there is no signal, so an
    unseen topic is neither promoted nor buried.

    Engagement is weighted by action type (accept and manual_highlight count for
    more than a view; reject and dismiss count against) and scaled by keyword
    overlap between the candidate text and what was interacted with before.
    """
    keywords = _extract_keywords(content)
    if not keywords:
        return 0.5

    rows = _read_interactions(clinic_id)
    if not rows:
        return 0.5

    weighted_total = 0.0
    overlap_total = 0.0

    for row in rows:
        metadata = row.get("target_metadata") or {}
        stored_raw = metadata.get("keywords", [])
        if isinstance(stored_raw, str):
            stored = {k.strip().lower() for k in stored_raw.split(",") if k.strip()}
        elif isinstance(stored_raw, list):
            stored = {str(k).strip().lower() for k in stored_raw if str(k).strip()}
        else:
            stored = set()

        topic = str(metadata.get("topic", "")).lower().replace("_", " ")
        if topic:
            stored |= {w for w in topic.split() if len(w) >= 3}

        if not stored:
            continue

        overlap = keywords & stored
        if not overlap:
            continue

        # Proportion of the candidate's vocabulary that this past interaction covers.
        overlap_ratio = len(overlap) / max(len(keywords), 1)
        weight = ACTION_TYPE_WEIGHTS.get(row.get("action_type", "view"), 0.3)

        weighted_total += overlap_ratio * weight
        overlap_total += overlap_ratio

    if overlap_total == 0.0:
        return 0.5

    # Mean action weight across matching interactions, mapped from the
    # ACTION_TYPE_WEIGHTS range (-0.3 .. 1.0) onto 0..1.
    mean_weight = weighted_total / overlap_total
    normalized = (mean_weight + 0.3) / 1.3

    # Repeated engagement should compound, but with diminishing returns so a
    # single hot topic cannot permanently dominate the glance view.
    volume_bonus = min(0.25, 0.05 * overlap_total)

    return max(0.0, min(1.0, normalized + volume_bonus))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_importance_score(
    content: str,
    risk_level: str = "medium",
    created_at: str | datetime | None = None,
    clinic_id: str | None = None,
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
        clinic_id: Clinic whose interaction history informs the learned weight.
            Learning never crosses a clinic boundary.

    Returns:
        Float between 0.0 and 1.0.
    """
    recency = _compute_recency_score(created_at)
    risk = _compute_risk_score(risk_level)
    unresolved = _compute_unresolved_score(content)
    learned = await _compute_learned_score(content, clinic_id)

    score = (
        RECENCY_WEIGHT * recency
        + RISK_LEVEL_WEIGHT * risk
        + UNRESOLVED_ACTION_WEIGHT * unresolved
        + LEARNED_WEIGHT * learned
    )

    # Clamp to [0.0, 1.0]
    final = max(0.0, min(1.0, score))

    # SAFETY FLOOR — the learning loop may not bury a clinically severe item.
    #
    # Without this, the arithmetic allows it. `critical` contributes
    # RISK_LEVEL_WEIGHT * 1.0 = 0.30, so a critical highlight with no recency,
    # no unresolved marker and a learned weight driven negative by repeated
    # dismissal lands near 0.30 — below a merely `medium` item that is recent
    # and frequently engaged with. The queue then reads as though the medium
    # item matters more.
    #
    # That is not a hypothetical drift. `reject` carries -0.3 in
    # ACTION_TYPE_WEIGHTS, and the population most likely to dismiss repeatedly
    # is a tired clinician at the end of a list — so the signal the loop learns
    # from is fatigue, and the thing it learns to hide is the alert that keeps
    # firing. An allergy warning dismissed forty times is the single most
    # dangerous item to demote, and forty dismissals is exactly what teaches the
    # model to demote it.
    #
    # So severity sets a floor that learning can raise but never lower. The loop
    # keeps its full range above the floor, and keeps working normally for
    # everything below `high`, where being wrong is recoverable.
    severity = (risk_level or "").lower()

    if severity in NO_DEMOTION_SEVERITIES:
        # What the item scores on clinical grounds alone, with the learned term
        # held neutral. Learning may push above this; it may not pull below.
        unlearned = max(0.0, min(1.0,
            RECENCY_WEIGHT * recency
            + RISK_LEVEL_WEIGHT * risk
            + UNRESOLVED_ACTION_WEIGHT * unresolved
            + LEARNED_WEIGHT * NEUTRAL_LEARNED_SCORE
        ))
        if final < unlearned:
            logger.info(
                "Demotion blocked: %s item scored %.3f, held at its unlearned "
                "value %.3f. Engagement history cannot bury this severity.",
                risk_level, final, unlearned,
            )
            final = unlearned

    absolute = ABSOLUTE_FLOOR.get(severity)
    if absolute is not None and final < absolute:
        logger.info(
            "Absolute floor applied: %s item scored %.3f, raised to %.2f.",
            risk_level, final, absolute,
        )
        final = absolute

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
    clinic_id: str | None = None,
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
            clinic_id=clinic_id,
        )
        highlight["importance_score"] = score

    return highlights
