"""
PHI redaction — zero-leakage assertions.

These are pure unit tests: no Supabase, no Groq, no network. They run in any
checkout with the venv installed, which matters because the five integration
suites cannot run without live credentials.

The contract under test is guardrails S4: no PHI reaches an LLM unredacted, and
names ARE PHI. Every assertion below is written as "this string must NOT appear
in the text we would send to Groq".
"""

from __future__ import annotations

import re

import pytest

from services.redaction import (
    ENTITY_NRIC,
    ENTITY_PERSON,
    ENTITY_PHONE,
    RedactionMap,
    assert_no_residual_placeholders,
    cleanup_redaction_map,
    de_redact,
    redact,
    validate_and_repair_placeholders,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --------------------------------------------------------------------------
# Singapore identifiers
# --------------------------------------------------------------------------

SG_NRICS = [
    "S1234567D",   # citizen, 1900s
    "T7654321J",   # citizen, 2000s
    "F1234567N",   # foreign, pre-2022
    "G7654321X",   # foreign
    "M1234567K",   # foreign, M series introduced 2022
]


@pytest.mark.parametrize("nric", SG_NRICS)
def test_nric_never_survives_redaction(nric: str) -> None:
    """Every NRIC/FIN series, including the 2022 M series, must be removed."""
    text = f"Patient presented today. NRIC {nric}. Reviewed labs."
    redacted, rmap = redact(text)
    assert nric not in redacted, f"NRIC {nric} leaked into LLM payload"
    assert rmap.entity_counts.get(ENTITY_NRIC, 0) >= 1
    cleanup_redaction_map(rmap.id)


SG_PHONES = [
    "91234567",        # mobile
    "81234567",        # mobile
    "61234567",        # landline
    "+65 9123 4567",   # country code, conventional spacing
    "+6591234567",     # country code, no spacing
    "+65-9123-4567",   # country code, dashes
]


@pytest.mark.parametrize("phone", SG_PHONES)
def test_sg_phone_never_survives_redaction(phone: str) -> None:
    """All SG formats: 6/8/9 prefixes, optional +65, optional internal spacing."""
    text = f"Contact the patient on {phone} to confirm the appointment."
    redacted, rmap = redact(text)
    assert phone not in redacted, f"Phone {phone} leaked into LLM payload"
    # The bare digits must not survive either, even if spacing was normalised.
    assert re.sub(r"[\s+-]", "", phone) not in re.sub(r"[\s+-]", "", redacted)
    assert rmap.entity_counts.get(ENTITY_PHONE, 0) >= 1
    cleanup_redaction_map(rmap.id)


# --------------------------------------------------------------------------
# Names — the entity the previous regex-only implementation missed entirely
# --------------------------------------------------------------------------

NAMES_IN_CONTEXT = [
    ("Alice Wong", "Alice Wong reports feeling dizzy in the mornings."),
    ("Sarah Chen", "Reviewed by Dr. Sarah Chen on the same afternoon."),
    ("James Rivera", "Nurse James Rivera recorded the vitals at 14:15."),
    ("Tan Ah Kow", "Patient: Tan Ah Kow, seen for a routine review."),
    ("Nurul Aisyah binte Rahman", "Spoke with Mdm Nurul Aisyah binte Rahman about discharge."),
]


@pytest.mark.parametrize("name,text", NAMES_IN_CONTEXT)
def test_person_names_never_survive_redaction(name: str, text: str) -> None:
    """
    Western and local SG naming conventions alike must be removed.

    This is the specific failure the Phase 1 audit demonstrated: the previous
    pipeline had no PERSON entity, so 'Alice Wong' and 'Dr. Sarah Chen' were
    sent to Groq verbatim.
    """
    redacted, rmap = redact(text)
    assert name not in redacted, f"Name {name!r} leaked into LLM payload"
    assert rmap.entity_counts.get(ENTITY_PERSON, 0) >= 1
    cleanup_redaction_map(rmap.id)


def test_deny_list_catches_names_the_model_misses() -> None:
    """
    A caller-supplied name is redacted by exact match regardless of NER recall.

    The scribe endpoint passes the patient's display_name this way, so coverage
    of the single most sensitive identifier does not depend on model recall.
    """
    text = "Kiasu Lim Ah Seng attended the clinic and denied chest pain."
    redacted, rmap = redact(text, extra_names=["Kiasu Lim Ah Seng"])
    assert "Kiasu Lim Ah Seng" not in redacted
    cleanup_redaction_map(rmap.id)


def test_deny_list_is_case_insensitive() -> None:
    redacted, rmap = redact("spoke to alice wong by phone", extra_names=["Alice Wong"])
    assert "alice wong" not in redacted.lower()
    cleanup_redaction_map(rmap.id)


# --------------------------------------------------------------------------
# Combined payload
# --------------------------------------------------------------------------

def test_no_phi_of_any_kind_reaches_the_llm() -> None:
    """One realistic note; assert every identifier in it is gone at once."""
    text = (
        "Alice Wong (NRIC S1234567D, mobile 91234567, MRN 445566) was reviewed by "
        "Dr. Sarah Chen. Emergency contact Tan Ah Kow on +65 9123 4567, "
        "email alice.wong@example.com. eGFR fell from 62 to 45."
    )
    redacted, rmap = redact(text, extra_names=["Alice Wong"])

    for secret in [
        "Alice Wong", "Sarah Chen", "Tan Ah Kow",
        "S1234567D", "91234567", "9123 4567",
        "445566", "alice.wong@example.com",
    ]:
        assert secret not in redacted, f"{secret!r} leaked into LLM payload"

    # Clinical signal must survive, or the summary is worthless.
    assert "eGFR" in redacted
    assert "62" in redacted and "45" in redacted
    cleanup_redaction_map(rmap.id)


def test_clinical_values_are_not_over_redacted() -> None:
    """Vitals and lab values must not be mistaken for identifiers."""
    text = (
        "BP 130/80, HR 72, Temp 36.8C, SpO2 98%. Potassium 5.1, creatinine 1.4, "
        "eGFR 45 mL/min. Lisinopril 10mg daily."
    )
    redacted, rmap = redact(text)
    assert redacted == text, f"Clinical values were altered: {redacted}"
    assert rmap.total_entities == 0
    cleanup_redaction_map(rmap.id)


def test_redaction_is_reversible() -> None:
    text = "Alice Wong, NRIC S1234567D, called 91234567."
    redacted, rmap = redact(text, extra_names=["Alice Wong"])
    assert de_redact(redacted, rmap.id) == text
    cleanup_redaction_map(rmap.id)


def test_repeated_value_reuses_one_placeholder() -> None:
    """The same identifier twice must map to one placeholder, not two."""
    redacted, rmap = redact(
        "Alice Wong arrived. Alice Wong was reviewed.", extra_names=["Alice Wong"]
    )
    assert redacted.count("<PERSON_1>") == 2
    assert rmap.entity_counts[ENTITY_PERSON] == 1
    cleanup_redaction_map(rmap.id)


def test_expired_map_raises_rather_than_silently_failing() -> None:
    _, rmap = redact("Alice Wong called.", extra_names=["Alice Wong"])
    cleanup_redaction_map(rmap.id)
    with pytest.raises(KeyError):
        de_redact("<PERSON_1> called.", rmap.id)


# --------------------------------------------------------------------------
# Placeholder integrity (red-team item 2)
# --------------------------------------------------------------------------

def _map_with(*pairs: tuple[str, str]) -> RedactionMap:
    rmap = RedactionMap()
    for value, entity in pairs:
        rmap.add(value, entity)
    return rmap


@pytest.mark.parametrize(
    "corrupted",
    ["[Person 1]", "(PERSON 1)", "<PERSON 1>", "PERSON_1", "[PERSON_1]", "<person_1>"],
)
def test_corrupted_placeholders_are_repaired(corrupted: str) -> None:
    """
    LLMs rewrite '<PERSON_1>' in predictable ways. Each variant must be
    normalised back so restoration works instead of leaking the token.
    """
    rmap = _map_with(("Alice Wong", ENTITY_PERSON))
    report = validate_and_repair_placeholders(f"The patient {corrupted} is stable.", rmap)
    assert report.ok, f"{corrupted!r} was not recognised: unknown={report.unknown}"
    assert "<PERSON_1>" in report.repaired_text
    assert corrupted in report.recovered or corrupted == "<PERSON_1>"


def test_unknown_placeholder_fails_validation() -> None:
    """A token we never issued means the model invented one — refuse the text."""
    rmap = _map_with(("Alice Wong", ENTITY_PERSON))
    report = validate_and_repair_placeholders("<PERSON_1> and <PERSON_7> spoke.", rmap)
    assert not report.ok
    assert "<PERSON_7>" in report.unknown


def test_missing_placeholder_detected_when_required() -> None:
    rmap = _map_with(("Alice Wong", ENTITY_PERSON), ("S1234567D", ENTITY_NRIC))
    report = validate_and_repair_placeholders("<PERSON_1> is stable.", rmap, expect_all=True)
    assert not report.ok
    assert "<NRIC_1>" in report.missing


def test_residual_placeholder_detection() -> None:
    assert assert_no_residual_placeholders("Alice Wong is stable.") == []
    assert assert_no_residual_placeholders("<PERSON_1> is stable.") == ["<PERSON_1>"]


def test_repair_then_restore_round_trip() -> None:
    """The full defence: corrupt output is repaired, restored, and verified clean."""
    text = "Alice Wong called 91234567."
    redacted, rmap = redact(text, extra_names=["Alice Wong"])

    # Simulate the model mangling both placeholders.
    mangled = redacted.replace("<PERSON_1>", "[Person 1]").replace("<PHONE_1>", "(PHONE 1)")
    report = validate_and_repair_placeholders(mangled, rmap)
    assert report.ok

    restored = de_redact(report.repaired_text, rmap.id)
    assert "Alice Wong" in restored
    assert "91234567" in restored
    assert assert_no_residual_placeholders(restored) == []
    cleanup_redaction_map(rmap.id)


class TestPlaceholderRepairDoesNotCorruptCorrectOutput:
    """
    Regression: the repair pass used to corrupt well-formed output.

    The bare-token corruption pattern also matched the `PERSON_1` *inside* an
    already-correct `<PERSON_1>` and re-wrapped it as `<<PERSON_1>>`, which
    de-redacted to `<Alice Wong>` — stray angle brackets in a clinical note.

    The failure hit the CORRECT case: a model that followed the placeholder
    guard exactly had its output mangled, while a model that mangled the syntax
    was repaired properly. It affected every AI path and was invisible until an
    end-to-end test inspected a real response body.
    """

    def test_well_formed_placeholder_is_left_alone(self):
        text = "Alice Wong called about her results."
        redacted, rmap = redact(text, extra_names=["Alice Wong"])

        report = validate_and_repair_placeholders(f"Summary: {redacted}", rmap)

        assert report.ok
        assert report.recovered == [], "a correct placeholder was treated as corrupt"
        assert "<<" not in report.repaired_text
        assert ">>" not in report.repaired_text
        assert de_redact(report.repaired_text, rmap.id) == "Summary: Alice Wong called about her results."
        cleanup_redaction_map(rmap.id)

    def test_round_trip_leaves_no_stray_brackets(self):
        """The end state a clinician sees: clean prose, no artefacts."""
        text = "Alice Wong, NRIC S1234567D, called 91234567 about her results."
        redacted, rmap = redact(text, extra_names=["Alice Wong"])
        report = validate_and_repair_placeholders(redacted, rmap)
        restored = de_redact(report.repaired_text, rmap.id)

        assert restored == text
        for artefact in ["<<", ">>", "<Alice", "<S1234567D"]:
            assert artefact not in restored, f"stray artefact in the record: {artefact!r}"
        cleanup_redaction_map(rmap.id)

    @pytest.mark.parametrize("corrupted", [
        "[Person 1]", "(PERSON 1)", "<PERSON 1>", "PERSON_1", "[PERSON_1]",
    ])
    def test_genuinely_corrupted_placeholders_are_still_repaired(self, corrupted):
        """The fix must not have disabled repair for output that IS mangled."""
        rmap = _map_with(("Alice Wong", ENTITY_PERSON))
        report = validate_and_repair_placeholders(f"The patient {corrupted} is stable.", rmap)
        assert report.ok, f"{corrupted!r} was no longer repaired: {report.unknown}"
        assert "<PERSON_1>" in report.repaired_text

    def test_sg_identity_labels_are_not_redacted_as_people(self):
        """
        'IC', 'NRIC' and 'FIN' are Singapore identity-card LABELS, not
        identifiers. Redacting the word destroys the sentence while protecting
        nothing — the number beside it is what matters, and the NRIC recogniser
        already removes that.
        """
        text = "Can you confirm your full name and IC for me?"
        redacted, rmap = redact(text)
        assert redacted == text
        assert rmap.total_entities == 0
        cleanup_redaction_map(rmap.id)

        # The number beside the label is still removed.
        redacted, rmap = redact("IC number is S1234567D.")
        assert "S1234567D" not in redacted
        assert "IC number is" in redacted
        cleanup_redaction_map(rmap.id)
