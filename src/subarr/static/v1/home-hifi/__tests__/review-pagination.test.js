// P3 — server-side search + pagination query builder and page-state derivation
// for the Review queue. Both are pure so they're testable here, mirroring the
// review-multiselect.test.js convention of exercising review.jsx's exported
// helpers without rendering.
import { describe, it, expect } from 'vitest';
import { buildReviewQuery, computePagination } from '../review.jsx';

describe('buildReviewQuery', () => {
  it('Review opts into grouped mode by default (grouped=true) with an empty search omitted', () => {
    expect(buildReviewQuery({})).toBe('limit=200&offset=0&grouped=true');
  });

  it('includes a trimmed search when present', () => {
    const q = buildReviewQuery({ search: '  TheSpecialOne  ', limit: 50, offset: 60 });
    expect(q).toBe('search=TheSpecialOne&limit=50&offset=60&grouped=true');
  });

  it('encodes search values with reserved characters', () => {
    const q = buildReviewQuery({ search: 'show & file', limit: 200, offset: 0 });
    expect(q).toBe('search=show+%26+file&limit=200&offset=0&grouped=true');
  });

  it('grouped=false keeps the legacy default-mode URL (no grouped param)', () => {
    // #494: non-group /pending-review consumers keep the old contract; the
    // builder can opt out for callers/tests that still page files.
    expect(buildReviewQuery({ grouped: false })).toBe('limit=200&offset=0');
    expect(buildReviewQuery({ search: 'x', limit: 50, offset: 60, grouped: false }))
      .toBe('search=x&limit=50&offset=60');
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

describe('computePagination — grouped Review mode (#494 P1-S2)', () => {
  it('group_count (groups), not file count, drives the paging unit in grouped mode', () => {
    // `count` is the matching FILE total; `groupCount` is what paging addresses.
    const p = computePagination({ count: 500, groupCount: 10, limit: 4, offset: 0, grouped: true });
    expect(p.total).toBe(10);          // groups, not the 500 files
    expect(p.groupCount).toBe(10);
    expect(p.fileCount).toBe(500);
    expect(p.totalPages).toBe(3);      // ceil(10/4)
    expect(p.pageNumber).toBe(1);
    expect(p.shownStart).toBe(1);
    expect(p.shownEnd).toBe(4);
    expect(p.hasPrev).toBe(false);
    expect(p.hasNext).toBe(true);
  });

  it('page ranges and next/previous boundaries come from the group total', () => {
    const first = computePagination({ count: 30, groupCount: 9, limit: 4, offset: 0, grouped: true });
    expect(first).toMatchObject({ pageNumber: 1, shownStart: 1, shownEnd: 4, hasPrev: false, hasNext: true });

    const middle = computePagination({ count: 30, groupCount: 9, limit: 4, offset: 4, grouped: true });
    expect(middle).toMatchObject({ pageNumber: 2, shownStart: 5, shownEnd: 8, hasPrev: true, hasNext: true });

    const last = computePagination({ count: 30, groupCount: 9, limit: 4, offset: 8, grouped: true });
    expect(last).toMatchObject({ pageNumber: 3, shownStart: 9, shownEnd: 9, hasPrev: true, hasNext: false });
  });

  it('next is governed by groups even when the file total would suggest more pages', () => {
    // 12 groups fill one page; 48 matching files would keep paging under legacy
    // file math, but grouped mode pages GROUPS, so there is no next page.
    const p = computePagination({ count: 48, groupCount: 12, limit: 12, offset: 0, grouped: true });
    expect(p.hasNext).toBe(false);
    expect(p.totalPages).toBe(1);
    expect(p.shownEnd).toBe(12);
    // ...and the inverse: few files across many groups still pages by groups.
    const q = computePagination({ count: 3, groupCount: 25, limit: 5, offset: 0, grouped: true });
    expect(q.total).toBe(25);
    expect(q.totalPages).toBe(5);
    expect(q.hasNext).toBe(true);
  });

  it('a zero group-count grouped page shows no bogus range but still carries the file total', () => {
    const p = computePagination({ count: 7, groupCount: 0, limit: 4, offset: 0, grouped: true });
    expect(p).toMatchObject({ total: 0, groupCount: 0, fileCount: 7, shownStart: 0, shownEnd: 0, hasNext: false });
  });
});

describe('computePagination — legacy file-total default stays guarded (#494 P1-S2)', () => {
  it('omitting grouped (the legacy default) pages FILES even when groupCount is supplied', () => {
    // aftercare.jsx and other non-group callers pass no grouped flag; a stray
    // groupCount must NOT switch them onto group math.
    const p = computePagination({ count: 10, groupCount: 25, limit: 4, offset: 0 });
    expect(p.total).toBe(10);      // the file total, not the 25 "groups"
    expect(p.groupCount).toBe(0);  // grouped-only fields stay inert in legacy mode
    expect(p.fileCount).toBe(10);
    expect(p.totalPages).toBe(3);  // ceil(10/4)
    expect(p.hasNext).toBe(true);
  });

  it('explicit grouped=false keeps the exact legacy field set and file math', () => {
    const p = computePagination({ count: 250, limit: 100, offset: 200, grouped: false });
    expect(p).toMatchObject({
      total: 250, groupCount: 0, fileCount: 250, pageNumber: 3,
      shownStart: 201, shownEnd: 250, hasPrev: true, hasNext: false,
    });
  });
});

describe('buildReviewQuery — explicit grouped mode keeps the byte-identical search contract (#494 P3-S1)', () => {
  it('appends grouped=true after the unchanged trimmed search + flag + limit + offset fields', () => {
    // The grouped opt-in must not reorder or re-encode the existing search/flag
    // fields — it is purely additive at the end of the legacy query.
    const q = buildReviewQuery({ search: 'The Special One', flag: 'track_mismatch', limit: 50, offset: 120 });
    expect(q).toBe('search=The+Special+One&flag=track_mismatch&limit=50&offset=120&grouped=true');
  });

  it('legacy grouped:false omits the mode entirely (exact old URL bytes)', () => {
    const q = buildReviewQuery({ search: 'a/b&c', flag: 'suspect', limit: 25, offset: 75, grouped: false });
    expect(q).toBe('search=a%2Fb%26c&flag=suspect&limit=25&offset=75');
  });
});
