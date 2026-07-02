# #406 Multilingual Correction UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make auto-classified multilingual audio verdicts visible and correctable in the Review page, and let a user create a multilingual verdict from the bulk toolbar.

**Architecture:** One additive read-endpoint branch in `pending_review` surfaces store rows whose verification `source == 'auto-high-conf-multi'` as `flag='multilingual'` (keyed on the STORE source, never the snapshot display state, which reads `'multilingual'` for both auto and user rows). The frontend gains a multi-select toggle in the existing bulk toolbar, a distinct sorted-last lane row, and a per-row "Accept (keep as detected)" action. All logic lives in pure exported helpers so it is vitest-covered.

**Tech Stack:** Python 3.11 / FastAPI (backend), vanilla React via Babel-in-browser JSX bundled by esbuild (frontend), pytest + TestClient (backend tests), vitest (frontend helper tests).

---

## Background — verified facts (read before starting)

Signatures and shapes quoted from the real code so no task invents an interface:

- **Store** `src/subarr/audio_lang_store.py`:
  - `get_all_sources_as_lookup(self) -> dict[str, str]` (line 206) returns `{canonical_path: source}` for every per-file verification (no series-intent expansion).
  - `get_all_multi_as_lookup(self) -> dict[str, list[str]]` (line 191) returns `{canonical_path: lang_codes}` for every `lang_class='multi'` row.
  - `upsert(*, canonical_path, lang_code, source='user', confidence=1.0, verified_by=None, evidence=None, lang_class='single', lang_codes=None)` (line 101). The store normalizes lang codes.

- **Router** `src/subarr/routers/audio_lang.py`, `pending_review` (line 656). The current branch chain per snapshot item `it` (lines 699-718):

  ```python
  if it.get("default_track_mismatch"):
      ...
      flag = "track_mismatch"
      extra = {...}
  elif (file_path and file_path in verifications) or (canonical and canonical in verifications):
      continue  # already verified, and not a track mismatch
  elif it.get("audio_label_suspect"):
      flag = "suspect"
  elif it.get("audio_label_unknown"):
      flag = "unknown"
  if not flag:
      continue
  pending.append({ ... "flag": flag, ... **extra })
  ```

  `verifications = audio_lang_store.get_all_as_lookup()` (line 664). `file_path = it.get("file_canonical_path")`, `canonical = it.get("canonical_path")` (lines 692-693).

- **Snapshot item shape (why source-keying is required):** `coverage_engine._surface_multilingual` (line 1204-1211) sets `it.audio_source = "multilingual"` and `it.audio_label_suspect = False` for BOTH auto and user-confirmed multi rows — so the snapshot's display `audio_source` cannot distinguish them. The STORE `source` (`auto-high-conf-multi` vs `user`) is the only reliable discriminator. An auto-multi row therefore has a populated `lang_code` in the store → today hits the "already verified → skip" and is dropped.

- **`app.state.audio_lang`** is a real `AudioLangStore` (`app.py:507`), so backend tests seed it directly with `.upsert(...)`.

- **Test harness** (`tests/conftest.py`): `app_with_stub` yields a **sync** `TestClient`. The existing pending-review test (`tests/test_track_mismatch_clearing.py`) uses plain sync functions — `app.state.coverage_cache = _SnapCache([...])`, then `app_with_stub.get(PENDING).json()`. It seeds items via a `_SnapCache` stub, NOT the fallback build. **These backend tests are sync — no `@pytest.mark.asyncio`.** (pytest-asyncio strict only applies to genuinely `async def` tests; mirror the existing sync pattern exactly.)

- **Frontend** `src/subarr/static/v1/home-hifi/review.jsx`:
  - `buildVerifyBody(canonicalPath, langs)` (line 253) exported — `[]`/all-falsy → `null`; 1 code → `{canonical_path, lang_code, source:'user', lang_class:'single'}`; 2+ → `{..., lang_class:'multi', lang_codes}`.
  - `isAutoMultilingualRow(r)` (line 268) exported.
  - Toolbar single `<select value={bulkLang}>` (line 947) + "Remember for future downloads" `<label><input type=checkbox>` (line 961-971).
  - `applyBulk` (line 621) builds `buildVerifyBody(p, [bulkLang])` (line 649), runs 4 workers, dispatches `audio-lang-verified`.
  - State: `bulkLang` (line 301), `rememberFuture` (line 306), `epSelection` (line 297), `selAssignPaths`/`selTmItems` (line 605), `data` (line 274), `langPicks = useLanguagePicks()` (line 273, an array of `[code, name]` pairs — see `langPicks.map(([code, name]) => ...)` line 957).
  - Rows are grouped by title and rendered via `SeriesGroup` → `EpisodeRow` (line 122). Existing per-series sort is `episode_number` (line 585); series sort alphabetical (line 591). **There is no flag-priority sort today** — rows render in group/episode order. `FlagDot` (line 18) renders the suspect/unknown dot; `TrackMismatchRow` (line 51) renders the mismatch chip.
  - Existing helper tests: `src/subarr/static/v1/home-hifi/__tests__/review-multiselect.test.js` imports from `../review.jsx`.

- **Build/scripts** (`package.json`): `npm run build:frontend`, `npm run check:frontend` (rebuild + `git diff --exit-code` on `*.bundle.js`/`*.html`), `npm run test:frontend` (`vitest run`). `review.bundle.js` is the served artifact and MUST be committed after any `.jsx` edit.

## File Structure

| File | Create/Modify | Responsibility |
|------|---------------|----------------|
| `src/subarr/routers/audio_lang.py` | Modify (`pending_review`, ~656) | Add the source-keyed `multilingual` branch before the already-verified skip; emit `flag='multilingual'` + `lang_codes` in the payload. |
| `tests/test_pending_review_multilingual.py` | Create | Backend: auto-multi surfaces as `multilingual` with `lang_codes`; user-multi does NOT; suspect/track-mismatch unaffected. |
| `src/subarr/static/v1/home-hifi/review.jsx` | Modify | `multilingualMode`/`bulkLangs` state + toolbar toggle + checkable lang list; route `applyBulk` through `buildVerifyBody`; export `sortPendingRows` + `acceptMultilingualBody`; render 🌐 chip; `acceptSelected` handler + Accept button. |
| `src/subarr/static/v1/home-hifi/review.bundle.js` | Modify (generated) | Rebuilt artifact — committed alongside the `.jsx`. |
| `src/subarr/static/v1/home-hifi/__tests__/review-multiselect.test.js` | Modify | Add vitest for `sortPendingRows` and `acceptMultilingualBody`. |

---

## Task 1: Backend — surface auto-multilingual rows in `pending-review`

**Files:**
- Modify: `src/subarr/routers/audio_lang.py` (`pending_review`, lines 656-738)
- Test: `tests/test_pending_review_multilingual.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_pending_review_multilingual.py`:

```python
"""#406: pending-review surfaces AUTO-classified multilingual rows (store
source == 'auto-high-conf-multi') as flag='multilingual' with the lang_codes
set, so they are visible + correctable. User-confirmed multi (source=='user')
is settled and must NOT re-enter the lane.

Sync TestClient tests mirroring tests/test_track_mismatch_clearing.py — the
pending-review endpoint reads app.state.coverage_cache's cached snapshot, so we
seed a stub snapshot and seed the real AudioLangStore with .upsert().
"""

from __future__ import annotations


class _SnapStub:
    def __init__(self, items):
        self.items = items


class _SnapCache:
    def __init__(self, items):
        self._snap = _SnapStub(items)

    def request_refresh(self, *a, **k):
        pass

    def get_cached(self):
        return self._snap


PENDING = "/api/audio-lang/pending-review"

_AUTO_PATH = "Movies/TheBeasts.mkv"
_USER_PATH = "Movies/Roma.mkv"
_SUSPECT_PATH = "TV/Show/Season 1/ep.mkv"


def _multi_item(path):
    # Mirrors a real snapshot row for a multilingual verdict: audio_source is
    # 'multilingual' and suspect is suppressed for BOTH auto and user rows —
    # which is exactly why the endpoint must key on the STORE source, not this.
    return {
        "file_canonical_path": path,
        "canonical_path": path,
        "title": path.split("/")[-1],
        "audio_source": "multilingual",
        "audio_label_suspect": False,
        "audio_label_unknown": False,
        "audio_langs": ["gl", "es"],
    }


def _suspect_item(path):
    return {
        "file_canonical_path": path,
        "canonical_path": path,
        "title": "Show",
        "audio_label_suspect": True,
    }


def test_auto_multilingual_surfaces_with_lang_codes(app_with_stub):
    app = app_with_stub.app
    store = app.state.audio_lang
    # AUTO multilingual verdict — store source is auto-high-conf-multi.
    store.upsert(
        canonical_path=_AUTO_PATH,
        lang_code="gl",
        source="auto-high-conf-multi",
        lang_class="multi",
        lang_codes=["gl", "es"],
    )
    app.state.coverage_cache = _SnapCache([_multi_item(_AUTO_PATH)])

    items = app_with_stub.get(PENDING).json()["items"]
    row = next((it for it in items if it.get("canonical_path") == _AUTO_PATH), None)
    assert row is not None, "auto-multi row must appear in the lane"
    assert row["flag"] == "multilingual"
    assert row["lang_codes"] == ["gl", "es"]


def test_user_confirmed_multilingual_is_not_surfaced(app_with_stub):
    app = app_with_stub.app
    store = app.state.audio_lang
    # USER-confirmed multilingual — settled, must never re-enter the lane.
    store.upsert(
        canonical_path=_USER_PATH,
        lang_code="es",
        source="user",
        lang_class="multi",
        lang_codes=["es", "en"],
    )
    app.state.coverage_cache = _SnapCache([_multi_item(_USER_PATH)])

    items = app_with_stub.get(PENDING).json()["items"]
    assert not any(it.get("canonical_path") == _USER_PATH for it in items)


def test_suspect_row_unaffected(app_with_stub):
    app = app_with_stub.app
    app.state.coverage_cache = _SnapCache([_suspect_item(_SUSPECT_PATH)])
    items = app_with_stub.get(PENDING).json()["items"]
    row = next((it for it in items if it.get("canonical_path") == _SUSPECT_PATH), None)
    assert row is not None
    assert row["flag"] == "suspect"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pending_review_multilingual.py -q`
Expected: `test_auto_multilingual_surfaces_with_lang_codes` FAILS — the auto row hits the existing `elif ... in verifications: continue` (it is stored) and is dropped, so `row is None` → AssertionError "auto-multi row must appear in the lane". (`test_user_confirmed_multilingual_is_not_surfaced` and `test_suspect_row_unaffected` may already pass — that is fine; the RED signal is the first test.)

- [ ] **Step 3: Write minimal implementation**

In `src/subarr/routers/audio_lang.py`, inside `pending_review`, after `verifications = audio_lang_store.get_all_as_lookup()` (line 664) add the two source/multi lookups:

```python
    verifications = audio_lang_store.get_all_as_lookup()
    # #406: key the multilingual lane on the STORE source (not the snapshot's
    # display audio_source, which reads 'multilingual' for BOTH auto and
    # user-confirmed rows). Auto rows are surfaced for review; user rows are
    # settled and excluded.
    sources = audio_lang_store.get_all_sources_as_lookup()
    multi_map = audio_lang_store.get_all_multi_as_lookup()
```

Then restructure the branch chain (lines 699-718) so `multilingual` sits AFTER track-mismatch (which keeps FIRST precedence) and BEFORE the already-verified skip:

```python
        if it.get("default_track_mismatch"):
            if file_path and file_path in tm_dismissed:
                continue  # user dismissed this track-mismatch — honor it live
            flag = "track_mismatch"
            extra = {
                "mismatch_default_track_lang": it.get("mismatch_default_track_lang"),
                "mismatch_native_track_lang": it.get("mismatch_native_track_lang"),
                "mismatch_native_audio_ordinal": it.get("mismatch_native_audio_ordinal"),
            }
        # #406: an auto-classified multilingual verdict is stored (so it would
        # otherwise hit the already-verified skip below) but is NOT settled —
        # surface it for review. Keyed on the store source; user-confirmed
        # multi (source=='user') is deliberately excluded.
        elif (file_path and sources.get(file_path) == "auto-high-conf-multi") or (
            canonical and sources.get(canonical) == "auto-high-conf-multi"
        ):
            flag = "multilingual"
            extra = {"lang_codes": multi_map.get(file_path) or multi_map.get(canonical)}
        # Skip if already verified — check BOTH keys (bulk-verify stores under
        # file_canonical_path || canonical_path; Bazarr-synthetic / series-level
        # rows have a None file_canonical_path and are verified under canonical).
        elif (file_path and file_path in verifications) or (canonical and canonical in verifications):
            continue  # already verified, and not a track mismatch
        elif it.get("audio_label_suspect"):
            flag = "suspect"
        elif it.get("audio_label_unknown"):
            flag = "unknown"
```

The `**extra` spread on the existing `pending.append(...)` block (line 735) carries `lang_codes` into the payload unchanged — no edit needed to `pending.append`. (For a `multilingual` row `extra = {"lang_codes": [...]}`; for others `extra` keeps its current value.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pending_review_multilingual.py -q`
Expected: PASS (3 passed).

Then confirm no regression on the sibling endpoint tests:
Run: `python -m pytest tests/test_track_mismatch_clearing.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/audio_lang.py tests/test_pending_review_multilingual.py
git commit -m "feat(406): surface auto-multilingual rows in pending-review

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Frontend — Multilingual toggle in the bulk toolbar

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/review.jsx` (state ~301-306; `applyBulk` ~621-695; toolbar ~940-984)
- Modify (generated): `src/subarr/static/v1/home-hifi/review.bundle.js`

No new pure helper is introduced in this task (`buildVerifyBody` single-vs-multi is already vitest-covered in `review-multiselect.test.js`), so there is no RED test step here — the change is wiring existing tested helpers into new UI state. Verification is the existing vitest suite staying green + a clean bundle rebuild.

- [ ] **Step 1: Add state**

In `review.jsx`, after `const [rememberFuture, setRememberFuture] = useState(true);` (line 306) add:

```jsx
  // #406: multilingual bulk mode. When on, the single <select> becomes a
  // checkable language list and applyBulk submits the full set as a
  // lang_class='multi' verdict. Series-level multilingual intent is out of
  // scope (#357 non-goal), so "Remember for future" is disabled while on.
  const [multilingualMode, setMultilingualMode] = useState(false);
  const [bulkLangs, setBulkLangs] = useState([]);

  const toggleBulkLang = useCallback((code) => {
    setBulkLangs((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
    );
  }, []);
```

- [ ] **Step 2: Route `applyBulk` through the tested builder**

In `applyBulk` change the body build (line 648-652) so it uses the selected set when multilingual mode is on, and SKIP files whose `buildVerifyBody` returns `null` (empty selection). Replace:

```jsx
          const r = await fetch('/api/audio-lang/verifications', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            // #357: buildVerifyBody carries lang_class (+ lang_codes for a
            // multilingual pick). bulkLang is single today; the builder is
            // ready for a multi-select control without changing this path.
            body: JSON.stringify({
              ...buildVerifyBody(p, [bulkLang]),
              confidence: 1.0,
              evidence: { bulk: true },
            }),
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
```

with:

```jsx
          // #406: multilingual mode submits the full checked set; otherwise the
          // single bulkLang. Empty selection -> builder returns null -> skip.
          const verifyBody = buildVerifyBody(p, multilingualMode ? bulkLangs : [bulkLang]);
          if (!verifyBody) { done += 1; setBulkProgress({ done, total: paths.length, errors }); continue; }
          const r = await fetch('/api/audio-lang/verifications', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ ...verifyBody, confidence: 1.0, evidence: { bulk: true } }),
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
```

Add `multilingualMode` and `bulkLangs` to the `applyBulk` dependency array (line 695):

```jsx
  }, [selAssignPaths, bulkLang, multilingualMode, bulkLangs, fetchPending, clearSelection, rememberFuture, data]);
```

- [ ] **Step 3: Add the toolbar toggle + checkable list**

In the language-assignable toolbar block, replace the single `<select>` (lines 947-960) so it renders the checkable list when `multilingualMode`, else the existing `<select>`. Insert the "Multilingual" checkbox before the assign control and disable "Remember for future" when on. Replace lines 944-971:

```jsx
              <label style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
                Assign audio language
              </label>
              {multilingualMode ? (
                <div role="group" aria-label="Multilingual: select languages"
                     style={{
                       display: 'flex', flexWrap: 'wrap', gap: 6, maxWidth: 420,
                       maxHeight: 84, overflowY: 'auto', padding: '4px 6px',
                       background: 'var(--bg-1)', border: 'var(--border)',
                       borderRadius: 'var(--radius-md)',
                     }}>
                  {langPicks.map(([code, name]) => (
                    <label key={code}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                        fontSize: 'var(--text-2xs)', color: 'var(--fg-1)', cursor: 'pointer',
                      }}>
                      <input type="checkbox"
                        checked={bulkLangs.includes(code)}
                        onChange={() => toggleBulkLang(code)}
                        disabled={bulkRunning}
                        style={{ accentColor: 'var(--violet-500)' }} />
                      {name} ({code})
                    </label>
                  ))}
                </div>
              ) : (
                <select value={bulkLang}
                        onChange={(e) => setBulkLang(e.target.value)}
                        disabled={bulkRunning}
                        aria-label="Audio language to assign"
                        style={{
                          height: 28, padding: '0 8px',
                          background: 'var(--bg-1)', color: 'var(--fg-0)',
                          border: 'var(--border)', borderRadius: 'var(--radius-md)',
                          fontSize: 'var(--text-sm)',
                        }}>
                  {langPicks.map(([code, name]) => (
                    <option key={code} value={code}>{name} ({code})</option>
                  ))}
                </select>
              )}
              <label style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                fontSize: 'var(--text-xs)', color: 'var(--fg-1)', cursor: 'pointer',
              }}
                title="Mark these files as multilingual (multiple audio languages in one file).">
                <input type="checkbox" checked={multilingualMode}
                  onChange={(e) => setMultilingualMode(e.target.checked)}
                  disabled={bulkRunning}
                  style={{ accentColor: 'var(--violet-500)' }} />
                Multilingual
              </label>
              <label style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                fontSize: 'var(--text-xs)', color: 'var(--fg-1)',
                cursor: multilingualMode ? 'not-allowed' : 'pointer',
                opacity: multilingualMode ? 0.5 : 1,
              }}
                title="Also save a rule so new episodes — and re-downloaded movies — of these titles inherit this language automatically. A per-file correction always overrides it.">
                <input type="checkbox" checked={rememberFuture && !multilingualMode}
                  onChange={(e) => setRememberFuture(e.target.checked)}
                  disabled={bulkRunning || multilingualMode}
                  style={{ accentColor: 'var(--violet-500)' }} />
                Remember for future downloads
              </label>
```

- [ ] **Step 4: Rebuild the bundle and run vitest**

Run: `npm run build:frontend`
Expected: rebuilds `review.bundle.js` with no error.

Run: `npm run test:frontend`
Expected: PASS — existing `buildVerifyBody` / `isAutoMultilingualRow` suites still green (no helper signature changed).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/static/v1/home-hifi/review.jsx src/subarr/static/v1/home-hifi/review.bundle.js
git commit -m "feat(406): multilingual bulk toggle in review toolbar

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Frontend — sort multilingual rows last, render the 🌐 chip, and Accept

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/review.jsx` (add `sortPendingRows` + `acceptMultilingualBody` exports; `EpisodeRow` chip; `acceptSelected` handler + Accept button)
- Modify (generated): `src/subarr/static/v1/home-hifi/review.bundle.js`
- Test: `src/subarr/static/v1/home-hifi/__tests__/review-multiselect.test.js`

- [ ] **Step 1: Write the failing test**

Append to `src/subarr/static/v1/home-hifi/__tests__/review-multiselect.test.js`, and add the two new names to the existing import on line 3. Change:

```js
import { buildVerifyBody, isAutoMultilingualRow } from '../review.jsx';
```

to:

```js
import {
  buildVerifyBody, isAutoMultilingualRow, sortPendingRows, acceptMultilingualBody,
} from '../review.jsx';
```

Then append:

```js
describe('sortPendingRows', () => {
  it('orders multilingual rows last, stable otherwise', () => {
    const rows = [
      { canonical_path: 'a', flag: 'suspect' },
      { canonical_path: 'b', flag: 'multilingual' },
      { canonical_path: 'c', flag: 'unknown' },
      { canonical_path: 'd', flag: 'multilingual' },
      { canonical_path: 'e', flag: 'track_mismatch' },
    ];
    const out = sortPendingRows(rows).map((r) => r.canonical_path);
    // non-multilingual keep original relative order; multilingual sink to the end.
    expect(out).toEqual(['a', 'c', 'e', 'b', 'd']);
  });

  it('does not mutate the input array', () => {
    const rows = [{ flag: 'multilingual' }, { flag: 'suspect' }];
    const copy = rows.slice();
    sortPendingRows(rows);
    expect(rows).toEqual(copy);
  });
});

describe('acceptMultilingualBody', () => {
  it('builds a user multi body from the row own lang_codes', () => {
    expect(acceptMultilingualBody({
      canonical_path: 'Movies/TheBeasts.mkv', lang_codes: ['gl', 'es', 'fr'],
    })).toEqual({
      canonical_path: 'Movies/TheBeasts.mkv', lang_code: 'gl', source: 'user',
      lang_class: 'multi', lang_codes: ['gl', 'es', 'fr'],
    });
  });

  it('prefers file_canonical_path when present', () => {
    expect(acceptMultilingualBody({
      file_canonical_path: 'F.mkv', canonical_path: 'C.mkv', lang_codes: ['es', 'en'],
    }).canonical_path).toBe('F.mkv');
  });

  it('returns null for a row missing lang_codes (caller skips)', () => {
    expect(acceptMultilingualBody({ canonical_path: 'x.mkv' })).toBe(null);
    expect(acceptMultilingualBody({ canonical_path: 'x.mkv', lang_codes: [] })).toBe(null);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:frontend`
Expected: FAIL — `sortPendingRows` and `acceptMultilingualBody` are not exported from `../review.jsx` (import resolves to `undefined`, calls throw / assertions fail).

- [ ] **Step 3: Write the pure helpers**

In `review.jsx`, after `isAutoMultilingualRow` (line 270) add:

```jsx
// #406: order the pending lane so auto-multilingual rows sink to the bottom —
// they are already applied (not blocking), just up for an eyeball. Stable for
// every other flag. Pure + non-mutating (returns a new array) so it is testable
// and safe to call in render.
export function sortPendingRows(rows) {
  return (rows || [])
    .map((r, i) => [r, i])
    .sort((a, b) => {
      const am = a[0].flag === 'multilingual' ? 1 : 0;
      const bm = b[0].flag === 'multilingual' ? 1 : 0;
      if (am !== bm) return am - bm;
      return a[1] - b[1];  // stable
    })
    .map(([r]) => r);
}

// #406: "Accept (keep as detected)" — re-submit a multilingual row's OWN set as
// a user verdict (source='user', lang_class='multi'). Distinct from the uniform
// bulk-assign: each row carries its own lang_codes. A row missing lang_codes
// (shouldn't happen) -> null so the caller skips it.
export function acceptMultilingualBody(row) {
  const codes = (row.lang_codes || []).filter(Boolean);
  if (codes.length === 0) return null;
  return {
    canonical_path: row.file_canonical_path || row.canonical_path,
    lang_code: codes[0], source: 'user', lang_class: 'multi', lang_codes: codes,
  };
}
```

- [ ] **Step 4: Run the new helper tests to verify they pass**

Run: `npm run test:frontend`
Expected: PASS — `sortPendingRows` and `acceptMultilingualBody` suites green, existing suites still green.

- [ ] **Step 5: Render the 🌐 chip on multilingual rows**

In `EpisodeRow` (line 122), the `FlagDot` component (line 18) renders the suspect/unknown dot. Extend `FlagDot` so a multilingual row shows a 🌐 chip instead of the dot. Replace `FlagDot` (lines 18-24):

```jsx
function FlagDot({ flag }) {
  // #406: multilingual rows are auto-detected multi-language files — badge them
  // with a distinct 🌐 chip, not the suspect/unknown status dot.
  if (flag === 'multilingual') {
    return (
      <span title="Auto-detected multilingual audio — review or accept as detected"
        aria-label="multilingual"
        style={{ fontSize: 'var(--text-2xs)' }}>🌐</span>
    );
  }
  const kind = flag === 'suspect' ? 'warn' : 'muted';
  const tip = flag === 'suspect'
    ? "File metadata likely lies — claims English on a foreign show"
    : "ffprobe couldn't determine audio language";
  return <span title={tip}><StatusDot kind={kind} /></span>;
}
```

- [ ] **Step 6: Wire the sort into the rendered list**

In the `useMemo` that builds `groups` (lines 553-599), sort each group's items with `sortPendingRows` AFTER the existing episode-number sort so multilingual rows sink last within a group. Replace the per-group sort block (lines 584-590):

```jsx
    // Sort each series's episodes by episode_number, series alphabetical.
    for (const g of byTitle.values()) {
      g.items.sort((a, b) => {
        const an = a.episode_number || '';
        const bn = b.episode_number || '';
        return an.localeCompare(bn, undefined, { numeric: true });
      });
      // #406: within a group, auto-multilingual rows render last (low priority).
      g.items = sortPendingRows(g.items);
    }
```

- [ ] **Step 6b: Add a `multilingual` count + filter tab (discoverability parity)**

The auto lane needs a visible count like the other flags, else the rows are only findable by scrolling the "all" list. In the counts loop (lines 555-560) add the tally — change:

```jsx
    const counts = { all: allItems.length, suspect: 0, unknown: 0, track_mismatch: 0 };
    for (const it of allItems) {
      if (it.flag === 'suspect') counts.suspect += 1;
      else if (it.flag === 'unknown') counts.unknown += 1;
      else if (it.flag === 'track_mismatch') counts.track_mismatch += 1;
    }
```

to:

```jsx
    const counts = { all: allItems.length, suspect: 0, unknown: 0, track_mismatch: 0, multilingual: 0 };
    for (const it of allItems) {
      if (it.flag === 'suspect') counts.suspect += 1;
      else if (it.flag === 'unknown') counts.unknown += 1;
      else if (it.flag === 'track_mismatch') counts.track_mismatch += 1;
      else if (it.flag === 'multilingual') counts.multilingual += 1;  // #406
    }
```

Then add the tab entry to the filter chip list (after line 701's `track_mismatch` entry):

```jsx
    { id: 'track_mismatch', label: `track mismatch (${totalCounts.track_mismatch})` },
    { id: 'multilingual', label: `multilingual (${totalCounts.multilingual})` },  // #406
```

The existing `filter` logic (`if (filter !== 'all' && it.flag !== filter) return false`) already isolates the tab when clicked — no other change needed. Rows still appear inline in the "all" view, sorted last.

- [ ] **Step 7: Add `acceptSelected` handler + partition multilingual selection**

In the `useMemo` that computes `selTmItems`/`selAssignPaths` (lines 605-614), also surface the selected multilingual rows. Replace:

```jsx
  const { selTmItems, selAssignPaths } = useMemo(() => {
    const items = data?.items || [];
    const sel = items.filter((it) => epSelection.has(it.file_canonical_path || it.canonical_path));
    return {
      selTmItems: sel.filter((it) => it.flag === 'track_mismatch'),
      selAssignPaths: sel
        .filter((it) => it.flag !== 'track_mismatch')
        .map((it) => it.file_canonical_path || it.canonical_path),
    };
  }, [data, epSelection]);
```

with:

```jsx
  const { selTmItems, selAssignPaths, selMultiRows } = useMemo(() => {
    const items = data?.items || [];
    const sel = items.filter((it) => epSelection.has(it.file_canonical_path || it.canonical_path));
    return {
      selTmItems: sel.filter((it) => it.flag === 'track_mismatch'),
      selAssignPaths: sel
        .filter((it) => it.flag !== 'track_mismatch')
        .map((it) => it.file_canonical_path || it.canonical_path),
      // #406: multilingual rows selected for "Accept (keep as detected)".
      selMultiRows: sel.filter((it) => it.flag === 'multilingual'),
    };
  }, [data, epSelection]);
```

Then add the `acceptSelected` handler after `applyBulk` (after line 695), reusing applyBulk's 4-worker/progress pattern:

```jsx
  // #406: "Accept (keep as detected)" — confirm each selected multilingual row's
  // OWN detected set as a user verdict (source='user'). Per-row (each carries its
  // own lang_codes), distinct from the uniform bulk-assign. Reuses the 4-worker
  // progress pattern; rows missing lang_codes are skipped (builder returns null).
  const acceptSelected = useCallback(async () => {
    const rows = selMultiRows;
    if (!rows.length) return;
    setBulkRunning(true);
    setBulkProgress({ done: 0, total: rows.length, errors: 0 });
    let done = 0; let errors = 0;
    const queue = rows.slice();
    async function worker() {
      while (queue.length) {
        const row = queue.shift();
        const body = acceptMultilingualBody(row);
        const p = row.file_canonical_path || row.canonical_path;
        if (!body) { done += 1; setBulkProgress({ done, total: rows.length, errors }); continue; }
        try {
          const r = await fetch('/api/audio-lang/verifications', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ ...body, confidence: 1.0, evidence: { accept_multi: true } }),
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          window.dispatchEvent(new CustomEvent('audio-lang-verified', {
            detail: { file_canonical_path: p, lang_code: body.lang_code },
          }));
        } catch (e) {
          // eslint-disable-next-line no-console
          console.error('accept multilingual failed for', p, e);
          errors += 1;
        }
        done += 1;
        setBulkProgress({ done, total: rows.length, errors });
      }
    }
    await Promise.all([worker(), worker(), worker(), worker()]);
    setBulkRunning(false);
    clearSelection();
    fetchPending({ silent: true });
  }, [selMultiRows, fetchPending, clearSelection]);
```

- [ ] **Step 8: Add the Accept button to the toolbar**

In the bulk toolbar, after the track-mismatch block (after line 1005, before the `{bulkRunning && (` progress span), add a block shown when multilingual rows are selected:

```jsx
          {/* #406: selected multilingual rows — confirm each row's own detected
              set as a user verdict so the lane empties as they are reviewed. */}
          {selMultiRows.length > 0 && (
            <>
              <span style={{ color: 'var(--bg-5)' }}>·</span>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-2)' }}>
                🌐 {selMultiRows.length} multilingual
              </span>
              <button className="btn primary" onClick={acceptSelected} disabled={bulkRunning}
                title="Confirm each selected file's detected language set as-is (marks them user-verified so they leave this list).">
                {bulkRunning ? 'Applying…' : `Accept (keep as detected) (${selMultiRows.length})`}
              </button>
            </>
          )}
```

- [ ] **Step 9: Rebuild the bundle and run the full frontend gate**

Run: `npm run build:frontend`
Expected: rebuilds `review.bundle.js` with no error.

Run: `npm run test:frontend`
Expected: PASS — all suites green.

Run: `npm run check:frontend`
Expected: PASS — no `git diff` (bundle already rebuilt + will be staged). If it reports drift, the bundle was stale; it has now been rebuilt.

- [ ] **Step 10: Commit**

```bash
git add src/subarr/static/v1/home-hifi/review.jsx src/subarr/static/v1/home-hifi/review.bundle.js src/subarr/static/v1/home-hifi/__tests__/review-multiselect.test.js
git commit -m "feat(406): sort multilingual last, 🌐 chip, and Accept action

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Full verification gate + hand back to controller

**Files:** none (verification only)

- [ ] **Step 1: Backend full suite**

Run: `python -m pytest -q`
Expected: PASS — full suite green (new `tests/test_pending_review_multilingual.py` included, no regressions).

- [ ] **Step 2: Frontend suite + build + bundle-drift check**

Run: `npm run test:frontend`
Expected: PASS.

Run: `npm run build:frontend`
Expected: rebuilds bundles cleanly.

Run: `npm run check:frontend`
Expected: PASS — no bundle/html drift (`git diff --exit-code` clean).

- [ ] **Step 3: Ruff on the changed Python**

Run: `python -m ruff check src/subarr/routers/audio_lang.py tests/test_pending_review_multilingual.py`
Expected: PASS — no findings.

Run: `python -m ruff format --check src/subarr/routers/audio_lang.py tests/test_pending_review_multilingual.py`
Expected: PASS — already formatted. (If it reports a diff, run `python -m ruff format <files>` and amend the relevant commit — the heredoc-appended test file is the likely culprit.)

- [ ] **Step 4: Confirm the working tree is clean**

Run: `git status --porcelain`
Expected: empty (everything committed across Tasks 1-3).

- [ ] **Step 5: Hand back to the controller**

Do NOT push, merge, or open a PR. Report to the controller: the four acceptance criteria demonstrated (auto-multi surfaces + sorts last; correct via toolbar drops it; Accept drops it; user-multi never re-appears), full suite + vitest + build green, ruff clean. The controller performs the risk-tiered pre-merge review and the PR.

---

## Spec coverage

| Spec requirement (design doc section) | Task |
|---|---|
| §1 Backend: `pending_review` branch keyed on store source, before the already-verified skip | Task 1 (Step 3) |
| §1 `source == 'auto-high-conf-multi'` → `flag='multilingual'`, `extra={'lang_codes': ...}` | Task 1 (Step 3) |
| §1 Exclude user-confirmed multi (`source=='user'`) | Task 1 (Step 3, `test_user_confirmed_multilingual_is_not_surfaced`) |
| §1 Track-mismatch keeps FIRST precedence | Task 1 (Step 3, branch order) |
| §2 "Multilingual" checkbox mirroring "Remember for future" | Task 2 (Step 3) |
| §2 off = single `<select>` unchanged; on = checkable list backed by `bulkLangs` | Task 2 (Steps 1, 3) |
| §2 Apply routes through `buildVerifyBody(path, on ? bulkLangs : [bulkLang])` | Task 2 (Step 2) |
| §2 Skip file when `buildVerifyBody` returns null | Task 2 (Step 2) |
| §2 "Remember for future" disabled while Multilingual on | Task 2 (Step 3) |
| §3 Auto-multilingual rows sorted last | Task 3 (Steps 1, 3, 6 — `sortPendingRows`) |
| §3 Distinct 🌐 chip | Task 3 (Step 5 — `FlagDot`) |
| §3 Correction lifecycle (POST source=user drops row) | Task 2 (correction) + Task 1 (source filter drops it on next fetch) |
| §3 Accept action re-submits each row's own `lang_codes` as source=user | Task 3 (Steps 1, 7, 8 — `acceptMultilingualBody` + `acceptSelected`) |
| §Error handling: row missing `lang_codes` → Accept skips, renders anyway | Task 3 (Step 1 null test, Step 7 skip) |
| §Error handling: Accept/correct POST failure surfaced in bulk-progress | Task 3 (Step 7 error counter) |
| §Testing backend: auto surfaces / user not / suspect unaffected | Task 1 (Step 1, 3 tests) |
| §Testing frontend: sort helper + Accept-payload builder | Task 3 (Step 1) |
| §Acceptance 1 (appears, 🌐, sorted last) | Tasks 1 + 3 |
| §Acceptance 2 (correct → drops) | Task 2 + Task 1 |
| §Acceptance 3 (Accept → user, drops) | Task 3 |
| §Acceptance 4 (create multilingual verdict from any assignable row) | Task 2 |
| §Acceptance 5 (user-multi never re-appears; full suite + vitest + ruff green) | Task 1 + Task 4 |

## Name/type consistency check

Verified identical across tasks: `multilingualMode`, `bulkLangs`, `toggleBulkLang`, `selMultiRows`, `acceptSelected`, `sortPendingRows`, `acceptMultilingualBody`, `buildVerifyBody` (existing), `flag === 'multilingual'`, store source string `'auto-high-conf-multi'`, POST body shape `{canonical_path, lang_code, source:'user', lang_class:'multi', lang_codes}`.
