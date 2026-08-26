"""
PHI redaction pipeline: Microsoft Presidio + spaCy NER, hardened for Singapore.

Guardrails S4/S5: no PHI reaches an LLM unredacted, and names ARE PHI. The
previous implementation was pure regex with no PERSON entity at all, while the
README and the endpoint description both advertised Presidio + spaCy. Patient
and clinician names went to Groq in the clear.

Detection runs in three layers, because no single one is sufficient:

  1. spaCy `en_core_web_sm` NER via Presidio  — general PERSON/LOCATION/ORG
  2. Custom SG PatternRecognizers            — NRIC/FIN, +65 phones, MRN
  3. Context and title recognizers           — "Dr. Sarah Chen", "Mdm Tan Ah Kow"

Layer 3 exists because en_core_web_sm is trained on US/EU news text and
routinely misses Chinese, Malay, and Tamil name forms common in Singapore
("Tan Ah Kow", "Nurul Aisyah binte Rahman"). Callers that already know a name
should also pass it via `extra_names` — an exact deny-list match is the highest
precision signal available and is not subject to model recall at all.

Redaction maps are held server-side only and never returned to a client.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Entity naming
# ---------------------------------------------------------------------------
# Placeholder text is derived directly from the entity label, so these names are
# part of the external contract: NRIC -> "<NRIC_1>", PERSON -> "<PERSON_1>".

ENTITY_NRIC = "NRIC"
ENTITY_PHONE = "PHONE"
ENTITY_MRN = "MRN"
ENTITY_PERSON = "PERSON"

# Entities we ask Presidio for. Deliberately excludes DATE_TIME: clinical
# reasoning depends on relative timing ("eGFR fell over 6 months"), and stripping
# every date destroys the signal the summary exists to surface. Dates alone are
# low re-identification risk once names, NRIC, phone, and MRN are gone.
_PRESIDIO_ENTITIES = [
    ENTITY_PERSON,
    "EMAIL_ADDRESS",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "URL",
    "LOCATION",
    ENTITY_NRIC,
    ENTITY_PHONE,
    ENTITY_MRN,
]

# ---------------------------------------------------------------------------
# Singapore-specific patterns
# ---------------------------------------------------------------------------

# NRIC/FIN. S/T = citizens & PRs by century, F/G/M = foreign identification.
# M was introduced in 2022 and is missed by every off-the-shelf recognizer.
_NRIC_REGEX = r"\b[STFGM]\d{7}[A-Z]\b"

# SG numbers are 8 digits opening with 6 (landline), 8 or 9 (mobile), with an
# optional +65 country code. The negative lookarounds stop the pattern eating
# the tail of a longer digit run such as a lab accession number.
_PHONE_REGEX = r"(?<!\d)(?:\+65[\s-]?)?[689]\d{3}[\s-]?\d{4}(?!\d)"

_MRN_REGEX = r"\bMRN[:\s#-]?\s?\d{6,10}\b"

# Clinical and honorific titles followed by 1-4 capitalised tokens. Covers
# "Dr. Sarah Chen", "Mdm Tan Ah Kow", "Nurse James Rivera".
_NAME_PARTICLE = r"binte|binti|bin|a/l|a/p|s/o|d/o"

# Title, then optional initials, then at least one real name token.
# "Mr K Lim" and "Dr. J Tan" were previously missed because the name token was
# required to be two characters or more, so a single-letter given name fell
# through to spaCy — which caught one of them and not the other.
_TITLE_REGEX = (
    r"\b(?:[Dd]r|[Dd]octor|[Pp]rof|[Pp]rofessor|[Nn]urse|[Ss]ister|[Mm]atron"
    r"|[Mm]r|[Mm]rs|[Mm]s|[Mm]iss|[Mm]dm|[Mm]adam)"
    r"\.?\s+(?:[A-Z]\.?\s+){0,2}"
    r"[A-Z][a-zA-Z'-]+(?:\s+(?:" + _NAME_PARTICLE + r"|[A-Z][a-zA-Z'-]+)){0,3}"
)

# Names introduced by an explicit role label, e.g. "Patient: Tan Ah Kow".
# Anchored on the label so ordinary capitalised prose is not swept up.
# The LABEL is matched case-insensitively via character classes while the NAME
# stays case-sensitive. Previously the whole pattern was case-sensitive, so a
# capitalised "Patient:" -- the way it is actually written -- never matched.
_LABEL_WORD = (
    r"(?:[Pp]atient|[Pp]t|[Cc]lient|[Rr]esident|[Cc]aregiver|"
    r"[Nn]ext[- ]of[- ]kin|NOK|[Gg]uardian|[Ss]pouse|[Nn]ame)"
)
_LABELLED_NAME_REGEX = (
    r"(?:\b" + _LABEL_WORD + r"\b\s*(?:[Nn]ame)?\s*[:\-]\s*)"
    r"([A-Z][a-zA-Z'-]+(?:\s+(?:" + _NAME_PARTICLE + r"|[A-Z][a-zA-Z'-]+)){0,3})"
)

# PHI inside structured payloads. A name in a JSON string value or a key=value
# pair is still a name, but NER sees a quoted token rather than a sentence and
# routinely misses it. Structure must not be a hiding place.
_STRUCTURED_NAME_REGEX = (
    r"(?:[\"']?\b\w*(?:patient|full|first|last|given|family|display|contact)?[_-]?name\w*"
    r"[\"']?\s*[:=]\s*)"
    r"[\"']?([A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+){0,3})[\"']?"
)

# CJK personal names. en_core_web_sm has no coverage for these at all, and in a
# Singapore deployment they are common. A run of 2-4 CJK ideographs inside an
# otherwise English clinical note is overwhelmingly a name; the bounded length
# keeps this from swallowing longer Chinese prose.
_CJK_NAME_REGEX = r"[\u4e00-\u9fff\u3040-\u30ff]{2,4}"


# spaCy's en_core_web_sm reliably mislabels medication names as PERSON
# ("Lisinopril", "Metformin"). Redacting them fails safe for privacy but
# destroys the clinical signal a summary exists to carry, so PERSON spans that
# match a known clinical term are dropped.
#
# This list is a mitigation, not a solution: an unusual drug name not listed
# here will still be redacted. That is the acceptable failure direction — we
# lose utility, never privacy. Extend it as real transcripts surface gaps.
_CLINICAL_ALLOWLIST = {
    # ACE inhibitors / ARBs
    "lisinopril", "ramipril", "enalapril", "perindopril", "losartan", "valsartan",
    "irbesartan", "candesartan", "telmisartan",
    # Diabetes
    "metformin", "ozempic", "semaglutide", "insulin", "glipizide", "gliclazide",
    "sitagliptin", "empagliflozin", "dapagliflozin", "januvia", "jardiance",
    # Cardiovascular
    "amlodipine", "atorvastatin", "simvastatin", "rosuvastatin", "bisoprolol",
    "metoprolol", "carvedilol", "furosemide", "frusemide", "spironolactone",
    "warfarin", "apixaban", "rivaroxaban", "clopidogrel", "aspirin", "digoxin",
    "hydrochlorothiazide", "nifedipine", "diltiazem", "isosorbide",
    # Analgesia / common
    "paracetamol", "acetaminophen", "ibuprofen", "naproxen", "tramadol",
    "codeine", "morphine", "omeprazole", "pantoprazole", "ranitidine",
    "famotidine", "prednisolone", "salbutamol", "levothyroxine", "allopurinol",
    "amoxicillin", "augmentin", "azithromycin", "ciprofloxacin", "doxycycline",
    "atenolol", "gabapentin", "sertraline", "statin", "statins",
    # Clinical vocabulary occasionally tagged PERSON
    "creatinine", "potassium", "sodium", "glucose", "albumin", "hba1c",
    "cardiology", "nephrology", "dietitian", "physiotherapy", "hyperkalemia",
    "dyspnea", "dyspnoea", "oedema", "edema", "systolic", "diastolic",
}


# Patterns whose NAME lives in capture group 1. Presidio replaces the whole
# match, which would swallow the surrounding label ("Patient:", "name=") and
# damage the clinical text, so these are applied here with group-scoped spans
# instead — the label survives, only the name is replaced.
_GROUP_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = []


def _init_group_patterns() -> None:
    if _GROUP_PATTERNS:
        return
    _GROUP_PATTERNS.extend([
        (ENTITY_PERSON, re.compile(_LABELLED_NAME_REGEX, re.MULTILINE)),
        (ENTITY_PERSON, re.compile(_STRUCTURED_NAME_REGEX, re.MULTILINE)),
    ])


def _is_clinical_term(value: str) -> bool:
    """True if every word of `value` is known clinical vocabulary."""
    words = re.findall(r"[A-Za-z]+", value.lower())
    return bool(words) and all(w in _CLINICAL_ALLOWLIST for w in words)


def _build_analyzer():
    """
    Construct the Presidio analyzer with spaCy plus the SG recognizers.

    Import-time cost is significant (the spaCy model load dominates), so this is
    called once behind a lock and cached for the process lifetime.
    """
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    )
    analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])

    custom = [
        PatternRecognizer(
            supported_entity=ENTITY_NRIC,
            name="sg_nric_recognizer",
            patterns=[Pattern(name="sg_nric", regex=_NRIC_REGEX, score=0.95)],
            context=["nric", "fin", "ic", "identification", "identity"],
        ),
        PatternRecognizer(
            supported_entity=ENTITY_PHONE,
            name="sg_phone_recognizer",
            patterns=[Pattern(name="sg_phone", regex=_PHONE_REGEX, score=0.8)],
            context=["phone", "mobile", "hp", "contact", "tel", "call"],
        ),
        PatternRecognizer(
            supported_entity=ENTITY_MRN,
            name="mrn_recognizer",
            patterns=[Pattern(name="mrn", regex=_MRN_REGEX, score=0.9)],
            context=["mrn", "record", "chart"],
        ),
        # global_regex_flags omits re.IGNORECASE deliberately: Presidio applies
        # it by default, which would turn every [A-Z] below into [A-Za-z] and
        # let the match run past the name into ordinary lowercase prose.
        PatternRecognizer(
            supported_entity=ENTITY_PERSON,
            name="clinical_title_recognizer",
            patterns=[Pattern(name="titled_name", regex=_TITLE_REGEX, score=0.85)],
            global_regex_flags=re.MULTILINE,
        ),
        PatternRecognizer(
            supported_entity=ENTITY_PERSON,
            name="cjk_name_recognizer",
            patterns=[Pattern(name="cjk_name", regex=_CJK_NAME_REGEX, score=0.7)],
            global_regex_flags=re.MULTILINE,
        ),
    ]
    for rec in custom:
        analyzer.registry.add_recognizer(rec)

    logger.info("Presidio analyzer ready (spaCy en_core_web_sm + %d SG recognizers)", len(custom))
    return analyzer


_analyzer = None
_analyzer_lock = threading.Lock()


def get_analyzer():
    """Lazily build and cache the analyzer. Thread-safe."""
    global _analyzer
    if _analyzer is None:
        with _analyzer_lock:
            if _analyzer is None:
                _analyzer = _build_analyzer()
    return _analyzer


def warmup() -> None:
    """Pre-load the model so the first request is not penalised. Called at startup."""
    get_analyzer().analyze(text="warmup", language="en", entities=_PRESIDIO_ENTITIES)


# ---------------------------------------------------------------------------
# Redaction map
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"<([A-Z_]+)_(\d+)>")


@dataclass
class RedactionMap:
    """Server-side only mapping between original PHI and placeholders."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    forward: dict[str, str] = field(default_factory=dict)
    reverse: dict[str, str] = field(default_factory=dict)
    entity_counts: dict[str, int] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def add(self, original: str, entity_type: str) -> str:
        """Register an original value and return a stable placeholder for it."""
        if original in self.forward:
            return self.forward[original]
        count = self.entity_counts.get(entity_type, 0) + 1
        self.entity_counts[entity_type] = count
        placeholder = f"<{entity_type}_{count}>"
        self.forward[original] = placeholder
        self.reverse[placeholder] = original
        return placeholder

    @property
    def total_entities(self) -> int:
        return len(self.forward)

    @property
    def placeholders(self) -> set[str]:
        return set(self.reverse)


# Bounded LRU store with TTL. The previous implementation was an unbounded dict
# that leaked on every path not covered by a router's finally block.
_MAX_MAPS = 512
_MAP_TTL_SECONDS = 3600
_redaction_store: "OrderedDict[str, RedactionMap]" = OrderedDict()
_store_lock = threading.Lock()


def _store(rmap: RedactionMap) -> None:
    with _store_lock:
        now = time.time()
        for key in [k for k, v in _redaction_store.items() if now - v.created_at > _MAP_TTL_SECONDS]:
            _redaction_store.pop(key, None)
        _redaction_store[rmap.id] = rmap
        _redaction_store.move_to_end(rmap.id)
        while len(_redaction_store) > _MAX_MAPS:
            _redaction_store.popitem(last=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def redact(text: str, *, extra_names: Sequence[str] = ()) -> tuple[str, RedactionMap]:
    """
    Remove PHI from `text`, returning the redacted text and its redaction map.

    Args:
        text: raw clinical text.
        extra_names: known names (patient, clinician) to redact by exact match
            regardless of what the model detects. Highest-precision layer.

    Returns:
        (redacted_text, redaction_map). The map is server-side only and must
        never be sent to a client.
    """
    if not text or not text.strip():
        empty = RedactionMap()
        _store(empty)
        return text, empty

    spans: list[tuple[int, int, str, float]] = []

    # Layer 1 + 2: Presidio (spaCy NER plus the registered SG recognizers).
    for result in get_analyzer().analyze(
        text=text, language="en", entities=_PRESIDIO_ENTITIES
    ):
        matched = text[result.start : result.end]
        # Drop medication and clinical terms misdetected as people.
        if result.entity_type in (ENTITY_PERSON, "LOCATION") and _is_clinical_term(matched):
            logger.debug("Ignoring clinical term detected as %s: %r", result.entity_type, matched)
            continue
        spans.append((result.start, result.end, result.entity_type, float(result.score)))

    # Layer 2b: label- and structure-anchored names, scoped to capture group 1
    # so the surrounding label is preserved.
    _init_group_patterns()
    for entity_type, pattern in _GROUP_PATTERNS:
        for m in pattern.finditer(text):
            if m.group(1):
                spans.append((m.start(1), m.end(1), entity_type, 0.85))

    # Layer 3: caller-supplied deny-list, matched case-insensitively on word
    # boundaries. Score 1.0 so it always wins overlap resolution.
    for name in extra_names:
        name = (name or "").strip()
        if len(name) < 2:
            continue
        for m in re.finditer(rf"\b{re.escape(name)}\b", text, flags=re.IGNORECASE):
            spans.append((m.start(), m.end(), ENTITY_PERSON, 1.0))

    if not spans:
        empty = RedactionMap()
        _store(empty)
        return text, empty

    # Resolve overlaps: prefer higher score, then the longer span. Presidio can
    # return e.g. PERSON "Sarah Chen" and the title recognizer "Dr. Sarah Chen";
    # keeping the longer, higher-scoring one avoids a half-redacted name.
    spans.sort(key=lambda s: (-s[3], -(s[1] - s[0]), s[0]))
    kept: list[tuple[int, int, str, float]] = []
    for span in spans:
        if not any(span[0] < k[1] and span[1] > k[0] for k in kept):
            kept.append(span)

    rmap = RedactionMap()
    # Assign placeholders in reading order so <PERSON_1> is the first person
    # mentioned — this makes LLM output far easier to reason about.
    for start, end, entity_type, _ in sorted(kept, key=lambda s: s[0]):
        rmap.add(text[start:end], entity_type)

    # Substitute back-to-front so earlier offsets stay valid.
    redacted = text
    for start, end, entity_type, _ in sorted(kept, key=lambda s: s[0], reverse=True):
        redacted = redacted[:start] + rmap.forward[text[start:end]] + redacted[end:]

    _store(rmap)
    logger.info(
        "Redacted %d entities (%s) from %d chars",
        rmap.total_entities,
        ", ".join(f"{k}:{v}" for k, v in sorted(rmap.entity_counts.items())) or "none",
        len(text),
    )
    return redacted, rmap


def get_map(map_id: str) -> RedactionMap | None:
    with _store_lock:
        return _redaction_store.get(map_id)


def de_redact(redacted_text: str, map_id: str) -> str:
    """Restore original values. Raises KeyError if the map expired."""
    rmap = get_map(map_id)
    if rmap is None:
        raise KeyError(f"Redaction map '{map_id}' not found or has expired")
    result = redacted_text
    for placeholder, original in rmap.reverse.items():
        result = result.replace(placeholder, original)
    return result


def cleanup_redaction_map(map_id: str) -> bool:
    with _store_lock:
        return _redaction_store.pop(map_id, None) is not None


# ---------------------------------------------------------------------------
# Placeholder integrity (red-team item 2)
# ---------------------------------------------------------------------------
# LLMs mangle placeholder syntax: "<PERSON_1>" comes back as "[Person 1]",
# "<PERSON 1>", or "PERSON_1". Naive string restoration then leaves the token in
# the clinical note, or silently fails to restore. We normalise the common
# corruptions, then verify nothing unknown survives.

_CORRUPTION_PATTERNS = [
    re.compile(r"[\[\(<]\s*([A-Za-z_]+?)[\s_]+(\d+)\s*[\]\)>]"),  # [Person 1] (PERSON_1) <person 1>
    re.compile(r"\b([A-Z][A-Za-z]*?)_(\d+)\b"),                    # bare PERSON_1
]


@dataclass
class PlaceholderReport:
    """Outcome of validating an LLM response against its redaction map."""

    ok: bool
    repaired_text: str
    recovered: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def validate_and_repair_placeholders(
    text: str, rmap: RedactionMap, *, expect_all: bool = False
) -> PlaceholderReport:
    """
    Normalise corrupted placeholders in an LLM response and report integrity.

    Args:
        text: the model's output.
        rmap: the map used to redact the prompt.
        expect_all: when True, every placeholder issued must reappear.

    Returns:
        PlaceholderReport. `ok` is False if any placeholder-shaped token cannot
        be matched to this map — callers must not store such text unreviewed.
    """
    known = rmap.placeholders
    recovered: list[str] = []

    def _fix(match: re.Match[str]) -> str:
        label, num = match.group(1), match.group(2)
        candidate = f"<{label.upper()}_{num}>"
        if candidate in known:
            if match.group(0) != candidate:
                recovered.append(match.group(0))
            return candidate
        return match.group(0)

    repaired = text
    for pattern in _CORRUPTION_PATTERNS:
        repaired = pattern.sub(_fix, repaired)

    found = {m.group(0) for m in _PLACEHOLDER_RE.finditer(repaired)}
    unknown = sorted(found - known)
    missing = sorted(known - found) if expect_all else []

    if recovered:
        logger.warning("Repaired %d corrupted placeholder(s): %s", len(recovered), recovered)
    if unknown:
        logger.error("Response contains unknown placeholders: %s", unknown)

    return PlaceholderReport(
        ok=not unknown and not missing,
        repaired_text=repaired,
        recovered=recovered,
        unknown=unknown,
        missing=missing,
    )


def assert_no_residual_placeholders(text: str) -> list[str]:
    """Return any placeholder-shaped tokens left after de-redaction (want: none)."""
    return [m.group(0) for m in _PLACEHOLDER_RE.finditer(text)]
