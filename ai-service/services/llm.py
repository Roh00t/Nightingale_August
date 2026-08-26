"""
Groq LLM client for clinical text processing.

Uses the Groq Python SDK with the GPT-OSS-20B model for:
- Care note summarization
- Clinical highlight extraction
- Patient summary generation

All functions accept pre-redacted text (PHI already removed) and return
structured JSON output.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from groq import AsyncGroq, RateLimitError

logger = logging.getLogger(__name__)

MODEL_ID = "openai/gpt-oss-20b"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds, exponential backoff


def _get_client() -> AsyncGroq:
    """Create a Groq async client. Reads GROQ_API_KEY from the environment."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "Obtain a key from https://console.groq.com and export it."
        )
    return AsyncGroq(api_key=api_key)


async def _call_with_retry(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """
    Send a chat completion request to Groq with exponential backoff on rate limits.

    Returns the parsed JSON response body from the model.
    """
    client = _get_client()

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.chat.completions.create(
                model=MODEL_ID,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("Empty response from Groq model")

            parsed: dict[str, Any] = json.loads(content)
            return parsed

        except RateLimitError as exc:
            last_error = exc
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "Groq rate limit hit (attempt %d/%d). Retrying in %.1fs",
                attempt,
                MAX_RETRIES,
                delay,
            )
            await asyncio.sleep(delay)

        except json.JSONDecodeError as exc:
            logger.error("Failed to parse JSON from Groq response: %s", exc)
            raise ValueError(f"Model returned invalid JSON: {exc}") from exc

    raise RuntimeError(
        f"Groq API rate limit exceeded after {MAX_RETRIES} retries"
    ) from last_error


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_summary(
    redacted_entries: list[dict[str, Any]],
    *,
    patient_context: str = "",
) -> dict[str, Any]:
    """
    Generate a clinical summary from a list of redacted timeline entries.

    Args:
        redacted_entries: List of care note entries with PHI already redacted.
            Each entry should have at minimum: ``content``, ``entry_type``,
            ``created_at``.
        patient_context: Optional high-level context (e.g. diagnosis, age range).

    Returns:
        Dictionary containing:
        - highlights: list of key clinical observations
        - changes_since_last_visit: list of notable changes
        - care_plan_score: 0-100 adherence/progress score
        - care_plan_items: list of actionable care plan items
        - patient_summary: prose summary paragraph
    """
    entries_text = "\n\n".join(
        f"[{e.get('entry_type', 'note')} | {e.get('created_at', 'unknown date')}]\n{e.get('content', '')}"
        for e in redacted_entries
    )

    system_prompt = (
        "You are a clinical summarization assistant for home healthcare professionals. "
        "You receive de-identified care notes and produce structured summaries. "
        "Always respond with valid JSON matching the schema below. "
        "Be concise, clinically precise, and highlight actionable information.\n\n"
        "Output JSON schema:\n"
        "{\n"
        '  "highlights": ["string - key clinical observation"],\n'
        '  "changes_since_last_visit": ["string - notable change"],\n'
        '  "care_plan_score": <integer 0-100>,\n'
        '  "care_plan_items": [\n'
        "    {\n"
        '      "item": "string - action item",\n'
        '      "priority": "high | medium | low",\n'
        '      "status": "new | ongoing | resolved"\n'
        "    }\n"
        "  ],\n"
        '  "patient_summary": "string - 2-4 sentence prose summary"\n'
        "}"
    )

    user_prompt = f"Summarize the following care notes:\n\n{entries_text}"
    if patient_context:
        user_prompt = f"Patient context: {patient_context}\n\n{user_prompt}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    result = await _call_with_retry(messages, temperature=0.2, max_tokens=4096)

    # Ensure required keys exist with sensible defaults
    result.setdefault("highlights", [])
    result.setdefault("changes_since_last_visit", [])
    result.setdefault("care_plan_score", 50)
    result.setdefault("care_plan_items", [])
    result.setdefault("patient_summary", "")

    return result


async def generate_highlights(
    redacted_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Extract clinical highlights with risk assessment from care note entries.

    Args:
        redacted_entries: List of care note entries with PHI already redacted.

    Returns:
        List of highlight dictionaries, each containing:
        - content_snippet: relevant excerpt from the note
        - risk_reason: why this is flagged
        - risk_level: critical | high | medium | low
        - importance_score: float 0.0-1.0 (model's initial estimate)
        - provenance_pointer: reference to source entry
    """
    entries_text = "\n\n".join(
        f"[Entry {i+1} | {e.get('entry_type', 'note')} | {e.get('created_at', 'unknown')}]\n{e.get('content', '')}"
        for i, e in enumerate(redacted_entries)
    )

    system_prompt = (
        "You are a clinical risk assessment assistant. Analyze care notes and extract "
        "highlights that require clinical attention. Focus on: medication changes, "
        "vital sign anomalies, new symptoms, falls, wounds, behavioral changes, "
        "and care plan deviations.\n\n"
        "Respond with valid JSON matching this schema:\n"
        "{\n"
        '  "highlights": [\n'
        "    {\n"
        '      "content_snippet": "string - relevant excerpt from the note",\n'
        '      "risk_reason": "string - clinical rationale for flagging",\n'
        '      "risk_level": "critical | high | medium | low",\n'
        '      "importance_score": <float 0.0-1.0>,\n'
        '      "provenance_pointer": "string - Entry N reference"\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    user_prompt = (
        f"Extract clinical highlights from these care notes:\n\n{entries_text}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    result = await _call_with_retry(messages, temperature=0.2, max_tokens=4096)

    highlights = result.get("highlights", [])

    # Validate and normalize each highlight
    validated: list[dict[str, Any]] = []
    for h in highlights:
        validated.append(
            {
                "content_snippet": h.get("content_snippet", ""),
                "risk_reason": h.get("risk_reason", ""),
                "risk_level": h.get("risk_level", "medium"),
                "importance_score": float(h.get("importance_score", 0.5)),
                "provenance_pointer": h.get("provenance_pointer", ""),
            }
        )

    return validated


async def generate_patient_summary(
    redacted_entries: list[dict[str, Any]],
    *,
    summary_type: str = "shift_handover",
) -> dict[str, Any]:
    """
    Generate a patient summary tuned for a specific use case.

    Args:
        redacted_entries: List of care note entries with PHI already redacted.
        summary_type: One of 'shift_handover', 'family_update', 'clinical_review'.

    Returns:
        Dictionary with 'summary' text and 'key_points' list.
    """
    type_instructions = {
        "shift_handover": (
            "Write a concise shift handover summary suitable for the incoming "
            "care professional. Prioritize immediate needs, pending tasks, and "
            "observations from the current shift."
        ),
        "family_update": (
            "You are a clinician writing directly to the patient. Write a warm, "
            "compassionate message addressed to the patient (use 'you' and 'your'). "
            "Use simple, jargon-free language. Focus on their progress, what they "
            "should do next (medications, diet, lifestyle), and encouragement. "
            "Do NOT refer to the patient in third person or as 'your loved one'. "
            "Example tone: 'Your blood pressure is looking better! Please continue...'"
        ),
        "clinical_review": (
            "Write a detailed clinical summary suitable for a physician review. "
            "Include vital trends, medication adherence, symptom progression, "
            "and any concerns requiring medical intervention."
        ),
    }

    instruction = type_instructions.get(
        summary_type, type_instructions["shift_handover"]
    )

    entries_text = "\n\n".join(
        f"[{e.get('entry_type', 'note')} | {e.get('created_at', 'unknown')}]\n{e.get('content', '')}"
        for e in redacted_entries
    )

    system_prompt = (
        f"You are a clinical summarization assistant. {instruction}\n\n"
        "Respond with valid JSON:\n"
        "{\n"
        '  "summary": "string - the summary paragraph(s)",\n'
        '  "key_points": ["string - key point"]\n'
        "}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"Generate summary from these notes:\n\n{entries_text}",
        },
    ]

    result = await _call_with_retry(messages, temperature=0.3, max_tokens=2048)

    result.setdefault("summary", "")
    result.setdefault("key_points", [])

    return result
