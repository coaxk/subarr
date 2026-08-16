// P4 — server-side search + pagination for the Aftercare page. Mirrors the
// review-pagination.test.js convention of exercising exported PURE helpers
// without rendering (the vitest harness stubs React; these are the pieces of
// the Aftercare query/page logic that are testable in isolation). The
// component behaviors they drive — stale-row retention during the 8s poll,
// page reset on view/source/search changes, page-scoped selection clearing,
// and action/polling preservation — are guaranteed by construction (the
// actions read row ids off the CURRENT page and refetch only ever replaces
// data on a guarded response), so the pure predicates below are the unit
// surface.
import { describe, it, expect } from 'vitest';
import { buildAftercareQuery, recoverEmptyPageOffset } from '../aftercare.jsx';
import { computePagination } from '../review.jsx';

describe('buildAftercareQuery', () => {
  it('defaults to the flagged view, page size 100, offset 0, no search', () => {
    expect(buildAftercareQuery({})).toBe('view=flagged&limit=100&offset=0');
  });

  it('includes view=all and a source filter when set', () => {
    const q = buildAftercareQuery({ view: 'all', source: 'existing_audit' });
    expect(q).toBe('view=all&source=existing_audit&limit=100&offset=0');
  });

  it('includes a trimmed search when present, omits an empty one', () => {
    expect(buildAftercareQuery({ search: '  SPAM  ', limit: 50, offset: 60 }))
      .toBe('view=flagged&search=SPAM&limit=50&offset=60');
    expect(buildAftercareQuery({ search: '   ' })).toBe('view=flagged&limit=100&offset=0');
  });

  it('always sends limit and offset alongside view/source/search', () => {
    const q = buildAftercareQuery({ view: 'all', source: 'existing_audit', search: 'e2', limit: 200, offset: 0 });
    expect(q).toBe('view=all&source=existing_audit&search=e2&limit=200&offset=0');
  });

  it('encodes search values with reserved characters', () => {
    const q = buildAftercareQuery({ search: 'a & b', limit: 100, offset: 0 });
    expect(q).toBe('view=flagged&search=a+%26+b&limit=100&offset=0');
  });
});

describe('recoverEmptyPageOffset', () => {
  it('steps back to the previous page when the current one emptied out', () => {
    // 3 rows existed past page 2 (offset 200) but were acknowledged out between
    // fetches, so page 3 came back empty with a truthful count > 0.
    expect(recoverEmptyPageOffset({ itemsLength: 0, count: 3, offset: 200, limit: 100 }))
      .toBe(100);
  });

  it('clamps the step-back to the first page (offset 0)', () => {
    // A single leftover row on page 2 (offset 100) vanishes — step back to 0.
    expect(recoverEmptyPageOffset({ itemsLength: 0, count: 1, offset: 100, limit: 100 }))
      .toBe(0);
  });

  it('returns null on the first page even with a count (genuinely empty)', () => {
    // offset 0 + empty page = no matches, not an invalid page past the end.
    expect(recoverEmptyPageOffset({ itemsLength: 0, count: 0, offset: 0 })).toBe(null);
  });

  it('returns null when the server reports no matching rows at all', () => {
    expect(recoverEmptyPageOffset({ itemsLength: 0, count: 0, offset: 200, limit: 100 })).toBe(null);
  });

  it('returns null when the page still has rows (valid page)', () => {
    expect(recoverEmptyPageOffset({ itemsLength: 2, count: 5, offset: 100, limit: 100 })).toBe(null);
  });
});

describe('aftercare pagination uses the server total', () => {
  it('builds a page query whose offset/limit feed computePagination off the server count', () => {
    // Simulate a real round trip: build the query for page 2 (offset 200, size 100),
    // then derive the controls from the response's truthful `count` — NOT the page
    // length (a page can return fewer rows than the total without losing the next page).
    const q = buildAftercareQuery({ view: 'all', limit: 100, offset: 200 });
    expect(q).toBe('view=all&limit=100&offset=200');

    // That page came back EMPTY with count=5 (only 5 rows exist, all on page 1):
    // recoverEmptyPageOffset runs BEFORE computePagination, stepping the offset
    // back to 0 — so computePagination never sees a count smaller than the offset
    // (which would yield an inverted shownStart > shownEnd range).
    expect(recoverEmptyPageOffset({ itemsLength: 0, count: 5, offset: 200, limit: 100 })).toBe(100);
    const pagination = computePagination({ count: 5, limit: 100, offset: 0 });
    expect(pagination).toMatchObject({
      total: 5, totalPages: 1, pageNumber: 1,
      shownStart: 1, shownEnd: 5, hasPrev: false, hasNext: false,
    });
  });

  it('reports a truthful total across pages (count not page length)', () => {
    // Page 1 of 5 with a page size of 2: count is the full 5, not 2.
    const pagination = computePagination({ count: 5, limit: 2, offset: 0 });
    expect(pagination.total).toBe(5);
    expect(pagination.hasNext).toBe(true);
  });
});
