# Series-Intent UI Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the already-built series/title audio-language "intent" feature usable from the UI — declare a whole show/movie's language from the Review page, manage rules from Settings.

**Architecture:** Backend already owns the durable rule + coverage inheritance (`audio_lang_store.set_series_intent`, `_build_verification_lookup`). This plan (1) makes the PUT/DELETE handlers kick a coverage refresh so changes show immediately, (2) enriches GET with `covered_count` + `media_type` from the coverage snapshot, (3) adds a "Remember for future downloads" checkbox to the Review bulk bar that writes one intent per distinct title-prefix, and (4) adds a Settings "Language rules" panel (flags, movies, alphabetical, A–Z ladder).

**Tech Stack:** Python 3.12 + FastAPI (backend), React (global, no JSX transform beyond esbuild) bundled via esbuild (`npm run build:frontend`), pytest. Pure frontend logic lives in a plain ESM `.mjs` module tested with `node`.

**Conventions used throughout:**
- pytest runs need `PYTHONPATH=C:\Projects\subarr\src` (editable install points at a stale path). On Windows PowerShell: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest ...`. Examples below use the bash form `PYTHONPATH=C:/Projects/subarr/src python -m pytest ...`.
- Frontend changes require `npm run build:frontend` then `wsl -e bash -lc "docker restart subarr-next"`; test the live app on **:9923** (never :9922).
- Work happens on branch `feat/series-intent-ui` (already created).

---

## File Structure

- `src/subarr/routers/audio_lang.py` — series-intent handlers: refresh kicks (PUT/DELETE) + GET enrichment. **Modify.**
- `src/subarr/static/v1/home-hifi/lang-rules-util.mjs` — pure helpers (prefix derivation, title, alphabetical grouping, ladder letters). **Create.**
- `src/subarr/static/v1/home-hifi/review.jsx` — bulk-bar checkbox + per-prefix intent PUT. **Modify.**
- `src/subarr/static/v1/home-hifi/settings.jsx` — "Language rules" rail item + `LangRulesPanel`. **Modify.**
- `tests/test_audio_lang_series_intent_routes.py` — existing route test (committed in Task 1); extended in Tasks 2–4. **Modify.**
- `tests/frontend/lang-rules-util.test.mjs` — node tests for the pure util. **Create.**

---

## Task 1: Commit the route fix + existing route test (foundation)

The route fix (decorators → bare `/series-intent`) and `tests/test_audio_lang_series_intent_routes.py` already exist in the working tree from the bug-fix step. Lock them in before building on top.

**Files:**
- Modify: `src/subarr/routers/audio_lang.py` (already edited — lines 297/313/319 now bare paths)
- Test: `tests/test_audio_lang_series_intent_routes.py` (already created)

- [ ] **Step 1: Run the existing route test to confirm it passes**

Run: `PYTHONPATH=C:/Projects/subarr/src python -m pytest tests/test_audio_lang_series_intent_routes.py -q`
Expected: `4 passed` (route-table single-path, GET resolves, double-prefix 404, PUT→GET roundtrip).

- [ ] **Step 2: Commit**

```bash
git add src/subarr/routers/audio_lang.py tests/test_audio_lang_series_intent_routes.py
git commit -m "fix(audio-lang): series-intent routes were double-prefixed

The router carries prefix=/api/audio-lang but the series-intent PUT/GET/DELETE
decorators repeated it (/audio-lang/series-intent), resolving to the unreachable
/api/audio-lang/audio-lang/series-intent. Bare paths now resolve at the intended
/api/audio-lang/series-intent. Adds a route-table regression test.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: PUT /series-intent kicks a coverage refresh

So existing episodes flip green within seconds instead of waiting for the next scheduled walk — mirroring what the per-file `upsert_verification` already does.

**Files:**
- Modify: `src/subarr/routers/audio_lang.py:297-310` (`upsert_series_intent`)
- Test: `tests/test_audio_lang_series_intent_routes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_audio_lang_series_intent_routes.py`:

```python
class _RefreshSpy:
    """Stand-in for coverage_cache that records request_refresh calls and
    serves an empty snapshot."""
    def __init__(self):
        self.refresh_calls = 0

    def request_refresh(self, *args, **kwargs):
        self.refresh_calls += 1

    def get_cached(self):
        return None


def test_put_series_intent_kicks_coverage_refresh(app_with_stub):
    spy = _RefreshSpy()
    app_with_stub.app.state.coverage_cache = spy
    r = app_with_stub.put(SINGLE, json={"series_prefix": "TV/Cheers/", "lang_code": "eng"})
    assert r.status_code == 200
    assert spy.refresh_calls == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=C:/Projects/subarr/src python -m pytest tests/test_audio_lang_series_intent_routes.py::test_put_series_intent_kicks_coverage_refresh -q`
Expected: FAIL — `assert 0 == 1` (handler doesn't refresh yet).

- [ ] **Step 3: Add the refresh kick**

In `src/subarr/routers/audio_lang.py`, in `upsert_series_intent`, replace the body's return with a refresh kick before it. The function currently ends:

```python
    store.set_series_intent(
        series_prefix=req.series_prefix,
        lang_code=req.lang_code,
        note=req.note,
    )
    return {"ok": True, "series_prefix": req.series_prefix, "lang_code": req.lang_code.lower()}
```

Change to:

```python
    store.set_series_intent(
        series_prefix=req.series_prefix,
        lang_code=req.lang_code,
        note=req.note,
    )
    # Kick a coalesced coverage refresh so episodes under the prefix flip
    # green within seconds (mirrors upsert_verification). Best-effort.
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    if cov_cache is not None:
        bundle = request.app.state.integrations
        probe_store = request.app.state.probe_store
        cov_cache.request_refresh(bundle, probe_store, store)
    return {"ok": True, "series_prefix": req.series_prefix, "lang_code": req.lang_code.lower()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=C:/Projects/subarr/src python -m pytest tests/test_audio_lang_series_intent_routes.py::test_put_series_intent_kicks_coverage_refresh -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/audio_lang.py tests/test_audio_lang_series_intent_routes.py
git commit -m "feat(audio-lang): series-intent PUT kicks coverage refresh

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: DELETE /series-intent kicks refresh; missing rule still 404s

**Files:**
- Modify: `src/subarr/routers/audio_lang.py:319-325` (`delete_series_intent`)
- Test: `tests/test_audio_lang_series_intent_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audio_lang_series_intent_routes.py`:

```python
def test_delete_series_intent_kicks_coverage_refresh(app_with_stub):
    app_with_stub.app.state.audio_lang.set_series_intent(
        series_prefix="TV/Cheers/", lang_code="eng")
    spy = _RefreshSpy()
    app_with_stub.app.state.coverage_cache = spy
    r = app_with_stub.delete(SINGLE, params={"series_prefix": "TV/Cheers/"})
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert spy.refresh_calls == 1


def test_delete_missing_series_intent_404(app_with_stub):
    r = app_with_stub.delete(SINGLE, params={"series_prefix": "TV/DoesNotExist/"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=C:/Projects/subarr/src python -m pytest tests/test_audio_lang_series_intent_routes.py -k delete -q`
Expected: `test_delete_series_intent_kicks_coverage_refresh` FAILS (`assert 0 == 1`); `test_delete_missing_series_intent_404` already PASSES (handler already 404s on miss).

- [ ] **Step 3: Add the refresh kick on successful delete**

In `src/subarr/routers/audio_lang.py`, `delete_series_intent` currently:

```python
    store = request.app.state.audio_lang
    removed = store.delete_series_intent(series_prefix)
    if not removed:
        raise HTTPException(404, detail=f"no intent declared for {series_prefix}")
    return {"deleted": True, "series_prefix": series_prefix}
```

Change to:

```python
    store = request.app.state.audio_lang
    removed = store.delete_series_intent(series_prefix)
    if not removed:
        raise HTTPException(404, detail=f"no intent declared for {series_prefix}")
    # Revoking a rule must re-evaluate coverage so inherited episodes revert.
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    if cov_cache is not None:
        bundle = request.app.state.integrations
        probe_store = request.app.state.probe_store
        cov_cache.request_refresh(bundle, probe_store, store)
    return {"deleted": True, "series_prefix": series_prefix}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=C:/Projects/subarr/src python -m pytest tests/test_audio_lang_series_intent_routes.py -k delete -q`
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/audio_lang.py tests/test_audio_lang_series_intent_routes.py
git commit -m "feat(audio-lang): series-intent DELETE kicks coverage refresh

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: GET /series-intent enriches each rule with covered_count + media_type

The Settings list needs a per-rule episode/file count and a show-vs-movie type. Compute both from the coverage snapshot (best-effort: empty list when no snapshot). `title` is derived in the frontend, so it is NOT added here.

**Files:**
- Modify: `src/subarr/routers/audio_lang.py:313-316` (`list_series_intents`)
- Test: `tests/test_audio_lang_series_intent_routes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_audio_lang_series_intent_routes.py`:

```python
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


def test_get_series_intent_enriches_count_and_media_type(app_with_stub):
    store = app_with_stub.app.state.audio_lang
    store.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    store.set_series_intent(series_prefix="Movies/Parasite (2019)/", lang_code="kor")
    app_with_stub.app.state.coverage_cache = _SnapCache([
        {"file_canonical_path": "TV/Cheers/Season 1/e1.mkv", "media_type": "episode"},
        {"file_canonical_path": "TV/Cheers/Season 1/e2.mkv", "media_type": "episode"},
        {"file_canonical_path": "Movies/Parasite (2019)/Parasite.mkv", "media_type": "movie"},
    ])
    body = app_with_stub.get(SINGLE).json()
    by_prefix = {it["series_prefix"]: it for it in body["items"]}
    assert by_prefix["TV/Cheers/"]["covered_count"] == 2
    assert by_prefix["TV/Cheers/"]["media_type"] == "show"
    assert by_prefix["Movies/Parasite (2019)/"]["covered_count"] == 1
    assert by_prefix["Movies/Parasite (2019)/"]["media_type"] == "movie"


def test_get_series_intent_without_snapshot_returns_rules(app_with_stub):
    app_with_stub.app.state.audio_lang.set_series_intent(
        series_prefix="TV/Cheers/", lang_code="eng")
    app_with_stub.app.state.coverage_cache = _RefreshSpy()  # get_cached() -> None
    body = app_with_stub.get(SINGLE).json()
    assert body["items"][0]["series_prefix"] == "TV/Cheers/"
    assert body["items"][0]["covered_count"] == 0
    assert body["items"][0]["media_type"] == "show"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=C:/Projects/subarr/src python -m pytest tests/test_audio_lang_series_intent_routes.py -k "enriches or without_snapshot" -q`
Expected: FAIL with `KeyError: 'covered_count'`.

- [ ] **Step 3: Implement the enrichment**

In `src/subarr/routers/audio_lang.py`, replace `list_series_intents`:

```python
@router.get("/series-intent")
async def list_series_intents(request: Request) -> dict[str, Any]:
    store = request.app.state.audio_lang
    return {"items": store.list_series_intents()}
```

with:

```python
@router.get("/series-intent")
async def list_series_intents(request: Request) -> dict[str, Any]:
    store = request.app.state.audio_lang
    rows = store.list_series_intents()
    # Best-effort enrichment from the coverage snapshot: how many files each
    # rule currently covers, and whether it's a show or a movie. No snapshot
    # (fresh boot) → count 0, default "show". title is derived client-side.
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    snap = cov_cache.get_cached() if cov_cache is not None else None
    snap_items = snap.items if snap is not None else []
    enriched = []
    for row in rows:
        prefix = row["series_prefix"]
        count = 0
        media_type = "show"
        for it in snap_items:
            p = it.get("file_canonical_path") or it.get("canonical_path") or ""
            if p.startswith(prefix):
                count += 1
                if it.get("media_type") == "movie":
                    media_type = "movie"
        enriched.append({**row, "covered_count": count, "media_type": media_type})
    return {"items": enriched}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=C:/Projects/subarr/src python -m pytest tests/test_audio_lang_series_intent_routes.py -k "enriches or without_snapshot" -q`
Expected: `2 passed`.

- [ ] **Step 5: Run the whole route-test file**

Run: `PYTHONPATH=C:/Projects/subarr/src python -m pytest tests/test_audio_lang_series_intent_routes.py tests/test_coverage_series_intent.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/subarr/routers/audio_lang.py tests/test_audio_lang_series_intent_routes.py
git commit -m "feat(audio-lang): enrich GET series-intent with covered_count + media_type

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Pure frontend util module (`lang-rules-util.mjs`) + node tests

Centralize the pure logic both Review and Settings need: derive distinct series prefixes from a selection, derive a title from a prefix, group rules alphabetically, and compute which ladder letters are active. ESM `.mjs` so plain `node` can test it (no JSX/React in this file).

**Files:**
- Create: `src/subarr/static/v1/home-hifi/lang-rules-util.mjs`
- Test: `tests/frontend/lang-rules-util.test.mjs`

- [ ] **Step 1: Write the failing test**

Create `tests/frontend/lang-rules-util.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import {
  distinctSeriesPrefixes, deriveTitle, ladderLetterFor,
  groupRulesAlphabetically, activeLadderLetters,
} from '../../src/subarr/static/v1/home-hifi/lang-rules-util.mjs';

// distinctSeriesPrefixes: map selected file paths -> distinct series roots (+ '/')
const items = [
  { file_canonical_path: 'TV/Cheers/Season 1/e1.mkv', canonical_path: 'TV/Cheers' },
  { file_canonical_path: 'TV/Cheers/Season 1/e2.mkv', canonical_path: 'TV/Cheers' },
  { file_canonical_path: 'Movies/Parasite (2019)/p.mkv', canonical_path: 'Movies/Parasite (2019)' },
];
const prefixes = distinctSeriesPrefixes(
  ['TV/Cheers/Season 1/e1.mkv', 'TV/Cheers/Season 1/e2.mkv', 'Movies/Parasite (2019)/p.mkv'],
  items,
).sort();
assert.deepEqual(prefixes, ['Movies/Parasite (2019)/', 'TV/Cheers/']);

// falls back to the path itself (slash-terminated) when not found in items
assert.deepEqual(distinctSeriesPrefixes(['TV/Unknown/x.mkv'], []), ['TV/Unknown/x.mkv/']);

// deriveTitle: last non-empty segment of the prefix
assert.equal(deriveTitle('TV/Cheers/'), 'Cheers');
assert.equal(deriveTitle('Movies/Parasite (2019)/'), 'Parasite (2019)');

// ladderLetterFor: A-Z uppercase, non-alpha -> '#'
assert.equal(ladderLetterFor('Cheers'), 'C');
assert.equal(ladderLetterFor('300'), '#');

// groupRulesAlphabetically: sections sorted, rules sorted by title within
const rules = [
  { series_prefix: 'TV/Squid Game/', lang_code: 'kor' },
  { series_prefix: 'TV/Cheers/', lang_code: 'eng' },
  { series_prefix: 'Movies/Parasite (2019)/', lang_code: 'kor' },
];
const groups = groupRulesAlphabetically(rules);
assert.deepEqual(groups.map(g => g.letter), ['C', 'P', 'S']);
assert.equal(groups[0].rules[0].title, 'Cheers');

// activeLadderLetters: set of present first-letters
const active = activeLadderLetters(rules);
assert.ok(active.has('C') && active.has('P') && active.has('S'));
assert.ok(!active.has('A'));

console.log('lang-rules-util: all assertions passed');
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node tests/frontend/lang-rules-util.test.mjs`
Expected: FAIL — `Cannot find module '.../lang-rules-util.mjs'`.

- [ ] **Step 3: Implement the util**

Create `src/subarr/static/v1/home-hifi/lang-rules-util.mjs`:

```javascript
// Pure helpers for the series/title audio-language "intent" UI. No React/JSX
// here so the logic is unit-testable with plain `node` (see
// tests/frontend/lang-rules-util.test.mjs). Imported by review.jsx + settings.jsx.

// Map a set of selected file canonical paths to the DISTINCT series/movie
// prefixes they belong to (each slash-terminated, matching the store's
// series_prefix convention). The series root comes from each item's
// canonical_path; if a path isn't found in `items`, fall back to the path
// itself so we never silently drop a selection.
export function distinctSeriesPrefixes(selectedPaths, items) {
  const rootByFile = new Map();
  for (const it of items || []) {
    const file = it.file_canonical_path || it.canonical_path;
    const root = it.canonical_path || it.file_canonical_path;
    if (file && root) rootByFile.set(file, root);
  }
  const out = new Set();
  for (const p of selectedPaths || []) {
    const root = rootByFile.get(p) || p;
    out.add(root.endsWith('/') ? root : root + '/');
  }
  return Array.from(out);
}

// Last non-empty path segment of a prefix, e.g. "TV/Cheers/" -> "Cheers".
export function deriveTitle(prefix) {
  const parts = String(prefix || '').split('/').filter(Boolean);
  return parts.length ? parts[parts.length - 1] : String(prefix || '');
}

// A-Z ladder bucket for a title: uppercase first letter, or '#' for non-alpha.
export function ladderLetterFor(title) {
  const c = String(title || '').trim().charAt(0).toUpperCase();
  return c >= 'A' && c <= 'Z' ? c : '#';
}

// Group rules into alphabetical sections by derived title. Each rule is
// annotated with a derived `title`. Sections sorted by letter; rules sorted by
// title within each section. Returns [{ letter, rules: [{...rule, title}] }].
export function groupRulesAlphabetically(rules) {
  const withTitle = (rules || []).map((r) => ({ ...r, title: deriveTitle(r.series_prefix) }));
  const byLetter = new Map();
  for (const r of withTitle) {
    const letter = ladderLetterFor(r.title);
    if (!byLetter.has(letter)) byLetter.set(letter, []);
    byLetter.get(letter).push(r);
  }
  const letters = Array.from(byLetter.keys()).sort();
  return letters.map((letter) => ({
    letter,
    rules: byLetter.get(letter).sort((a, b) =>
      a.title.localeCompare(b.title, undefined, { sensitivity: 'base' })),
  }));
}

// Set of ladder letters that have at least one rule (for highlighting the
// right-hand A-Z ladder).
export function activeLadderLetters(rules) {
  const set = new Set();
  for (const r of rules || []) set.add(ladderLetterFor(deriveTitle(r.series_prefix)));
  return set;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node tests/frontend/lang-rules-util.test.mjs`
Expected: `lang-rules-util: all assertions passed`.

- [ ] **Step 5: Commit**

```bash
git add src/subarr/static/v1/home-hifi/lang-rules-util.mjs tests/frontend/lang-rules-util.test.mjs
git commit -m "feat(ui): pure helpers for series-intent UI (prefix/title/ladder)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Review page — "Remember for future downloads" checkbox

When the user bulk-assigns a language to whole shows, an opt-in checkbox also writes the durable intent rule (one per distinct title-prefix in the selection). The existing per-file apply is unchanged; the intent PUT is best-effort and must not fail the bulk.

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/review.jsx` (import at top; new state near line 227; PUT loop inside `applyBulk` ~line 390; checkbox in the bulk bar ~line 566)

- [ ] **Step 1: Add the util import**

At the top of `review.jsx`, below the existing imports (after line 12 `import { AudioReviewModal } from './coverage.jsx';`), add:

```javascript
import { distinctSeriesPrefixes } from './lang-rules-util.mjs';
```

- [ ] **Step 2: Add checkbox state**

After the bulk state declarations (after `const [bulkProgress, setBulkProgress] = useState({ done: 0, total: 0, errors: 0 });`, line 227), add:

```javascript
  // #226: also declare a durable series/movie language rule so FUTURE
  // downloads (new episodes, re-grabbed movies) inherit the language.
  const [rememberFuture, setRememberFuture] = useState(true);
```

- [ ] **Step 3: Write the intent PUTs inside `applyBulk`**

In `applyBulk`, immediately after the per-file workers finish (`await Promise.all([worker(), worker(), worker(), worker()]);`, line 390) and BEFORE `setBulkRunning(false);`, insert:

```javascript
    // #226: if requested, declare one durable intent rule per distinct
    // series/movie in the selection. Best-effort — failures here never fail
    // the per-file bulk above (the primary action); they bump the error count.
    if (rememberFuture) {
      const prefixes = distinctSeriesPrefixes(paths, data?.items || []);
      for (const prefix of prefixes) {
        try {
          const r = await fetch('/api/audio-lang/series-intent', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ series_prefix: prefix, lang_code: bulkLang }),
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
        } catch (e) {
          // eslint-disable-next-line no-console
          console.error('series-intent declare failed for', prefix, e);
          errors += 1;
          setBulkProgress({ done, total: paths.length, errors });
        }
      }
    }
```

Then update `applyBulk`'s dependency array (line 395) from:

```javascript
  }, [epSelection, bulkLang, fetchPending, clearSelection]);
```

to:

```javascript
  }, [epSelection, bulkLang, fetchPending, clearSelection, rememberFuture, data]);
```

- [ ] **Step 4: Add the checkbox to the bulk bar**

In the bulk bar, right after the language `<select>` closes (after line 579, the `</select>` of the `bulkLang` select) and before the `{bulkRunning && (` block, insert:

```javascript
          <label style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            fontSize: 'var(--text-xs)', color: 'var(--fg-1)', cursor: 'pointer',
          }}
            title="Also save a rule so new episodes — and re-downloaded movies — of these titles inherit this language automatically. A per-file correction always overrides it.">
            <input type="checkbox" checked={rememberFuture}
              onChange={(e) => setRememberFuture(e.target.checked)}
              disabled={bulkRunning}
              style={{ accentColor: 'var(--violet-500)' }} />
            Remember for future downloads
          </label>
```

- [ ] **Step 5: Build the frontend**

Run: `npm run build:frontend`
Expected: completes without error; `review.bundle.js` rebuilt (it now inlines `lang-rules-util.mjs`).

- [ ] **Step 6: Deploy and verify in the live app**

```bash
wsl -e bash -lc "docker restart subarr-next"
```

Then verify the round-trip without needing the UI to have pending data — confirm the bundle wires the endpoint by checking the API directly, then confirm the checkbox renders:

```bash
# API still healthy + intent endpoint reachable (should be {"items":[...]})
curl -s http://localhost:9923/api/audio-lang/series-intent
```

Manual UI check (on :9923): open **Review**, tick a show, confirm the bulk bar shows the "Remember for future downloads" checkbox next to the language picker. (If there is pending data: assign a language with the box checked, then confirm the rule appears via `curl -s http://localhost:9923/api/audio-lang/series-intent`.)
Expected: checkbox visible; after an apply-with-check, the declared prefix appears in the GET response.

- [ ] **Step 7: Commit**

```bash
git add src/subarr/static/v1/home-hifi/review.jsx src/subarr/static/v1/home-hifi/review.bundle.js src/subarr/static/v1/home-hifi/review.bundle.js.map
git commit -m "feat(review): 'Remember for future downloads' declares series-intent

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Settings — "Language rules" management panel

A new rail item under the "subarr" group renders a `LangRulesPanel`: alphabetical unified list (shows + movies), flag chips, filter pills, internal scroll, A–Z ladder, and Revoke.

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/settings.jsx` (imports; `SettingsRail` group ~line 887; `SettingsPage` view state/breadcrumb/heading/render ~lines 1877–1985; new `LangRulesPanel` component)

- [ ] **Step 1: Add imports**

Find the existing top-of-file imports in `settings.jsx` (they import from `./atoms.jsx`). Ensure `LangTag` is imported and add the util import. If the file already imports from `./atoms.jsx`, add `LangTag` to that import list; otherwise add a new line. Then add the util import beneath it:

```javascript
import { LangTag } from './atoms.jsx';
import {
  deriveTitle, groupRulesAlphabetically, activeLadderLetters,
} from './lang-rules-util.mjs';
```

(If `./atoms.jsx` is already imported with other names, merge `LangTag` into that destructuring instead of duplicating the import.)

- [ ] **Step 2: Add the rail item**

In `SettingsRail`, the "subarr" group array (lines 887–892) currently lists Providers / System actions / Updates / Telemetry. Add a Language-rules entry and thread two new props. Change the function signature (line 836) to add `langRulesActive, onSelectLangRules`:

```javascript
function SettingsRail({ items, selectedId, onSelect, systemActive, onSelectSystem, telemetryActive, onSelectTelemetry, updatesActive, onSelectUpdates, providersActive, onSelectProviders, langRulesActive, onSelectLangRules }) {
```

Update the `active` computation for integration items (line 865) to also exclude the new view:

```javascript
          const active = it.id === selectedId && !systemActive && !telemetryActive && !updatesActive && !providersActive && !langRulesActive;
```

And add to the subarr group array (insert after the `providers` entry, line 888):

```javascript
          { id: 'lang-rules', label: 'Language rules', active: langRulesActive, onClick: onSelectLangRules },
```

- [ ] **Step 3: Wire the view into `SettingsPage`**

In `SettingsPage`:

(a) breadcrumb (after the `providers` line, ~1913):
```javascript
    : view === 'lang-rules' ? ['Settings', 'Language rules']
```
(b) heading (~1921):
```javascript
    : view === 'lang-rules' ? 'Language rules'
```
(c) subhead (~1929):
```javascript
    : view === 'lang-rules' ? 'Declared audio languages for whole shows and movies. New downloads inherit automatically; a per-file correction always overrides.'
```
(d) hash support (in the first-mount effect, line 1889 list) — change:
```javascript
    if (['providers', 'telemetry', 'system', 'updates'].includes(hash)) {
```
to:
```javascript
    if (['providers', 'telemetry', 'system', 'updates', 'lang-rules'].includes(hash)) {
```
(e) pass the props to `<SettingsRail .../>` (after the `providersActive` line, ~1941):
```javascript
        langRulesActive={view === 'lang-rules'} onSelectLangRules={() => setView('lang-rules')}
```
(f) render the panel (after the `{view === 'providers' && <ProvidersPanel />}` line, ~1985):
```javascript
          {view === 'lang-rules' && <LangRulesPanel />}
```

- [ ] **Step 4: Add the `LangRulesPanel` component**

Add this component to `settings.jsx` (place it just before `export function SettingsPage()`, ~line 1871):

```javascript
// #226: manage declared series/movie audio-language rules. Lists rules
// alphabetically (shows + movies unified), with flag chips, type filter,
// internal scroll, and an A-Z ladder. Declaring happens on the Review page;
// this surface is view + revoke only.
function LangRulesPanel() {
  const [rules, setRules] = useState(null);
  const [error, setError] = useState(null);
  const [typeFilter, setTypeFilter] = useState('all'); // all|show|movie
  const listRef = React.useRef(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/audio-lang/series-intent', { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const body = await r.json();
      setRules(body.items || []);
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const revoke = useCallback(async (prefix) => {
    if (!window.confirm(`Revoke the language rule for "${deriveTitle(prefix)}"?\n\nFuture downloads will no longer inherit it. Existing per-file verifications are kept.`)) return;
    try {
      const r = await fetch(`/api/audio-lang/series-intent?series_prefix=${encodeURIComponent(prefix)}`, {
        method: 'DELETE', credentials: 'same-origin',
      });
      // 404 = already gone; treat as success and just refresh.
      if (!r.ok && r.status !== 404) throw new Error(`HTTP ${r.status}`);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('revoke failed', e);
    }
    load();
  }, [load]);

  const filtered = (rules || []).filter((r) =>
    typeFilter === 'all' ? true : (r.media_type || 'show') === typeFilter);
  const groups = groupRulesAlphabetically(filtered);
  const active = activeLadderLetters(filtered);
  const counts = {
    all: (rules || []).length,
    show: (rules || []).filter((r) => (r.media_type || 'show') === 'show').length,
    movie: (rules || []).filter((r) => r.media_type === 'movie').length,
  };

  const jumpTo = (letter) => {
    const el = listRef.current && listRef.current.querySelector(`[data-letter="${letter}"]`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  if (error && !rules) {
    return (
      <div style={{ padding: 20, color: 'var(--error-500)' }}>
        Couldn't load language rules: {String(error.message || error)}
        <div style={{ marginTop: 12 }}><button className="btn" onClick={load}>Retry</button></div>
      </div>
    );
  }
  if (!rules) return <div style={{ padding: 20, color: 'var(--fg-2)' }}>Loading language rules…</div>;
  if (rules.length === 0) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-2)', maxWidth: 560 }}>
        No language rules yet. On the <strong>Review</strong> page, tick a whole show or movie,
        pick its audio language, and check <em>“Remember for future downloads.”</em>
      </div>
    );
  }

  const pills = [
    { id: 'all', label: `All · ${counts.all}` },
    { id: 'show', label: `📺 Shows · ${counts.show}` },
    { id: 'movie', label: `🎬 Movies · ${counts.movie}` },
  ];

  return (
    <div style={{ maxWidth: 820, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
        {pills.map((p) => (
          <span key={p.id} role="button" tabIndex={0}
            onClick={() => setTypeFilter(p.id)}
            onKeyDown={(e) => { if (e.key === 'Enter') setTypeFilter(p.id); }}
            className={`chip ${typeFilter === p.id ? 'violet' : ''}`}
            style={{ cursor: 'pointer' }}>
            {p.label}
          </span>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 8, minHeight: 0 }}>
        <div ref={listRef} style={{ flex: 1, overflowY: 'auto', maxHeight: '62vh', paddingRight: 8 }}>
          {groups.map((g) => (
            <div key={g.letter} data-letter={g.letter}>
              <div style={{
                fontSize: 'var(--text-2xs)', color: 'var(--fg-3)',
                textTransform: 'uppercase', letterSpacing: '0.1em',
                padding: '8px 0 4px', position: 'sticky', top: 0,
                background: 'var(--bg-0)',
              }}>{g.letter}</div>
              {g.rules.map((r) => (
                <div key={r.series_prefix} style={{
                  display: 'flex', alignItems: 'center', gap: 11,
                  padding: '9px 12px', marginBottom: 6,
                  background: 'var(--bg-1)', border: 'var(--border)',
                  borderRadius: 'var(--radius-md)',
                }}>
                  <span style={{ width: 18, textAlign: 'center', flex: 'none' }}>
                    {r.media_type === 'movie' ? '🎬' : '📺'}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 'var(--text-sm)' }}>{r.title}</div>
                    <div className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--fg-3)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {r.series_prefix} · {r.covered_count} {r.media_type === 'movie' ? 'file' : 'eps'}{(r.covered_count === 1 && r.media_type !== 'movie') ? '' : ''}
                    </div>
                  </div>
                  <LangTag value={r.lang_code} size={13} />
                  <button className="btn ghost" onClick={() => revoke(r.series_prefix)}
                    style={{ color: 'var(--error-500)', flex: 'none' }}>
                    Revoke
                  </button>
                </div>
              ))}
            </div>
          ))}
        </div>
        <div style={{ flex: 'none', width: 20, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1, paddingTop: 2 }}>
          {Array.from({ length: 26 }, (_, i) => String.fromCharCode(65 + i)).map((L) => (
            <span key={L}
              role={active.has(L) ? 'button' : undefined}
              onClick={active.has(L) ? () => jumpTo(L) : undefined}
              style={{
                fontSize: 10, lineHeight: 1.25,
                color: active.has(L) ? 'var(--violet-500)' : 'var(--fg-3)',
                fontWeight: active.has(L) ? 600 : 400,
                cursor: active.has(L) ? 'pointer' : 'default',
              }}>{L}</span>
          ))}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Build the frontend**

Run: `npm run build:frontend`
Expected: completes; `settings.bundle.js` rebuilt.

- [ ] **Step 6: Deploy and verify**

```bash
wsl -e bash -lc "docker restart subarr-next"
```

Manual check on :9923 — open **Settings → Language rules** (or `/settings#lang-rules`). With no rules, the empty-state shows the Review instructions. To verify a populated list end-to-end, seed one via the API, then reload the panel:

```bash
curl -s -X PUT http://localhost:9923/api/audio-lang/series-intent \
  -H 'Content-Type: application/json' \
  -d '{"series_prefix":"TV/Cheers/","lang_code":"eng"}'
curl -s http://localhost:9923/api/audio-lang/series-intent
```
Expected: the panel lists "Cheers" under "C" with an English flag chip and a Revoke button; clicking Revoke removes it. Clean up the seeded rule afterward with the Revoke button or:
```bash
curl -s -X DELETE "http://localhost:9923/api/audio-lang/series-intent?series_prefix=TV/Cheers/"
```

- [ ] **Step 7: Commit**

```bash
git add src/subarr/static/v1/home-hifi/settings.jsx src/subarr/static/v1/home-hifi/settings.bundle.js src/subarr/static/v1/home-hifi/settings.bundle.js.map
git commit -m "feat(settings): Language rules panel (view/revoke series-intent)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Full verification + frontend bundle-drift gate + finish

**Files:** none (verification only)

- [ ] **Step 1: Run the full Python suite**

Run: `PYTHONPATH=C:/Projects/subarr/src python -m pytest -q`
Expected: all pass (no regressions). If unrelated pre-existing failures appear, note them; do not "fix" them as part of this work.

- [ ] **Step 2: Run the frontend util node test**

Run: `node tests/frontend/lang-rules-util.test.mjs`
Expected: `lang-rules-util: all assertions passed`.

- [ ] **Step 3: Confirm bundles are in sync (the repo's drift gate)**

Run: `npm run check:frontend`
Expected: exits 0 (rebuild produces no diff in `*.bundle.js` / `*.html` — i.e., committed bundles match source). If it fails, the build wasn't committed; rebuild, `git add` the bundle(s), and amend the relevant commit.

- [ ] **Step 4: End-to-end smoke on the live app**

```bash
wsl -e bash -lc "docker restart subarr-next"
curl -s http://localhost:9923/api/audio-lang/series-intent
curl -s http://localhost:9923/openapi.json | python -c "import sys,json;p=json.load(sys.stdin)['paths'];print([m.upper()+' '+k for k in p for m in p[k] if 'series-intent' in k])"
```
Expected: GET returns `{"items":[...]}`; openapi lists exactly `PUT/GET/DELETE /api/audio-lang/series-intent` (single prefix, no double).

- [ ] **Step 5: Push the branch and open a PR**

```bash
git push -u origin feat/series-intent-ui
gh pr create --fill --title "Series-intent UI: declare from Review, manage in Settings (#226 follow-up)"
```

Include in the PR body: the route-fix rationale, the coverage-only design decision, and that movies are covered via folder-prefix rules. Reference issue #226.

---

## Self-Review Notes (author)

- **Spec coverage:** route fix (Task 1) ✓; PUT/DELETE refresh (Tasks 2–3) ✓; GET enrichment incl. best-effort covered_count + media_type (Task 4) ✓; Review checkbox + per-prefix PUT, generalized label (Task 6) ✓; Settings list with flags/movies/alphabetical/internal-scroll/A–Z ladder/revoke/empty-state (Task 7) ✓; drop bulk-for-series (never referenced) ✓; no Sonarr propagation for intent (intent PUT writes rule only) ✓; no proactive-declare form (panel is view/revoke) ✓; tests per spec (Tasks 2–5, 8) ✓.
- **Decision refined during planning:** `media_type` is derived in the **backend GET** (from the coverage snapshot), not the frontend — more robust than sniffing the prefix's first segment, since the media-root folder name isn't guaranteed to be literally `Movies`. The spec's frontend-derivation note for media_type is superseded by this; title is still derived client-side via `deriveTitle`.
- **Type consistency:** util exports (`distinctSeriesPrefixes`, `deriveTitle`, `ladderLetterFor`, `groupRulesAlphabetically`, `activeLadderLetters`) match their imports in review.jsx/settings.jsx and the node test. Backend keys (`series_prefix`, `lang_code`, `covered_count`, `media_type`) match between Task 4 and the Task 7 component.
