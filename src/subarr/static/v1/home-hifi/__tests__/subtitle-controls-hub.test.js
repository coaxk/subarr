// #317 Slice C: the Rules-page "Other subtitle controls" hub is a signpost —
// its value is entirely in the link targets being correct. Guard them against
// typos / route rot. (The component itself is presentational; the data map is
// the part worth asserting.)
import { describe, it, expect } from 'vitest';
import { SUBTITLE_CONTROLS } from '../rules.jsx';

// Real subarr server routes (chrome.jsx nav + app.py route layer). A link to
// anything outside this set is a bug.
const KNOWN_ROUTES = new Set(['/home', '/coverage', '/library', '/review', '/rules', '/queue', '/settings', '/health']);

describe('SUBTITLE_CONTROLS hub map', () => {
  it('lists the four control families with label + blurb + at least one link', () => {
    const labels = SUBTITLE_CONTROLS.map(c => c.label);
    expect(labels).toEqual([
      'Forced subtitles',
      'Ignored titles',
      'Language rules',
      'Audio-language review',
    ]);
    for (const c of SUBTITLE_CONTROLS) {
      expect(typeof c.blurb).toBe('string');
      expect(c.blurb.length).toBeGreaterThan(0);
      expect(c.links.length).toBeGreaterThan(0);
    }
  });

  it('every link points at a known server route (hash/sub-anchor allowed)', () => {
    for (const c of SUBTITLE_CONTROLS) {
      for (const l of c.links) {
        expect(l.href.startsWith('/')).toBe(true);
        const path = l.href.split('#')[0]; // strip any deep-link hash
        expect(KNOWN_ROUTES.has(path)).toBe(true);
        expect(l.label.length).toBeGreaterThan(0);
      }
    }
  });

  it('language rules carries both the manage (settings) and declare (review) links', () => {
    const langRules = SUBTITLE_CONTROLS.find(c => c.label === 'Language rules');
    const hrefs = langRules.links.map(l => l.href);
    expect(hrefs).toContain('/settings#lang-rules');
    expect(hrefs).toContain('/review');
  });
});
