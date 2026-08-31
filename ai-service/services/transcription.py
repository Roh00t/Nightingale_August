"""
Speech-to-text with diarization, mock-first.

ElevenLabs Scribe v2 is metered and the project budget is 10,000 credits, so
the default path spends nothing. A live call requires TWO independent opt-ins:

  1. the request asks for it   (?live=true)
  2. the deployment allows it  (ELEVENLABS_LIVE_ENABLED=true)

One switch is not enough. A stray query parameter in a test fixture, a copied
curl command, or a browser retry would each be sufficient on its own to start
burning credits, and the failure is silent until the balance is gone. Requiring
an environment flag as well means no request — however malformed or repeated —
can reach the meter unless someone deliberately enabled it on that machine.

The SDK is imported lazily inside the live branch. Mock mode therefore works
with the `elevenlabs` package absent entirely, which is what keeps the test
suite runnable on a clean checkout.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MODEL_ID = "scribe_v2"

# Hard ceiling enforced before any transcription is attempted.
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # 5 MB

# MediaRecorder emits webm on Chrome and mp4 on Safari; iOS sometimes reports
# a bare `audio/mpeg`. The bytes are passed through untouched — this list only
# rejects obvious non-audio uploads.
ACCEPTED_AUDIO_TYPES = {
    "audio/webm", "audio/mp4", "audio/mpeg", "audio/mpga", "audio/m4a",
    "audio/wav", "audio/x-wav", "audio/ogg", "audio/flac", "audio/aac",
    "video/webm",  # Chrome labels webm audio-only recordings this way
}


class TranscriptionUnavailable(RuntimeError):
    """Live transcription was requested but cannot be performed."""


@dataclass
class Segment:
    """One diarized utterance."""

    speaker: str
    text: str
    start: float | None = None
    end: float | None = None
    confidence: float | None = None

    def render(self) -> str:
        return f"{self.speaker}: {self.text}"


@dataclass
class Transcript:
    """A diarized transcript plus the metadata the clinical layer needs."""

    segments: list[Segment] = field(default_factory=list)
    language: str | None = None
    source: str = "mock"
    model_id: str = MODEL_ID

    @property
    def text(self) -> str:
        """
        Speaker-labelled dialogue.

        Labels are retained deliberately. The downstream summariser needs to know
        who said what — "I've been dizzy" from the patient and from the clinician
        mean different things — and provenance back to a segment depends on the
        label surviving redaction intact.
        """
        return "\n".join(s.render() for s in self.segments)

    @property
    def speakers(self) -> list[str]:
        seen: list[str] = []
        for s in self.segments:
            if s.speaker not in seen:
                seen.append(s.speaker)
        return seen

    @property
    def mean_confidence(self) -> float | None:
        values = [s.confidence for s in self.segments if s.confidence is not None]
        return sum(values) / len(values) if values else None

    def to_metadata(self) -> dict[str, Any]:
        """Provenance for the timeline entry. Carries no transcript text."""
        return {
            "source": self.source,
            "model_id": self.model_id,
            "language": self.language,
            "speaker_count": len(self.speakers),
            "segment_count": len(self.segments),
            "mean_confidence": (
                round(self.mean_confidence, 3) if self.mean_confidence is not None else None
            ),
        }


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------
# A realistic diarized consult carrying the PHI classes the redactor must catch
# — full name, NRIC, phone, DOB — plus a clinical value that must SURVIVE, so a
# test can prove redaction without over-redaction in the same fixture.

_MOCK_SEGMENTS: list[tuple[str, str, float, float, float]] = [
    ("Speaker 1", "Good morning. Can you confirm your full name and IC for me?", 0.0, 3.4, 0.96),
    ("Speaker 2", "Yes, it's Alice Wong, NRIC S1234567D.", 3.5, 7.1, 0.94),
    ("Speaker 1", "And your date of birth and contact number?", 7.2, 9.8, 0.95),
    ("Speaker 2", "14 March 1961, and my mobile is 91234567.", 9.9, 14.2, 0.92),
    ("Speaker 1", "Thank you. How have you been since the last visit?", 14.3, 17.0, 0.97),
    ("Speaker 2", "I've been getting short of breath climbing one flight of stairs.", 17.1, 22.6, 0.93),
    ("Speaker 1", "Your latest results show eGFR dropped to 45 and potassium 5.1.", 22.7, 28.9, 0.95),
    ("Speaker 1", "I'm increasing Lisinopril to 10mg daily and referring you to cardiology.", 29.0, 35.2, 0.96),
    ("Speaker 2", "Understood. Should I avoid anything in the meantime?", 35.3, 38.4, 0.94),
    ("Speaker 1", "Avoid high-potassium foods until we repeat the blood test.", 38.5, 42.7, 0.95),
]


def mock_transcript() -> Transcript:
    """Deterministic diarized transcript. Costs nothing and never varies."""
    return Transcript(
        segments=[
            Segment(speaker=sp, text=tx, start=st, end=en, confidence=cf)
            for sp, tx, st, en, cf in _MOCK_SEGMENTS
        ],
        language="en",
        source="mock",
    )


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------


def live_enabled() -> bool:
    """Whether this deployment permits metered calls at all."""
    return os.environ.get("ELEVENLABS_LIVE_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def _segments_from_words(words: list[Any]) -> list[Segment]:
    """
    Rebuild utterances from Scribe's word-level output.

    Consecutive words sharing a speaker_id collapse into one segment, which is
    what makes the transcript readable and what the summariser expects.
    """
    segments: list[Segment] = []
    current_speaker: str | None = None
    buffer: list[str] = []
    start: float | None = None
    end: float | None = None
    scores: list[float] = []

    def flush() -> None:
        if buffer and current_speaker is not None:
            segments.append(Segment(
                speaker=current_speaker,
                text=" ".join(buffer).strip(),
                start=start,
                end=end,
                confidence=(sum(scores) / len(scores)) if scores else None,
            ))

    for word in words:
        text = getattr(word, "text", None) or (word.get("text") if isinstance(word, dict) else None)
        if not text:
            continue
        raw_speaker = (
            getattr(word, "speaker_id", None)
            or (word.get("speaker_id") if isinstance(word, dict) else None)
            or "0"
        )
        # Scribe returns speaker_0 / 0 / "speaker_0" depending on version.
        digits = "".join(ch for ch in str(raw_speaker) if ch.isdigit()) or "0"
        speaker = f"Speaker {int(digits) + 1}"

        if speaker != current_speaker:
            flush()
            current_speaker, buffer, scores = speaker, [], []
            start = getattr(word, "start", None) or (word.get("start") if isinstance(word, dict) else None)

        buffer.append(text)
        end = getattr(word, "end", None) or (word.get("end") if isinstance(word, dict) else None)
        score = getattr(word, "logprob", None) or (word.get("logprob") if isinstance(word, dict) else None)
        if isinstance(score, (int, float)):
            scores.append(float(score))

    flush()
    return segments


def live_transcript(audio_bytes: bytes, *, filename: str = "audio.webm") -> Transcript:
    """
    Call ElevenLabs Scribe v2. METERED — every invocation spends credits.

    Raises TranscriptionUnavailable rather than falling back to the mock: a
    silent fallback would let a broken live path masquerade as a working one,
    and a clinician would have no way to tell a real transcript from a fixture.
    """
    if not live_enabled():
        raise TranscriptionUnavailable(
            "Live transcription is disabled. Set ELEVENLABS_LIVE_ENABLED=true to permit "
            "metered ElevenLabs calls on this deployment."
        )

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise TranscriptionUnavailable("ELEVENLABS_API_KEY is not set")

    try:
        # Imported here, not at module scope, so mock mode works without the
        # package installed and the test suite runs on a clean checkout.
        from elevenlabs.client import ElevenLabs
    except ImportError as exc:  # pragma: no cover - depends on install state
        raise TranscriptionUnavailable(
            "The `elevenlabs` package is not installed. Install it to use live transcription."
        ) from exc

    import io

    logger.warning(
        "METERED: calling ElevenLabs Scribe (%d bytes). This spends credits.",
        len(audio_bytes),
    )

    client = ElevenLabs(api_key=api_key)
    payload = io.BytesIO(audio_bytes)
    payload.name = filename  # the SDK infers the container from the filename

    # SOUTHEAST ASIAN CODE-SWITCHING
    #
    # A Singapore consult is routinely one sentence containing English, Malay and
    # Hokkien — "the doctor say your gula darah damn high already, must makan ubat
    # every day". Getting that transcribed is mostly about what NOT to send.
    #
    # `language_code` is deliberately omitted. Pinning it to "en" makes the model
    # decode the whole utterance under an English prior, and non-English spans come
    # back as the nearest English-sounding words rather than as themselves — so
    # "gula darah" (blood sugar) silently becomes plausible nonsense instead of an
    # obvious gap a clinician would catch. Auto-detection per segment is the
    # behaviour that keeps the switch intact.
    #
    # Note what this is NOT: Scribe has no Whisper-style free-text `prompt`
    # parameter to bias decoding with vocabulary hints, so there is no way to feed
    # it a Malay/Hokkien clinical glossary at this layer. The code-switching
    # instruction that IS available lives downstream, in the prompt that turns this
    # transcript into a summary (services/llm.py, CODE_SWITCHING_GUIDANCE), where a
    # model can be told what it is reading. Splitting the handling across the two
    # layers is a consequence of the engine, not an oversight.
    result = client.speech_to_text.convert(
        file=payload,
        model_id=MODEL_ID,
        diarize=True,
    )

    words = getattr(result, "words", None) or []
    segments = _segments_from_words(list(words))

    if not segments:
        # Diarization can come back empty on very short or single-speaker audio.
        plain = (getattr(result, "text", "") or "").strip()
        if not plain:
            raise TranscriptionUnavailable("Transcription returned no usable text")
        segments = [Segment(speaker="Speaker 1", text=plain)]

    return Transcript(
        segments=segments,
        language=getattr(result, "language_code", None),
        source="elevenlabs",
    )


def transcribe(audio_bytes: bytes, *, live: bool, filename: str = "audio.webm") -> Transcript:
    """
    Entry point. Mock unless BOTH the request and the deployment opt in.

    The two-key requirement is the whole credit guardrail; see the module
    docstring for why one is not enough.
    """
    if live and live_enabled():
        return live_transcript(audio_bytes, filename=filename)

    if live and not live_enabled():
        # Explicit, not silent: the caller asked for live and did not get it.
        logger.warning(
            "live=true requested but ELEVENLABS_LIVE_ENABLED is not set; using mock transcript"
        )
    return mock_transcript()
