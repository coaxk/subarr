// #448 — the pager maths both truncated pages now share.
import { describe, it, expect } from 'vitest';
import { pageWindow, clampOffset } from '../paging.js';

describe('pageWindow', () => {
  it('reports a human range, not just page numbers', () => {
    const w = pageWindow(538, 100, 200);
    expect(w.from).toBe(201);
    expect(w.to).toBe(300);
    expect(w.page).toBe(3);
    expect(w.pages).toBe(6);
  });

  it('knows the last page is reachable when total divides exactly', () => {
    // The classic off-by-one: 300/100 must be 3 pages, and page 3 must not
    // advertise a next page that does not exist.
    const w = pageWindow(300, 100, 200);
    expect(w.pages).toBe(3);
    expect(w.hasNext).toBe(false);
    expect(w.to).toBe(300);
  });

  it('caps the display range at the total on a short final page', () => {
    const w = pageWindow(538, 100, 500);
    expect(w.from).toBe(501);
    expect(w.to).toBe(538);
    expect(w.hasNext).toBe(false);
  });

  it('an empty result set is not page 1 of 1', () => {
    const w = pageWindow(0, 100, 0);
    expect(w).toMatchObject({ page: 0, pages: 0, from: 0, to: 0, hasPrev: false, hasNext: false });
  });

  it('first page has no previous', () => {
    expect(pageWindow(538, 100, 0).hasPrev).toBe(false);
  });
});

describe('clampOffset', () => {
  it('pulls an out-of-range offset back to the last page', () => {
    // Happens for real: acknowledge a batch on page 6, total shrinks, and the
    // offset now points past the end.
    expect(clampOffset(120, 100, 500)).toBe(100);
  });

  it('returns 0 when everything is gone', () => {
    expect(clampOffset(0, 100, 400)).toBe(0);
  });

  it('leaves a valid offset alone', () => {
    expect(clampOffset(538, 100, 200)).toBe(200);
  });

  it('never returns a negative', () => {
    expect(clampOffset(538, 100, -50)).toBe(0);
  });
});
