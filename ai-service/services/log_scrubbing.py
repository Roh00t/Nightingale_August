"""
Last-resort PHI scrubbing on the way to a log sink.

The redaction pipeline (services/redaction.py) is the real control: it strips
PHI before text reaches the LLM, and it is precise because it runs Presidio with
spaCy NER over content the caller intended to send. This module is the opposite
kind of thing — a blunt, regex-only net across *every* log record the process
emits, including ones written by libraries that have never heard of this
codebase.

Why both. Application logging in this repo is already written to record counts
and identifiers rather than content, and there are tests asserting that. But a
log line is not typed, and the failure mode is silent: a `logger.exception()`
during a redaction fault prints the very string that was being redacted, and
uvicorn's access log prints whatever landed in a query string. Neither goes
through `redact()`. One `%s` on the wrong variable puts an NRIC into a log
aggregator that has a different retention policy and a wider audience than the
database.

Deliberately NOT reusing Presidio here:

  * A logging filter runs inline on the emitting thread, on every record. Model
    inference in that path would add tens of milliseconds to every log line and
    could deadlock if the analyser itself logs.
  * A filter that raises loses the record. Regex is predictable and cannot fail
    on unusual input the way a model pipeline can.

So this catches the structured, high-confidence identifiers only — NRIC/FIN,
Singapore phone numbers, emails, and names in labelled positions. It is not a
substitute for redaction and does not claim to catch free-text names.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

# NRIC/FIN. Same character classes as the redaction recogniser, including the
# 2022 M series; see services/redaction.py for why M matters.
_NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b")

# Singapore numbers: mobile 8/9, landline 6, with or without +65, and with the
# internal spacing people actually type.
#
# Digit lookarounds rather than \b. In "+6591234567" there is no word boundary
# between the "5" of +65 and the leading "9" — both are word characters — so a
# \b anchored form matches the spaced "+65 9123 4567" and silently misses the
# unspaced one, which is the form a machine writes into a query string.
_PHONE = re.compile(r"(?<!\d)(?:\+?65[\s-]?)?[689]\d{3}[\s-]?\d{4}(?!\d)")

_EMAIL = re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")

# Names are only scrubbed where a label makes the following token a name with
# high confidence. A bare capitalised-word rule would eat "Lisinopril", "Monday"
# and every log message that starts with a capital, which would make logs
# useless and push people to disable this.
_LABELLED_NAME = re.compile(
    r"\b(patient|name|display_name|patient_name|full_name|caller|approver_name)"
    r"(\s*[:=]\s*)"
    r"(\"?)([A-Z][a-z'’-]+(?:\s+(?:[A-Z][a-z'’-]+|bin|binte|s/o|d/o|a/l|a/p)){0,3})\3",
    re.IGNORECASE,
)

_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_NRIC, "<NRIC_REDACTED>"),
    (_EMAIL, "<EMAIL_REDACTED>"),
    (_PHONE, "<PHONE_REDACTED>"),
)


def scrub(text: str) -> str:
    """Replace high-confidence identifiers in one string."""
    for pattern, replacement in _REPLACEMENTS:
        text = pattern.sub(replacement, text)
    # Keep the label so the line still reads: "patient=<NAME_REDACTED>".
    text = _LABELLED_NAME.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}<NAME_REDACTED>{m.group(3)}",
        text,
    )
    return text


def _scrub_value(value: Any) -> Any:
    """Scrub strings anywhere in a log record's argument structure."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, tuple):
        return tuple(_scrub_value(v) for v in value)
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    return value


class PHIAnonymizingLogFilter(logging.Filter):
    """
    Scrub PHI from a record before any handler formats it.

    Attached to the ROOT logger, so it covers uvicorn's access and error loggers
    and any third-party library, not just `nightingale.*`. A filter on the
    application logger alone would miss precisely the emitters most likely to
    print raw input.

    Both `msg` and `args` are scrubbed. Scrubbing only the formatted output
    would be simpler but has to happen in a Formatter, and a Formatter runs per
    handler — add a second handler and the new one is unprotected. Filters run
    once, before fan-out.

    `exc_text` is cleared rather than scrubbed: a cached traceback string is
    rebuilt from `exc_info` by the formatter, so scrubbing the cache would be
    silently discarded. Tracebacks themselves are scrubbed by
    `scrub_formatter`, which is what actually renders them.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if record.args:
            record.args = _scrub_value(record.args)
        record.exc_text = None
        return True  # never drop a record; scrubbing is not filtering


class PHIScrubbingFormatter(logging.Formatter):
    """
    Scrub the fully rendered line, tracebacks included.

    The filter cannot reach traceback text, because that is produced here during
    formatting. An exception raised while handling patient text puts the offending
    value in a frame's locals-derived message — the single most likely way PHI
    reaches a log — so the rendered result gets one more pass.
    """

    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))


def install(loggers: Iterable[str] = ("", "uvicorn", "uvicorn.access", "uvicorn.error")) -> None:
    """
    Attach the filter and formatter across the root and uvicorn loggers.

    Called at import time from main.py, before the app is constructed, so that
    records emitted during startup are covered too. Idempotent: re-running
    replaces the existing filter rather than stacking a second one.
    """
    fmt = PHIScrubbingFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for name in loggers:
        log = logging.getLogger(name)
        log.filters = [f for f in log.filters if not isinstance(f, PHIAnonymizingLogFilter)]
        log.addFilter(PHIAnonymizingLogFilter())
        for handler in log.handlers:
            handler.setFormatter(fmt)
            handler.filters = [
                f for f in handler.filters if not isinstance(f, PHIAnonymizingLogFilter)
            ]
            handler.addFilter(PHIAnonymizingLogFilter())
