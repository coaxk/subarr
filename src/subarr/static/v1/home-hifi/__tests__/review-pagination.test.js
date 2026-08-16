// P3 — server-side search + pagination query builder and page-state derivation
// for the Review queue. Both are pure so they're testable here, mirroring the
// review-multiselect.test.js convention of exercising review.jsx's exported
// helpers without rendering.
import { describe, it, expect } from 'vitest';
import { buildReviewQuery, computePagination } from '../review.jsx';

describe('buildReviewQuery', () => {
  it('always sends limit and offset, omits an empty search', () => {
    expect(buildReviewQuery({})).toBe('limit=200&offset=0');
  });

  it('includes a trimmed search when present', () => {
    const q = buildReviewQuery({ search: '  TheSpecialOne  ', limit: 50, offset: 60 });
    expect(q).toBe('search=TheSpecialOne&limit=50&offset=60');
  });

  it('encodes search values with reserved characters', () => {
    const q = buildReviewQuery({ search: 'show & file', limit: 200, offset: 0 });
    expect(q).toBe('search=show+%26+file&limit=200&offset=0');
  });
});

describe('computePagination', () => {
  it('first page with fewer rows than the page size', () => {
    expect(computePagination({ count: 3, limit: 200, offset: 0 })).toMatchObject({
      total: 3, totalPages: 1, pageNumber: 1,
      shownStart: 1, shownEnd: 3, hasPrev: false, hasNext: false,
    });
  });

  it('disables next on the last page and prev on the first', () => {
    // 250 total, page size 100 -> 3 pages.
    const first = computePagination({ count: 250, limit: 100, offset: 0 });
    expect(first.hasPrev).toBe(false);
    expect(first.hasNext).toBe(true);
    expect(first.shownStart).toBe(1);
    expect(first.shownEnd).toBe(100);

    const middle = computePagination({ count: 250, limit: 100, offset: 100 });
    expect(middle.hasPrev).toBe(true);
    expect(middle.hasNext).toBe(true);
    expect(middle.pageNumber).toBe(2);

    const last = computePagination({ count: 250, limit: 100, offset: 200 });
    expect(last.hasPrev).toBe(true);
    expect(last.hasNext).toBe(false);   // offset+limit (300) >= count (250)
    expect(last.pageNumber).toBe(3);
    expect(last.shownEnd).toBe(250);     // clamped to the truthful total
  });

  it('clamps a short last page to the actual total', () => {
    expect(computePagination({ count: 201, limit: 200, offset: 200 }))
      .toMatchObject({ totalPages: 2, pageNumber: 2, shownStart: 201, shownEnd: 201, hasNext: false });
  });

  it('handles a zero-total result with no bogus range', () => {
    expect(computePagination({ count: 0, limit: 200, offset: 0 })).toMatchObject({
      total: 0, totalPages: 1, pageNumber: 1, shownStart: 0, shownEnd: 0,
      hasPrev: false, hasNext: false,
    });
  });

  it('guards against undefined count and non-positive limit', () => {
    expect(computePagination({})).toMatchObject({ total: 0, totalPages: 1, hasNext: false });
    // A non-positive limit is clamped to 1 so we never divide by zero; the
    // server validates limit >= 1 anyway, so this is purely a NaN guard.
    expect(computePagination({ count: 10, limit: 0, offset: 0 })).toMatchObject({
      total: 10, totalPages: 10, shownStart: 1, shownEnd: 1, hasNext: true,
    });
  });
});
