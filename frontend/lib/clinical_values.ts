/**
 * Pull scannable clinical values out of a summary paragraph.
 *
 * ---------------------------------------------------------------------------
 * WHAT THIS MUST NOT DO: change the paragraph.
 *
 * `SpanHighlightedText` (components/timeline/TimelineEntry.tsx) renders
 * provenance highlights by slicing `content_text` on CHARACTER OFFSETS supplied
 * by the AI service. Reformatting that string — bullets, bolding, anything that
 * splits it across nodes — silently invalidates every offset. Silently, because
 * a failed offset just renders unhighlighted, which looks exactly like "no
 * highlight was requested".
 *
 * So this extracts values for a strip rendered ABOVE the paragraph, and the
 * paragraph itself reaches SpanHighlightedText byte-for-byte unchanged.
 * ---------------------------------------------------------------------------
 *
 * The second reason for a strip rather than inline bolding: auto-emphasis
 * de-emphasises everything it misses. A rule that catches "eGFR 45" but not
 * "K+ 6.4" trains the eye to skip the unbolded one, which is worse than
 * bolding nothing. A separate strip adds a scan path without removing one.
 *
 * Deliberately conservative. These patterns match shapes — a measurement, a
 * dose — not clinical meaning. Nothing here decides whether a value is
 * concerning; that is the safety layer's job and it runs server-side.
 */
export interface ClinicalValue {
  /** Exactly as it appears in the source text. Never reformatted or rounded. */
  text: string;
}

const PATTERNS: RegExp[] = [
  // Named lab or vital followed by a number: "eGFR 45", "Potassium 5.1",
  // "BP 128/78", "K+ 6.4", "SpO2 98%"
  /\b(?:eGFR|GFR|BP|HR|SpO2|K\+|Na\+|creatinine|potassium|sodium|glucose|temp|weight|BMI)\s*:?\s*\d+(?:\.\d+)?(?:\/\d+(?:\.\d+)?)?\s*(?:%|mmHg|mg\/dL|mEq\/L|mL\/min|bpm|kg|°C)?/gi,
  // A dose: "Lisinopril 10mg daily", "10 mg", "500mcg"
  /\b[A-Z][a-z]{3,}\s+\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units?)\b/g,
];

export function extractClinicalValues(text: string, limit = 6): ClinicalValue[] {
  if (!text) return [];

  const seen = new Set<string>();
  const out: ClinicalValue[] = [];

  for (const re of PATTERNS) {
    // Fresh lastIndex per call — these are module-level /g regexes.
    re.lastIndex = 0;
    for (const m of text.matchAll(re)) {
      const raw = m[0].trim();
      const key = raw.toLowerCase().replace(/\s+/g, ' ');
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ text: raw });
      if (out.length >= limit) return out;
    }
  }
  return out;
}
