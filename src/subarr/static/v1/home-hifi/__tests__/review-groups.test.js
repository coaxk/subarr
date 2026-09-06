// #494 P3-S2/S4/S5 — Review renders the server's COMPLETE, distinct-by-key
// groups as-is (arrangeServerGroups) and turns a rendered group into every
// matching explicit file path (groupExplicitPaths, the helper SeriesGroup's
// "Select all" actually calls). Together these two pure seams make group
// rendering, duplicate-title/library separation, truthful per-group row counts,
// and complete-group selection testable without a DOM. P3-S5 additionally locks
// in that Review consumes the server's filtered complete-group payload verbatim —
// no client-side re-group by title, no re-filter by flag, no row cap.
import { describe, it, expect } from 'vitest';
import {
  arrangeServerGroups, groupExplicitPaths, buildVerifyBody, acceptMultilingualBody,
} from '../review.jsx';

// Build a pending-review row stamped with the server's stable group_key link.
const ep = (key, { file, canonical, title = 'The Show', media_type = 'show', library = 'TV',
                   episode_number, flag = 'suspect', lang_codes } = {}) => {
  const row = { group_key: key, title, media_type, library, flag };
  if (file !== undefined) row.file_canonical_path = file;
  if (canonical !== undefined) row.canonical_path = canonical;
  if (episode_number !== undefined) row.episode_number = String(episode_number);
  if (lang_codes) row.lang_codes = lang_codes;
  return row;
};
// Backend group metadata (the authority for which groups exist, in what order).
const groupMeta = (key, { title = 'The Show', media_type = 'show', library = 'TV',
                          canonical_root = '/tv/' } = {}) => ({ key, title, media_type, library, canonical_root });

describe('arrangeServerGroups — stable group-key rendering data (#494 P3-S2)', () => {
  it('keeps two same-title groups in different libraries as separate groups with distinct keys', () => {
    const groups = [
      groupMeta('g1', { title: 'The Show', library: 'TV1' }),
      groupMeta('g2', { title: 'The Show', library: 'TV2' }),
    ];
    const items = [
      ep('g1', { file: 'TV1/TheShow/S01E01.mkv', canonical: '/tv/TheShow/S01E01.mkv', episode_number: 1 }),
      ep('g1', { file: 'TV1/TheShow/S01E02.mkv', canonical: '/tv/TheShow/S01E02.mkv', episode_number: 2 }),
      ep('g2', { file: 'TV2/TheShow/S01E01.mkv', canonical: '/tv2/TheShow/S01E01.mkv', episode_number: 1 }),
    ];
    const { groups: out, tvGroups, movieGroups, pageFileCount } = arrangeServerGroups({ groups, items });

    // Distinct groups preserved — never merged by bare title.
    expect(out).toHaveLength(2);
    const [g1, g2] = out;
    expect(g1.key).not.toBe(g2.key);
    expect(g1.title).toBe('The Show');
    expect(g2.title).toBe('The Show');
    expect(g1.library).toBe('TV1');
    expect(g2.library).toBe('TV2');
    // No title-based merge: each group holds exactly its OWN rows.
    expect(g1.items.map((r) => r.file_canonical_path))
      .toEqual(['TV1/TheShow/S01E01.mkv', 'TV1/TheShow/S01E02.mkv']);
    expect(g2.items.map((r) => r.file_canonical_path)).toEqual(['TV2/TheShow/S01E01.mkv']);
    // Media partition is correct (both are shows).
    expect(tvGroups).toHaveLength(2);
    expect(movieGroups).toHaveLength(0);
    // pageFileCount counts every returned row exactly once.
    expect(pageFileCount).toBe(3);
  });

  it('per-group row count equals the rows the server returned — complete groups, no cap', () => {
    const groups = [groupMeta('gS', { title: 'Long Run' })];
    const items = Array.from({ length: 6 }, (_, i) =>
      ep('gS', { file: `TV/LongRun/S0E${String(i + 1).padStart(2, '0')}.mkv`, canonical: `/tv/LongRun/e${i + 1}.mkv`, episode_number: i + 1 }));
    const out = arrangeServerGroups({ groups, items });
    expect(out.groups).toHaveLength(1);
    const g = out.groups[0];
    expect(g.file_count).toBe(6);            // truthful per-group matching count
    expect(g.items).toHaveLength(6);         // every returned row rendered — no capping
    expect(out.pageFileCount).toBe(6);
  });
});

describe('groupExplicitPaths — complete-group selection (#494 P3-S2)', () => {
  it('maps a rendered group to every explicit file path (file_canonical_path preferred, canonical_path fallback)', () => {
    const groups = [groupMeta('gSel', { title: 'Pick Me' })];
    const items = [
      ep('gSel', { file: 'TV/PickMe/S01E01.mkv', canonical: '/tv/PickMe/S01E01.mkv', episode_number: 1 }),
      // A row with only the canonical fallback still yields an explicit path.
      ep('gSel', { canonical: '/tv/PickMe/S01E02.mkv', episode_number: 2 }),
      ep('gSel', { file: 'TV/PickMe/S01E03.mkv', canonical: '/tv/PickMe/S01E03.mkv', episode_number: 3 }),
    ];
    const [rendered] = arrangeServerGroups({ groups, items }).groups;
    // groupExplicitPaths is exactly what SeriesGroup's "Select all" feeds the
    // selection set, so it must contain every row of the complete group.
    const paths = groupExplicitPaths(rendered);
    expect(paths).toEqual([
      'TV/PickMe/S01E01.mkv',   // file_canonical_path wins when both are present
      '/tv/PickMe/S01E02.mkv',  // canonical_path fallback
      'TV/PickMe/S01E03.mkv',
    ]);
    expect(paths).toHaveLength(rendered.items.length);
  });

  it('tolerates a bare {items:[...]} shape and an empty/absent items list', () => {
    expect(groupExplicitPaths({ items: [{ file_canonical_path: '/a' }, { canonical_path: '/b' }] }))
      .toEqual(['/a', '/b']);
    expect(groupExplicitPaths({ items: [] })).toEqual([]);
    expect(groupExplicitPaths(undefined)).toEqual([]);
  });
});

describe('complete-group selection does not change payload contracts (#494 P3-S4)', () => {
  it('every explicit path of a rendered multilingual group flows into the same buildVerifyBody / acceptMultilingualBody shapes', () => {
    const groups = [groupMeta('gM', { title: 'Bilingual', media_type: 'show' })];
    const items = [
      ep('gM', { file: '/m/a.mkv', canonical: '/m/a.mkv', lang_codes: ['es', 'en'], flag: 'multilingual', episode_number: 2 }),
      ep('gM', { canonical: '/t/b.mkv', lang_codes: ['fr'], flag: 'multilingual', episode_number: 1 }),
    ];
    const [rendered] = arrangeServerGroups({ groups, items }).groups;
    const paths = groupExplicitPaths(rendered);
    expect(paths).toHaveLength(2);

    // Selecting the whole group submits those same explicit paths as before:
    // buildVerifyBody keys on the explicit path, never a group id. (arrangeServerGroups
    // orders episodes ascending, so /t/b.mkv — ep 1 — precedes /m/a.mkv — ep 2.)
    expect(paths).toEqual(['/t/b.mkv', '/m/a.mkv']);
    expect(paths.map((p) => buildVerifyBody(p, ['es', 'ja']))).toEqual([
      { canonical_path: '/t/b.mkv', lang_code: 'es', source: 'user', lang_class: 'multi', lang_codes: ['es', 'ja'] },
      { canonical_path: '/m/a.mkv', lang_code: 'es', source: 'user', lang_class: 'multi', lang_codes: ['es', 'ja'] },
    ]);
    // acceptMultilingualBody resolves each row to the same explicit path the
    // selection carries (file_canonical_path preferred).
    expect(acceptMultilingualBody(items[0])).toEqual({
      canonical_path: '/m/a.mkv', lang_code: 'es', source: 'user', lang_class: 'multi', lang_codes: ['es', 'en'],
    });
    expect(acceptMultilingualBody(items[1]).canonical_path).toBe('/t/b.mkv');
  });
});

describe('arrangeServerGroups consumes the server filtered payload as-is — no re-group / re-filter / cap (#494 P3-S5)', () => {
  it('renders exactly the rows the server selected for a group, including multilingual rows (no client-side flag re-filter)', () => {
    // The server already filtered this group's unfiltered membership down to
    // these two rows and returned them as a COMPLETE group. Review must render
    // exactly those two — it must NOT drop the multilingual one again or cap.
    const groups = [groupMeta('gF', { title: 'Filtered' })];
    const items = [
      ep('gF', { file: 'TV/Filtered/S01E01.mkv', canonical: '/tv/Filtered/S01E01.mkv', episode_number: 1, flag: 'suspect' }),
      ep('gF', { file: 'TV/Filtered/S01E02.mkv', canonical: '/tv/Filtered/S01E02.mkv', episode_number: 2, flag: 'multilingual', lang_codes: ['es', 'en'] }),
    ];
    const { groups: out, pageFileCount } = arrangeServerGroups({ groups, items });
    expect(out).toHaveLength(1);
    const g = out[0];
    expect(g.file_count).toBe(2);
    expect(g.items).toHaveLength(2);
    // Both rows present; the auto-multilingual one is only moved to the end for
    // presentation — it is never dropped.
    const flags = g.items.map((r) => r.flag);
    expect(flags).toContain('suspect');
    expect(flags).toContain('multilingual');
    expect(g.items[g.items.length - 1].flag).toBe('multilingual');
    expect(pageFileCount).toBe(2);
  });

  it('does not invent a group for an item whose group the server omitted, and drops no returned row of listed groups', () => {
    // The server listed only gX on this page. A stray item still references a
    // group gGhost the server did NOT return — arrangeServerGroups must not
    // re-group it into existence by title, and must not drop any gX row.
    const groups = [groupMeta('gX', { title: 'Only Listed' })];
    const items = [
      ep('gX', { file: 'TV/OnlyListed/S01E01.mkv', canonical: '/tv/OnlyListed/S01E01.mkv', episode_number: 1 }),
      ep('gX', { file: 'TV/OnlyListed/S01E02.mkv', canonical: '/tv/OnlyListed/S01E02.mkv', episode_number: 2 }),
      // Belongs to a group that is NOT in groups[] this page (server filtered it out).
      ep('gGhost', { file: 'TV/Ghost/S01E01.mkv', canonical: '/tv/Ghost/S01E01.mkv', episode_number: 1 }),
    ];
    const { groups: out, pageFileCount } = arrangeServerGroups({ groups, items });
    expect(out).toHaveLength(1);
    expect(out[0].key).toBe('gX');
    expect(out[0].items).toHaveLength(2);   // no phantom gGhost group, gX rows intact
    expect(pageFileCount).toBe(2);          // ghost rows are not counted toward the page
    expect(out.some((g) => g.key === 'gGhost')).toBe(false);
  });
});
