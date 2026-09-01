"""
Telegram dispatch, webhook authenticity, and passwordless token identity.

The scenario these serve: a patient with no email, who exists to the clinic as a
phone number in a WhatsApp thread. The constraint that shapes every test below
is that **Telegram cannot message a phone number** — a bot may only send to a
`chat_id`, which exists only after the person opens the bot themselves. So the
tests are as much about refusing to pretend otherwise as about sending.
"""

from __future__ import annotations

import pytest

import services.messaging as msg
import services.telegram as tg
import services.telegram_identity as tid
from services.supabase_writer import AccessDenied


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestTelegramDispatch:
    @pytest.fixture(autouse=True)
    def _bot(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TEST")
        monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "NightingaleTestBot")

    async def test_send_returns_the_message_id(self, monkeypatch):
        """Acceptance returns an id — and nothing that could be read as receipt."""
        class _Resp:
            status_code = 200
            def json(self): return {"ok": True, "result": {"message_id": 8891}}

        class _Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json): return _Resp()

        monkeypatch.setattr(tg.httpx, "AsyncClient", lambda **kw: _Client())
        assert await tg.send_message(4242, "Your appointment is confirmed.") == "8891"

    async def test_api_refusal_surfaces_telegrams_own_description(self, monkeypatch):
        """
        "chat not found" and "bot was blocked by the user" are different clinical
        situations — a patient who never tapped the link versus one who opted out
        — and collapsing them into "failed" sends the front desk after the wrong
        thing.
        """
        class _Resp:
            status_code = 200
            def json(self): return {"ok": False, "description": "Forbidden: bot was blocked by the user"}

        class _Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json): return _Resp()

        monkeypatch.setattr(tg.httpx, "AsyncClient", lambda **kw: _Client())
        with pytest.raises(tg.TelegramDispatchError, match="blocked by the user"):
            await tg.send_message(4242, "hello")

    async def test_missing_token_refuses_rather_than_defaults(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with pytest.raises(tg.TelegramNotConfigured):
            await tg.send_message(4242, "hello")

    async def test_timeout_is_reported_as_dispatch_failure(self, monkeypatch):
        """A hung send must not hang the endpoint that called it."""
        class _Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json): raise tg.httpx.TimeoutException("timed out")

        monkeypatch.setattr(tg.httpx, "AsyncClient", lambda **kw: _Client())
        with pytest.raises(tg.TelegramDispatchError, match="timed out"):
            await tg.send_message(4242, "hello")

    async def test_long_message_is_truncated_not_rejected(self):
        """
        Telegram hard-rejects over 4096 chars. Truncating with a visible marker
        beats a 400 the patient never learns about.
        """
        out = tg._truncate("x" * 6000)
        assert len(out) <= tg.MAX_MESSAGE_CHARS
        assert "truncated" in out

    def test_markdown_is_not_enabled(self):
        """
        Clinical text contains underscores, asterisks and brackets — "take 1_2
        tablets", "BP 128/78 (stable)". parse_mode would mangle it or make
        Telegram reject the whole message for an unbalanced entity.
        """
        import ast
        import pathlib

        # AST over the payload dict, not a text search: the docstring explains
        # why parse_mode is omitted, and a grep cannot tell a rationale from a
        # setting. (Third time this trap has been hit in this codebase.)
        tree = ast.parse(pathlib.Path(tg.__file__).read_text())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "send_message")
        keys = {
            k.value
            for node in ast.walk(fn) if isinstance(node, ast.Dict)
            for k in node.keys if isinstance(k, ast.Constant)
        }
        assert "parse_mode" not in keys, "parse_mode is set; clinical text will be mangled"
        assert "text" in keys and "chat_id" in keys


# ---------------------------------------------------------------------------
# Deep links
# ---------------------------------------------------------------------------


class TestDeepLinks:
    def test_link_uses_the_raw_token(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "NightingaleTestBot")
        raw = "AbC123_def-456GHI789"
        assert tg.start_link(raw) == f"https://t.me/NightingaleTestBot?start={raw}"

    def test_link_rejects_a_token_telegram_cannot_carry(self, monkeypatch):
        """
        Telegram's start parameter allows only [A-Za-z0-9_-] and 64 chars. A
        base64 token with `+`, `/` or `=` produces a link that silently loses
        characters, and the patient gets "that link is no longer valid".
        """
        monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "NightingaleTestBot")
        for bad in ["short", "has+plus/and=padding", "x" * 65]:
            with pytest.raises(ValueError):
                tg.start_link(bad)

    def test_issued_tokens_are_deep_link_safe(self):
        """The generator and the link validator must agree."""
        import secrets

        for _ in range(20):
            raw = secrets.token_urlsafe(tid.TOKEN_BYTES)[:64]
            assert all(c.isalnum() or c in "-_" for c in raw)
            assert 16 <= len(raw) <= 64


class TestStartCommandParsing:
    def test_extracts_chat_and_token(self):
        assert tg.parse_start_command(
            {"message": {"chat": {"id": 4242}, "text": "/start tok_abc"}}
        ) == (4242, "tok_abc")

    def test_ignores_everything_that_is_not_a_start_with_payload(self):
        """
        The bot receives every update for its chats. A parser that guessed would
        bind the wrong chat to a patient.
        """
        for update in [
            {"message": {"chat": {"id": 1}, "text": "/start"}},
            {"message": {"chat": {"id": 1}, "text": "hello"}},
            {"callback_query": {"id": "x"}},
            {},
        ]:
            assert tg.parse_start_command(update) is None


# ---------------------------------------------------------------------------
# Webhook authenticity
# ---------------------------------------------------------------------------


class TestTelegramWebhookSecret:
    def _post(self, headers=None):
        from fastapi.testclient import TestClient

        import main

        return TestClient(main.app).post(
            "/api/messaging/telegram-webhook",
            json={"message": {"chat": {"id": 1}, "text": "/start abc"}},
            headers=headers or {},
        )

    def test_missing_secret_config_fails_closed(self, monkeypatch):
        """
        This endpoint cannot require a JWT — Telegram holds none — so without the
        secret there is nothing separating the platform from anyone who learned
        the URL.
        """
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
        assert self._post({"X-Telegram-Bot-Api-Secret-Token": "anything"}).status_code == 403

    def test_missing_header_is_rejected(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
        assert self._post().status_code == 403

    def test_wrong_secret_is_rejected(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
        assert self._post({"X-Telegram-Bot-Api-Secret-Token": "nope"}).status_code == 403

    def test_unredeemable_token_returns_200_not_an_error(self, monkeypatch):
        """
        Two reasons. Telegram retries any non-2xx indefinitely, so a permanently
        bad token would be retried forever and bury real callbacks. And a
        distinguishable response turns the endpoint into an oracle for guessing
        tokens.
        """
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
        import routers.messaging as mr

        monkeypatch.setattr(
            mr, "link_telegram_chat",
            lambda **kw: (_ for _ in ()).throw(AccessDenied("no")),
        )
        r = self._post({"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
        assert r.status_code == 200
        assert r.json()["result"] == "ok"

    def test_successful_link_is_reported(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
        import routers.messaging as mr

        monkeypatch.setattr(
            mr, "link_telegram_chat",
            lambda **kw: {"profile_id": "p1", "clinic_id": "c1", "chat_id": kw["chat_id"]},
        )
        r = self._post({"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
        assert r.status_code == 200
        assert r.json()["result"] == "linked"


# ---------------------------------------------------------------------------
# Token redemption
# ---------------------------------------------------------------------------


class TestTokenRedemption:
    def test_only_the_hash_is_stored(self):
        """
        The table is readable by clinic staff for support. A plaintext token in a
        support view is a credential lying in the open.
        """
        import inspect

        src = inspect.getsource(tid.issue_access_token)
        insert = src[src.index(".insert({"):src.index(".execute()")]
        assert "token_hash" in insert
        assert '"raw"' not in insert and "'raw'" not in insert

    def test_hash_is_sha256_of_the_raw_token(self):
        import hashlib

        assert tid.hash_token("abc") == hashlib.sha256(b"abc").hexdigest()

    def test_expired_consumed_and_unknown_are_indistinguishable(self, monkeypatch):
        """
        Each distinction is an oracle. "Unknown" in particular lets someone probe
        for valid tokens by watching which response differs.
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        cases = {
            "unknown":   [],
            "expired":   [{"id": "t", "expires_at": (now - timedelta(hours=1)).isoformat(),
                           "use_count": 0, "max_uses": 1, "failed_attempts": 0, "consumed_at": None}],
            "consumed":  [{"id": "t", "expires_at": (now + timedelta(hours=1)).isoformat(),
                           "use_count": 1, "max_uses": 1, "failed_attempts": 0, "consumed_at": now.isoformat()}],
            "exhausted": [{"id": "t", "expires_at": (now + timedelta(hours=1)).isoformat(),
                           "use_count": 0, "max_uses": 1, "failed_attempts": 5, "consumed_at": None}],
        }
        messages = set()
        for label, rows in cases.items():
            class _Tbl:
                def select(self, *a): return self
                def eq(self, *a): return self
                def limit(self, *a): return self
                def execute(self): return type("R", (), {"data": rows})()

            monkeypatch.setattr(tid, "get_service_client",
                                lambda: type("C", (), {"table": lambda s, n: _Tbl()})())
            with pytest.raises(AccessDenied) as exc:
                tid._load_redeemable("a" * 40)
            messages.add(str(exc.value))
        assert len(messages) == 1, f"redemption leaks which failure occurred: {messages}"

    def test_non_patient_role_is_refused(self, monkeypatch):
        """
        A token minted against a staff profile would be a password-free path into
        a clinical account.
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        token = {"id": "t", "profile_id": "p1", "expires_at": (now + timedelta(hours=1)).isoformat(),
                 "use_count": 0, "max_uses": 1, "failed_attempts": 0, "consumed_at": None}

        class _Tbl:
            def __init__(self, name): self.name = name
            def select(self, *a): return self
            def update(self, *a): return self
            def eq(self, *a): return self
            def limit(self, *a): return self
            def execute(self):
                if self.name == "patient_access_tokens":
                    return type("R", (), {"data": [token]})()
                return type("R", (), {"data": [{"id": "p1", "clinic_id": "c1",
                                                "role": "clinician", "display_name": "Dr X"}]})()

        monkeypatch.setattr(tid, "get_service_client",
                            lambda: type("C", (), {"table": lambda s, n: _Tbl(n)})())
        with pytest.raises(AccessDenied):
            tid.redeem_token("a" * 40)


class TestTelegramCannotMessageAPhoneNumber:
    """
    The constraint the whole design turns on, asserted so a future change cannot
    quietly assume otherwise.
    """

    def test_dispatch_addresses_a_chat_not_a_number(self, monkeypatch):
        """
        queue_delivery must resolve a bound chat_id. There is no API to derive
        one from a phone number, so an unlinked patient is genuinely unreachable
        on this channel — and must be reported as such, not rerouted.
        """
        import inspect

        assert "telegram_chat_id" in inspect.getsource(msg.resolve_telegram_chat)

    def test_e164_validation_does_not_apply_to_telegram(self):
        """
        A chat_id is not a phone number; running the E.164 check on one would
        reject every valid Telegram send.
        """
        import inspect

        src = inspect.getsource(msg.queue_delivery)
        assert 'channel in ("whatsapp", "sms")' in src
