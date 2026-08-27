// #448: shared paging maths for Review and Aftercare.
//
// Both pages silently truncated: Review hard-capped at 200 server-side, and
// Aftercare never sent an offset because the endpoint reported the page length
// as `count` and so never revealed that more rows existed.
//
// Kept pure and shared so the two pagers cannot disagree about, for example,
// whether the last page is reachable when total is an exact multiple of limit.

export function pageWindow(total, limit, offset) {
  const t = Math.max(0, Number(total) || 0);
  const lim = Math.max(1, Number(limit) || 1);
  const off = Math.min(Math.max(0, Number(offset) || 0), Math.max(0, t - 1));
  const pages = t === 0 ? 0 : Math.ceil(t / lim);
  const page = t === 0 ? 0 : Math.floor(off / lim) + 1;
  return {
    page,
    pages,
    limit: lim,
    offset: t === 0 ? 0 : off,
    hasPrev: off > 0,
    hasNext: off + lim < t,
    // 1-based inclusive display range: "showing 201-300 of 538"
    from: t === 0 ? 0 : off + 1,
    to: Math.min(off + lim, t),
    total: t,
  };
}

// Clamp a requested offset so a pager can never land past the end -- e.g. after
// rows are acknowledged and the total shrinks under the user's feet.
export function clampOffset(total, limit, offset) {
  const t = Math.max(0, Number(total) || 0);
  const lim = Math.max(1, Number(limit) || 1);
  if (t === 0) return 0;
  const lastPageStart = Math.max(0, Math.floor((t - 1) / lim) * lim);
  return Math.min(Math.max(0, Number(offset) || 0), lastPageStart);
}
