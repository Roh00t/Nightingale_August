"""
Telegram Bot API — outbound dispatch and deep-link generation.

WHAT TELEGRAM CAN AND CANNOT DO, because the difference is the whole design.

A bot **cannot message a phone number**. It can only send to a `chat_id`, and a
chat_id exists only after the person has opened a conversation with the bot
themselves. There is no API to initiate contact, by phone or otherwise.

That is the consent model, not an obstacle. It is why reaching a patient who has
no email and exists to the clinic as a phone number is a two-step flow:

    front desk generates a token
        └─→ t.me/<Bot>?start=<token>   shown at the desk or sent over WhatsApp
                └─→ patient taps, presses Start
                        └─→ Telegram delivers `/start <token>` WITH their chat_id
                                └─→ we bind it, and only now can we message them

Every claim this module makes stops at "Telegram accepted the message". Telegram
acknowledges with a `message_id`; that is acceptance by the platform, not receipt
by a person, and `queue_delivery` records it as `sent` rather than `delivered`
for exactly that reason.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PROVIDER_NAME = "telegram"
API_BASE = "https://api.telegram.org"

# Telegram rejects messages above 4096 UTF-16 code units. Truncating client-side
# with a visible marker beats a 400 the patient never learns about — but a
# clinical instruction that needed truncating is a content problem worth seeing
# in the logs, so it is warned about.
MAX_MESSAGE_CHARS = 4096
_TRUNCATION_NOTE = "\n\n[Message truncated — contact the clinic for the full text.]"


class TelegramNotConfigured(RuntimeError):
    """No bot token. Raised rather than defaulted, so nothing sends silently."""


class TelegramDispatchError(RuntimeError):
    """Telegram refused the message. Carries the API's own description."""


def bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise TelegramNotConfigured(
            "TELEGRAM_BOT_TOKEN is not set. Refusing to attempt a send that "
            "would fail silently."
        )
    return token


def is_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())


def bot_username() -> str | None:
    """Bot username for deep links, without the leading @."""
    name = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip()
    return name.lstrip("@") or None


def start_link(raw_token: str) -> str:
    """
    Deep link that binds a patient's Telegram account to an access token.

    The RAW token goes in the URL, never the hash — the patient's client has to
    send back something we can verify, and only the hash is stored. That makes
    the link itself a bearer credential: short-lived by construction
    (`patient_access_tokens.expires_at`), single-use, and never logged.

    Telegram's start parameter permits only [A-Za-z0-9_-], which is why tokens
    are generated in that alphabet rather than base64 with padding.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", raw_token):
        raise ValueError(
            "Token is not deep-link safe. Telegram's start parameter allows only "
            "A-Z a-z 0-9 _ - and at most 64 characters."
        )
    user = bot_username()
    if not user:
        raise TelegramNotConfigured("TELEGRAM_BOT_USERNAME is not set")
    return f"https://t.me/{user}?start={raw_token}"


def _truncate(text: str) -> str:
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    logger.warning(
        "Telegram message exceeded %d chars and was truncated. A clinical "
        "instruction this long probably should not be sent by message.",
        MAX_MESSAGE_CHARS,
    )
    keep = MAX_MESSAGE_CHARS - len(_TRUNCATION_NOTE)
    return text[:keep] + _TRUNCATION_NOTE


async def send_message(chat_id: int | str, body: str, *, timeout: float = 15.0) -> str:
    """
    Send one message. Returns Telegram's message_id as a string.

    Async, because both callers are async FastAPI endpoints and a blocking HTTP
    call there stalls the event loop for every other request — under a clinic's
    load that turns one slow send into a service-wide pause.

    `parse_mode` is deliberately NOT set. Clinical text contains underscores,
    asterisks and brackets ("BP 128/78 (stable)", "take 1_2 tablets"), and
    Markdown parsing would either mangle it or make Telegram reject the whole
    message for an unbalanced entity. Plain text always renders.
    """
    token = bot_token()
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": _truncate(body),
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{API_BASE}/bot{token}/sendMessage", json=payload)
    except httpx.TimeoutException as exc:
        raise TelegramDispatchError(f"Telegram timed out after {timeout}s") from exc
    except httpx.HTTPError as exc:
        raise TelegramDispatchError(f"Could not reach Telegram: {exc}") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise TelegramDispatchError(
            f"Telegram returned a non-JSON body (http {resp.status_code})"
        ) from exc

    if not data.get("ok"):
        # Telegram's own description, verbatim. "chat not found" and "bot was
        # blocked by the user" are different clinical situations — the first is
        # a patient who never tapped the link, the second is one who opted out —
        # and collapsing them into "failed" makes the front desk chase the wrong
        # thing.
        description = data.get("description", "unknown error")
        logger.error("Telegram refused message to chat %s: %s", chat_id, description)
        raise TelegramDispatchError(description)

    message_id = data.get("result", {}).get("message_id")
    if message_id is None:
        raise TelegramDispatchError("Telegram accepted the message but returned no message_id")

    logger.info("Telegram accepted message %s for chat %s", message_id, chat_id)
    return str(message_id)


def parse_start_command(update: dict[str, Any]) -> tuple[int, str] | None:
    """
    Extract (chat_id, token) from a `/start <token>` update.

    Returns None for anything else — a plain message, an edit, a callback query.
    The bot receives every update for its chats, so most of them are not this,
    and a parser that guessed would bind the wrong chat.
    """
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if chat_id is None or not text.startswith("/start"):
        return None

    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        # Bare /start with no token: the person opened the bot directly rather
        # than through a clinic link. Nothing to bind, and nothing to log about
        # them beyond that it happened.
        return None

    return int(chat_id), parts[1].strip()
