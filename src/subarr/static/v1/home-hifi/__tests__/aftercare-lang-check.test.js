// #451: bounded advisory text-LID sanity check rendering helper.
//
// langCheckView is a pure function (no DOM), so these exercise the real
// status/reason/language/provenance display logic — the same pattern as
// subtitle-tuning.test.js. It is ADVISORY: the presence or absence of a check
// result must never imply gating (the backend contract guarantees that), and
// these tests only assert the mapping of the persisted bounded result.
import { describe, it, expect } from 'vitest';
import { langCheckView } from '../aftercare.jsx';

describe('langCheckView (#451 advisory text-LID)', () => {
  it('returns null when no bounded result is present', () => {
    expect(langCheckView(undefined)).toBeNull();
    expect(langCheckView({})).toBeNull();
    expect(langCheckView({ text_lang_check: null })).toBeNull();
  });

  it('maps status/reason/languages into a single advisory label', () => {
    const view = langCheckView({
      text_lang_check: {
        status: 'WARN',
        reason: 'source_target_mismatch',
        languages: ['en', 'de'],
        provenance: {},
      },
    });
    expect(view.label).toBe('WARN · source_target_mismatch · en · de');
  });

  it('keeps reason/languages optional', () => {
    const view = langCheckView({ text_lang_check: { status: 'PASS', languages: ['en'] } });
    expect(view.label).toBe('PASS · en');
  });

  it('surfaces provenance origin and conflict flag', () => {
    const view = langCheckView({
      text_lang_check: {
        status: 'WARN',
        reason: 'ordinary_mismatch',
        languages: ['en'],
        provenance: { origin: 'webhook', conflict: true },
      },
    });
    expect(view.provenance.origin).toBe('webhook');
    expect(view.provenance.conflict).toBe(true);
  });

  it('treats absent/unknown provenance conflict as null (not a claim)', () => {
    const empty = langCheckView({ text_lang_check: { status: 'INCONCLUSIVE' } });
    expect(empty.provenance.conflict).toBeNull();
    const noConflict = langCheckView({
      text_lang_check: { status: 'PASS', provenance: { conflict: 0 } },
    });
    expect(noConflict.provenance.conflict).toBe(false);
  });
});
