// #357 — the audio badge classifier picks multilingual / zxx states.
import { describe, it, expect } from 'vitest';
import { audioBadgeKind } from '../coverage.jsx';

describe('audioBadgeKind', () => {
  it('picks multilingual for a confident multi-language file', () => {
    expect(audioBadgeKind({ audio_source: 'multilingual', audio_lang_codes: ['gl', 'es', 'fr'] }))
      .toBe('multilingual');
  });

  it('picks multilingual from lang_codes even without the source flag', () => {
    expect(audioBadgeKind({ audio_lang_codes: ['ja', 'en'] })).toBe('multilingual');
  });

  it('picks zxx for a no-linguistic-content file', () => {
    expect(audioBadgeKind({ audio_langs: ['zxx'] })).toBe('zxx');
  });

  it('multilingual wins over the suspect flag (stops crying wolf)', () => {
    expect(audioBadgeKind({
      audio_source: 'multilingual', audio_lang_codes: ['gl', 'es'], audio_label_suspect: true,
    })).toBe('multilingual');
  });

  it('leaves a normal suspect file as suspect', () => {
    expect(audioBadgeKind({ audio_label_suspect: true })).toBe('suspect');
  });

  it('leaves a verified user file as user', () => {
    expect(audioBadgeKind({ audio_source: 'user' })).toBe('user');
  });
});
