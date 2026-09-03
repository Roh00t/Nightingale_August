import { describe, it, expect } from 'vitest';
import { extractClinicalValues } from './clinical_values';

const vals = (s: string) => extractClinicalValues(s).map((v) => v.text);

describe('extractClinicalValues', () => {
  it('pulls labs and vitals', () => {
    const got = vals('Lab review: eGFR 45 mL/min, Potassium 5.1 mEq/L. BP 128/78.');
    expect(got).toContain('eGFR 45 mL/min');
    expect(got).toContain('Potassium 5.1 mEq/L');
    expect(got).toContain('BP 128/78');
  });

  it('pulls a drug and dose', () => {
    expect(vals('Continue Lisinopril 10mg daily.')).toContain('Lisinopril 10mg');
  });

  it('never alters the source string it matched', () => {
    // The values are shown verbatim. Rounding "5.1" to "5" or normalising
    // "10mg" to "10 mg" would put a number on screen that is not in the record.
    const src = 'Potassium 5.1 mEq/L';
    expect(vals(src)[0]).toBe('Potassium 5.1 mEq/L');
  });

  it('deduplicates case- and space-insensitively', () => {
    const got = vals('eGFR 45 and again eGFR 45 and EGFR  45');
    expect(got.filter((g) => g.toLowerCase().startsWith('egfr')).length).toBe(1);
  });

  it('caps the strip so it stays scannable', () => {
    const many = Array.from({ length: 20 }, (_, i) => `eGFR ${i}`).join(', ');
    expect(extractClinicalValues(many).length).toBeLessThanOrEqual(6);
  });

  it('returns nothing for prose with no values', () => {
    expect(vals('Patient reports feeling much better since the last visit.')).toEqual([]);
  });

  it('is safe on empty input', () => {
    expect(extractClinicalValues('')).toEqual([]);
  });

  it('is repeatable — module-level /g regexes do not leak lastIndex', () => {
    const s = 'eGFR 45 and Potassium 5.1';
    expect(vals(s)).toEqual(vals(s));
    expect(vals(s)).toEqual(vals(s));
  });
});
