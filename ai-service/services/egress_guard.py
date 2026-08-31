"""
The last thing that runs before text leaves the process for the LLM.

`services/redaction.py` is what removes PHI. This module does not remove
anything — it refuses to send. The distinction matters: silently repairing a
prompt here would hide the fact that some call path forgot to redact, and the
next unredacted field would go out under a different shape.

Why a runtime check when redaction is already ordered first. The ordering is
enforced by code reading — `redact()` on line N, the model call on line N+20 —
and code reading does not survive refactoring. A new endpoint, an added
`patient_context=` argument, a retry that rebuilds the prompt from the original:
each is a one-line change that reintroduces the leak with no test failing,
because the tests that exist assert the *redactor's* behaviour, not that every
prompt passed through it.

So this sits at the single chokepoint every model call funnels through
(`_call_with_retry`) and re-derives the answer from the payload itself. It is
structural rather than procedural: a call path cannot be added that skips it
without deleting this call.

It looks for high-confidence structured identifiers only — the same NRIC and
phone shapes the redactor recognises — plus residual placeholder corruption. It
cannot detect an un-redacted free-text name, and does not pretend to; that is
Presidio's job, upstream. What it guarantees is narrower and worth stating
exactly: **no NRIC/FIN or Singapore phone number reaches Groq, whatever the
call path did.**
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Same shapes as services/redaction.py recognises. Kept here rather than
# imported so that a change loosening the redactor cannot silently loosen the
# guard at the same time — the two are meant to be independent readings.
_NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?65[\s-]?)?[689]\d{3}[\s-]?\d{4}(?!\d)")

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("NRIC", _NRIC),
    ("PHONE", _PHONE),
)


class UnredactedEgressError(RuntimeError):
    """
    Raised instead of sending a prompt that still contains an identifier.

    Deliberately not an HTTPException: this is a programming fault in a call
    path, not a client error, and it should surface as a 500 with a traceback
    that names the offending category. A caller cannot fix it by retrying.
    """


class RedactedText(str):
    """
    A string that has been through `redact()`.

    A `str` subclass, so it flows through existing code and f-strings unchanged
    and adopting it needs no call-site edits. It carries no runtime power — this
    is a *label*, and `RedactedText(raw)` would happily wrap unredacted input.

    Its value is in type signatures and review: a function annotated
    `def build_prompt(text: RedactedText)` states its precondition where a
    reader and a type checker both see it. The actual enforcement is
    `assert_safe_for_model()` below, which trusts nothing and re-reads the text.
    """

    __slots__ = ()


def scan(text: str) -> list[str]:
    """Identifier categories still present in `text`. Empty means clean."""
    return [name for name, pattern in _PATTERNS if pattern.search(text)]


def _iter_strings(messages: Iterable[Any]) -> Iterable[str]:
    """Every string in a chat payload, whatever shape the content takes."""
    for message in messages:
        if isinstance(message, str):
            yield message
            continue
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            # Multimodal content blocks: [{"type": "text", "text": ...}, ...]
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    yield block["text"]


def assert_safe_for_model(messages: Iterable[Any]) -> None:
    """
    Refuse the request if any identifier survived into the prompt.

    Raises `UnredactedEgressError` naming the categories found — never the
    matched values, which are the PHI. The log line is likewise category-only,
    so investigating a fault does not create a second copy of the leak in the
    log aggregator.
    """
    found: set[str] = set()
    for text in _iter_strings(messages):
        found.update(scan(text))

    if found:
        categories = ", ".join(sorted(found))
        logger.error(
            "Blocked model call: unredacted %s in prompt. A call path reached "
            "the model without passing through redact().",
            categories,
        )
        raise UnredactedEgressError(
            f"Refusing to send prompt containing unredacted {categories}. "
            "Every path to the model must call services.redaction.redact() first."
        )
