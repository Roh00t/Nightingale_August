"""
Mock provider, OTP identity, and read-time provenance.

Each of these three closed a gap where the mechanism existed but was never
exercised end to end. The tests concentrate on the abuse cases, because in all
three the failure mode is silent: a forged receipt, an enumerated patient list,
and a highlight claiming currency it does not have.
"""

from __future__ import annotations

import os

import pytest

import services.messaging as msg
import services.messaging_mock as mm
from services.otp import (
    MAX_REQUESTS_PER_WINDOW,
    MAX_VERIFY_ATTEMPTS,
    OTP_TTL,
    hash_code,
)
from services.provenance import ProvenanceVerdict, quote_hash, verify_quote


# ---------------------------------------------------------------------------
# Task 1 — the mock must not be able to send, forge, or start by accident
# ---------------------------------------------------------------------------


class TestMockProviderCannotEscape:
    def test_mock_is_off_unless_explicitly_named(self, monkeypatch):
        """
        `== "mock"`, not `!= "live"`. A typo or a missing variable must give NO
        provider rather than the mock — otherwise a staging convenience reaches
        production, every message reports delivered, and nobody is contacted.
        """
        for value in [None, "", "live", "twilio", "MOCKING", "mok"]:
            if value is None:
                monkeypatch.delenv("MESSAGING_PROVIDER", raising=False)
            else:
                monkeypatch.setenv("MESSAGING_PROVIDER", value)
            assert mm.is_enabled() is False, f"mock enabled for {value!r}"

    def test_mock_enabled_only_for_exact_value(self, monkeypatch):
        monkeypatch.setenv("MESSAGING_PROVIDER", "  MoCk  ")
        assert mm.is_enabled() is True

    def test_mock_refuses_a_real_number(self, monkeypatch):
        """
        The rule that keeps a simulator from contacting a patient. Refusing is
        the only safe answer — silently dropping would look like a send.
        """
        monkeypatch.setenv("MESSAGING_PROVIDER", "mock")
        for real in ["+6591234567", "+6598765432", "+14155550123"]:
            with pytest.raises(mm.MockNotPermitted):
                mm.dispatch("whatsapp", real, "hello")

    def test_mock_refuses_when_not_enabled(self, monkeypatch):
        """Defence in depth: even a reserved number is refused when off."""
        monkeypatch.setenv("MESSAGING_PROVIDER", "live")
        with pytest.raises(mm.MockNotPermitted):
            mm.dispatch("whatsapp", "+6580000001", "hello")

    def test_dispatch_never_returns_a_delivered_status(self, monkeypatch):
        """
        Acceptance is not receipt. `dispatch` returns only an id, so there is no
        shape in which it could report delivery — the type makes the conflation
        the module exists to prevent impossible to express.
        """
        monkeypatch.setenv("MESSAGING_PROVIDER", "mock")
        monkeypatch.setenv("MOCK_WEBHOOK_DELAY_SECONDS", "999")
        provider, message_id = mm.dispatch("whatsapp", "+6580000001", "hello")
        assert provider == "mock"
        assert isinstance(message_id, str) and message_id.startswith("mock_")

    def test_silent_destination_never_confirms(self, monkeypatch):
        """
        The most realistic failure, and the one a system is likeliest to get
        wrong: accepted and never confirmed is indistinguishable from success
        until the patient does not arrive. It must stay reachable in testing.
        """
        monkeypatch.setenv("MESSAGING_PROVIDER", "mock")
        assert mm.MOCK_DESTINATIONS["+6580000004"] == "silent"

    def test_mock_has_no_privileged_path_into_delivery_state(self):
        """
        The mock must go through the signed HTTP webhook like anyone else. If it
        could call apply_provider_status directly it would prove nothing about
        authenticity and would itself be a forgery path.
        """
        import ast
        import pathlib

        tree = ast.parse(pathlib.Path(mm.__file__).read_text())
        # AST, not text search: the module docstring legitimately *names*
        # apply_provider_status while explaining why it must not call it, and a
        # grep cannot tell an explanation from an invocation.
        referenced = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "apply_provider_status" not in referenced, (
            "the mock reaches delivery state directly instead of via the signed webhook"
        )
        assert "X-Signature" in pathlib.Path(mm.__file__).read_text(), (
            "the mock does not sign its callback"
        )

    def test_provider_configured_reflects_the_mock(self, monkeypatch):
        monkeypatch.delenv("MESSAGING_PROVIDER_API_KEY", raising=False)
        monkeypatch.setenv("MESSAGING_PROVIDER", "mock")
        assert msg.provider_configured() is True
        monkeypatch.setenv("MESSAGING_PROVIDER", "")
        assert msg.provider_configured() is False


# ---------------------------------------------------------------------------
# Task 2 — OTP
# ---------------------------------------------------------------------------


class TestOTPCredentialHandling:
    @pytest.fixture(autouse=True)
    def _pepper(self, monkeypatch):
        monkeypatch.setenv("OTP_PEPPER", "test-pepper")

    def test_hash_is_bound_to_the_phone(self):
        """
        An unbound hash of "123456" is the same row for every patient, so one
        leaked valid code could be replayed against any account whose live code
        matched.
        """
        assert hash_code("+6591234567", "123456") != hash_code("+6598765432", "123456")

    def test_hash_is_peppered_not_bare_sha256(self, monkeypatch):
        """
        A bare sha256 over six digits is a 10^6 rainbow table. Changing the
        pepper must change every hash, proving the secret participates.
        """
        a = hash_code("+6591234567", "123456")
        monkeypatch.setenv("OTP_PEPPER", "different")
        assert hash_code("+6591234567", "123456") != a

    def test_missing_pepper_fails_closed(self, monkeypatch):
        """
        Running without a pepper would silently downgrade every stored hash to
        something trivially reversible, so issuance refuses instead.
        """
        monkeypatch.delenv("OTP_PEPPER", raising=False)
        monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
        from services.otp import OTPError

        with pytest.raises(OTPError):
            hash_code("+6591234567", "123456")

    def test_limits_bound_the_search_space(self):
        """
        Six digits is only safe because attempts are capped. Capping guesses
        without capping issuance would be pointless — each new request mints a
        fresh code, so unlimited requests times five guesses is unlimited
        guesses.
        """
        assert MAX_VERIFY_ATTEMPTS <= 5
        assert MAX_REQUESTS_PER_WINDOW <= 3
        assert OTP_TTL.total_seconds() <= 600

    def test_plaintext_code_is_never_persisted(self):
        """The row stores a hash; the plaintext exists only in the message."""
        import pathlib

        source = pathlib.Path("services/otp.py").read_text()
        insert = source[source.index('.insert({'):source.index('.execute()', source.index('.insert({'))]
        assert "token_hash" in insert
        assert '"code"' not in insert and "'code'" not in insert


class TestOTPDoesNotEnumeratePatients:
    def test_unknown_and_known_numbers_return_the_same_shape(self):
        """
        A 404 for an unknown number turns this into a patient-enumeration
        oracle: walk the mobile range, learn which numbers belong to the
        clinic's patients. That membership is itself PHI, disclosed before
        anyone authenticates.
        """
        import pathlib

        source = pathlib.Path("routers/auth_otp.py").read_text()
        assert "If that number is registered" in source
        # The unknown-number branch must return the generic response, not raise.
        unknown_branch = source[source.index("if profile is None:"):]
        assert "return generic" in unknown_branch.split("\n\n")[0]

    def test_verify_failures_are_indistinguishable(self):
        """
        Expired, wrong, already-used and no-such-number must produce one
        message. Each distinction is an oracle, and the last is enumeration.
        """
        import pathlib

        source = pathlib.Path("services/otp.py").read_text()
        assert source.count("raise generic") >= 4, (
            "verify() should funnel every failure through one generic error"
        )


# ---------------------------------------------------------------------------
# Task 3 — read-time provenance
# ---------------------------------------------------------------------------


SOURCE = "Patient reports dizziness. Lisinopril 10mg daily. Review in 3 months."
QUOTE = "Lisinopril 10mg daily."


class TestReadTimeProvenance:
    def test_unchanged_source_is_current(self):
        assert verify_quote(
            stored_hash=quote_hash(QUOTE), source_text=SOURCE
        ) is ProvenanceVerdict.CURRENT

    def test_intra_version_dose_edit_is_detected(self):
        """
        The gap this closes. `care_notes.version` advances on a care-note save;
        editing the text of a timeline entry can leave it untouched. Version
        alone would report this as current while the quote no longer exists.
        """
        edited = SOURCE.replace("10mg", "100mg")
        assert verify_quote(
            stored_hash=quote_hash(QUOTE), source_text=edited,
            stored_version=3, current_version=3,   # version says nothing changed
        ) is ProvenanceVerdict.MODIFIED

    def test_unrelated_edit_does_not_fire(self):
        """
        A tag that always fires is a tag nobody reads. An edit to a different
        sentence leaves the supporting quote intact, so the highlight is current.
        """
        elsewhere = SOURCE.replace("dizziness", "nausea")
        assert verify_quote(
            stored_hash=quote_hash(QUOTE), source_text=elsewhere
        ) is ProvenanceVerdict.CURRENT

    def test_hash_wins_over_a_reassuring_version(self):
        """Never take the better of the two verdicts."""
        edited = SOURCE.replace("10mg", "100mg")
        assert verify_quote(
            stored_hash=quote_hash(QUOTE), source_text=edited,
            stored_version=1, current_version=1,
        ) is ProvenanceVerdict.MODIFIED

    def test_deleted_source_is_distinct_from_modified(self):
        """
        Different facts. One means the record changed; the other means the
        supporting entry is gone, which a clinician needs to know is not the
        same thing.
        """
        assert verify_quote(
            stored_hash=quote_hash(QUOTE), source_text=None
        ) is ProvenanceVerdict.SOURCE_DELETED

    def test_untracked_highlight_is_unverifiable_not_current(self):
        """
        Everything unknown resolves toward "not current". A false warning costs
        a glance; a false assurance lets a clinician act on a quote the record
        no longer contains.
        """
        assert verify_quote(
            stored_hash=None, source_text=SOURCE
        ) is ProvenanceVerdict.UNVERIFIABLE
        assert not ProvenanceVerdict.UNVERIFIABLE.is_current

    def test_cosmetic_reflow_stays_current(self):
        reflowed = SOURCE.replace(". ", ".\n")
        assert verify_quote(
            stored_hash=quote_hash(QUOTE), source_text=reflowed
        ) is ProvenanceVerdict.CURRENT


# ---------------------------------------------------------------------------
# Session minting — turning a verified phone into a real sign-in
# ---------------------------------------------------------------------------


class TestSessionMinting:
    """
    The step that makes OTP an actual login rather than a verification oracle.

    The tempting shortcut — self-signing an HS256 JWT with SUPABASE_JWT_SECRET —
    was measured against this project and *works today*, because the legacy
    symmetric secret is still enabled alongside the ES256 keys the project
    publishes. It is still refused here: a self-signed token has no GoTrue
    session to revoke, no refresh token, and stops verifying on the day Supabase
    disables symmetric secrets, which is a total patient lockout at a date we do
    not control.
    """

    def test_only_patients_get_a_session(self):
        """
        The escalation this blocks: a clinician's phone in `profiles` would turn
        an SMS into a password-free path into a clinical account. Anyone who
        controls a staff number for sixty seconds becomes that clinician.
        """
        from services.session import MINTABLE_ROLES, SessionMintError, mint_session

        assert set(MINTABLE_ROLES) == {"patient"}
        for role in ["clinician", "staff", "admin", "system", ""]:
            with pytest.raises(SessionMintError):
                mint_session(profile_id="p1", phone="+6591234567", role=role)

    def test_sentinel_address_is_structurally_undeliverable(self):
        """
        `.invalid` is reserved by RFC 2606 and cannot resolve. The address is an
        internal primary key that happens to be email-shaped because GoTrue
        requires one — never a claim that the patient is reachable there.
        """
        from services.session import SENTINEL_EMAIL_DOMAIN, sentinel_email

        assert SENTINEL_EMAIL_DOMAIN.endswith(".invalid")
        assert sentinel_email("abc").endswith(".invalid")

    def test_sentinel_is_deterministic_and_collision_free(self):
        """
        Deterministic so a returning patient finds their existing account rather
        than accumulating a second one; derived from the profile id so two
        clinics cannot both reach for the same address — which is precisely the
        collision that made invented front-desk emails dangerous.
        """
        from services.session import sentinel_email

        assert sentinel_email("p1") == sentinel_email("p1")
        assert sentinel_email("p1") != sentinel_email("p2")

    def test_no_self_signed_jwt_path_exists(self):
        """
        Asserted structurally, because this is the shortcut a future change is
        most likely to reach for — it works, right up until it doesn't.
        """
        import ast
        import pathlib

        source = pathlib.Path("services/session.py").read_text()
        tree = ast.parse(source)
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "encode" not in called, "session.py signs its own token instead of using GoTrue"

        # AST again, not text: the module docstring names SUPABASE_JWT_SECRET
        # while explaining why it must not be read, and a grep cannot tell an
        # explanation from a lookup. Check what is actually passed to
        # os.environ.get / os.environ[...] instead.
        env_keys = {
            arg.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for arg in node.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        } | {
            node.slice.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        }
        assert "SUPABASE_JWT_SECRET" not in env_keys, (
            "session.py reads the symmetric secret, which means it is signing its own token"
        )

    def test_session_is_redeemed_with_the_anon_key(self):
        """
        The magiclink exchange must run as an ordinary client would. Redeeming
        with the service key risks a session carrying service-role authority —
        an RLS bypass handed to a patient's browser.
        """
        import pathlib

        source = pathlib.Path("services/session.py").read_text()
        verify_call = source[source.index('f"{base}/auth/v1/verify"'):]
        assert '"apikey": anon' in verify_call[:400]

    def test_response_carries_a_refresh_token(self):
        """
        Without one, @supabase/ssr cannot maintain the session and the patient is
        logged out mid-consultation with no way to continue.
        """
        from routers.auth_otp import VerifyOTPResponse

        fields = VerifyOTPResponse.model_fields
        assert {"access_token", "refresh_token", "expires_in"} <= set(fields)
