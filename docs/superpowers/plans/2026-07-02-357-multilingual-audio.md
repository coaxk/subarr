# #357 Multilingual + Non-Linguistic (zxx) Audio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop forcing a single audio-language pick per file: recognise confident *multilingual* files (e.g. *The Beasts*, gl+es+fr) and *non-linguistic* (`zxx`, e.g. *Junk Head*) files, represent them correctly, and suppress the false `⚠ suspect` alarm + the wrong `audio_language_override` for both.

**Architecture:** Detection uses the per-chunk `probability` values subgen's `/detect_language_robust` already emits (post-#396). A pure classifier maps chunks → `high_conf_langs` (languages with ≥1 chunk `probability ≥ T`); `≥2 → confident multilingual`, `==1 → single`, `==0 → confused` (today's tag-fallback path, unchanged). The verdict is persisted additively on the existing `audio_lang_verifications` table via two new columns (`lang_class`, `lang_codes`) — the singular `lang_code` stays populated so every existing single-language consumer keeps working. Coverage/UI gain a `multilingual` state (`🌐 gl·es·fr`) and a `zxx` label; the override-forward step skips multi/`zxx`.

**Tech Stack:** Python 3.11, SQLite (raw `sqlite3` + numbered `.sql` migrations), FastAPI, pytest / pytest-asyncio (strict mode), vanilla JSX bundles (esbuild) + vitest 4 for frontend tests.

---

## Context the implementer needs (read before starting)

**subarr test invocation & gotchas (baked into every task below):**

- Run a single Python test file: `python -m pytest tests/<file>.py -q`. Run one test: `python -m pytest tests/<file>.py::<test> -q`.
- **pytest-asyncio is STRICT mode.** Every `async def test_*` needs `@pytest.mark.asyncio` (or a module-level `pytestmark = pytest.mark.asyncio`). A plain `async def test` without the marker is silently *not run* (reported as skipped/warning) — never assume green from an un-marked async test.
- **conftest module-reload gotcha:** `tests/conftest.py` reloads `subarr.config`, `subarr.coverage_engine`, `subarr.paths`, integration modules, etc. per-test (see `subarr_env`, `anime_stack`). Because a class/function object gets a *new identity* after reload, **import the symbol you assert on INSIDE the test body**, not at module top. (Reference: `reference_subarr-test-module-reload`.) Store-only tests that never trigger `subarr_env` can import at top, but when in doubt import locally.
- **ruff PostToolUse hook strips just-added unused top-level imports.** Add an import and its first usage in the same edit, or import function-locally. Always run `ruff format <file>` on any test appended via heredoc before committing (heredoc bypasses the hook → CI `ruff format --check` fails otherwise).
- **`Settings` is `@dataclass(frozen=True)`** (`src/subarr/config.py:51`). In tests toggle a field with `object.__setattr__(settings, "field", value)`.
- **Commit messages:** no apostrophes or special characters; footer line `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Do NOT push or open the PR — the controller does that after the final task.
- **Frontend tests:** `npm run test:frontend` (= `vitest run`), config at `vitest.config.mjs`, tests live in `src/subarr/static/v1/home-hifi/__tests__/*.test.js`. Rebuild bundles with `npm run build:frontend` after editing a `.jsx` (the served artifact is the `.bundle.js`, not the `.jsx`).

**Feasibility already sighted (still verify in Task 0):** `src/subarr/static/v1/home-hifi/coverage.jsx:1901` already renders `p={(c.probability || 0).toFixed(2)}` per chunk, i.e. the frontend already consumes a per-chunk `probability` from the `/detect_language_robust` response. That is strong evidence the field exists post-#396, but Task 0 confirms it against a real/fixture response before any classifier work.

**Existing `resolve_source_language` contract (do not break):** `src/subarr/arena_service.py:49` returns a **4-tuple** `(language|None, source|None, mixed: bool, mislabel: bool)`. Two callers unpack exactly that arity:
- `src/subarr/arena_service.py:386` — `final, src, mixed, mislabel = resolve_source_language(det, tag, run.source_language, multitrack=multitrack)`
- `src/subarr/audio_audit.py:261` — `lang, _src, mixed, mislabel = resolve_source_language(detect, tag_lang, None, multitrack=multitrack)`

This plan keeps the 4-tuple arity. The multilingual set is carried by a *separate* pure classifier (`classify_high_conf_langs`) and surfaced through the `detect` dict, so no caller signature changes. The auto-record path (Task 7) consumes the classifier directly.

---

## File Structure

| File | Create/Modify | Responsibility (one line) |
|------|---------------|---------------------------|
| `src/subarr/arena.py` | Modify (`parse_robust_detect` ~:65) | Capture per-chunk `probability` into a new `chunks_conf` list on the parsed shape; unchanged when the field is absent. |
| `src/subarr/multilang.py` | Create | Pure classifier `classify_high_conf_langs(chunks_conf, threshold)` → ordered `list[str]` of high-confidence languages. |
| `src/subarr/config.py` | Modify (`Settings` + `load()`) | Add `multilang_chunk_min_prob: float` (env `SUBARR_MULTILANG_CHUNK_MIN_PROB`, default `0.5`). |
| `src/subarr/arena_service.py` | Modify (`resolve_source_language` ~:49) | Branch on confident-multilingual (≥2 high-conf langs) AHEAD of unanimous/bilingual/confused; return plurality in the language slot + set `mixed=True`; expose the set via `detect["multilingual_langs"]`. |
| `src/subarr/migrations/027_audio_lang_multilingual.sql` | Create | Add `lang_class TEXT NOT NULL DEFAULT 'single'` + `lang_codes TEXT` to `audio_lang_verifications`. |
| `src/subarr/audio_lang_store.py` | Modify (`AudioLangVerification`, `upsert`, `get`, `list_all`) | Persist/read `lang_class` + `lang_codes`; singular `lang_code` always populated (multi = first-of-set). |
| `src/subarr/coverage_engine.py` | Modify (`_classify_audio_label` ~:940) | A `lang_class='multi'` / `source='auto-high-conf-multi'` file surfaces as `multilingual`, never `audio_label_suspect`. |
| `src/subarr/audio_lang_store.py` | Modify (`resolve_audio_language_override` ~:432) | Return no override when `lang_class='multi'` or `lang_code='zxx'`. |
| `src/subarr/langs.py` | Modify (`WHISPER_LANGUAGES` ~:127) | Add `"zxx": "No linguistic content"` so `/api/languages` offers it. |
| `src/subarr/static/v1/home-hifi/coverage.jsx` | Modify (`AudioLabelChip` ~:647) | Render `🌐 gl·es·fr` for multilingual, a distinct label for `zxx`, instead of `⚠ suspect`. |
| `src/subarr/static/v1/home-hifi/review.jsx` | Modify (bulk picker ~:923) | Multi-select correction writing `lang_class='multi'`+`lang_codes`; glance-lane filter for `source='auto-high-conf-multi'`. |
| `tests/test_parse_robust_detect_conf.py` | Create | Parser captures `chunks_conf`; absent field → graceful. |
| `tests/test_multilang_classifier.py` | Create | Classifier unit tests (The Beasts / confused / noise / unanimous). |
| `tests/test_config_multilang.py` | Create | Env-parse of `multilang_chunk_min_prob`. |
| `tests/test_resolve_source_language_multi.py` | Create | Multilingual branch + regression on existing 4-tuple callers. |
| `tests/test_migration_027_multilingual.py` | Create | Migration applies to an existing DB, rows default `'single'`. |
| `tests/test_audio_lang_store_multi.py` | Create | Store round-trip of `lang_class`/`lang_codes`; auto-record `source='auto-high-conf-multi'`. |
| `tests/test_coverage_multilingual.py` | Create | Coverage row surfaces `multilingual`, not `suspect`. |
| `tests/test_override_suppression_multi_zxx.py` | Create | Override suppressed for multi + `zxx`. |
| `tests/test_zxx_language.py` | Create | `zxx` in `/api/languages`; storing `zxx` suppresses suspect + skips override. |
| `src/subarr/static/v1/home-hifi/__tests__/coverage-multilingual-badge.test.js` | Create | Vitest: badge classifier picks `multilingual`/`zxx` states. |

---

## Task 0: Feasibility gate — confirm per-chunk `probability` exists (NOT deferrable)

**Files:**
- Investigate only (no code change): `src/subarr/subgen_client.py`, `src/subarr/static/v1/home-hifi/coverage.jsx:1901`, `tests/test_audio_audit.py`

- [ ] **Step 1: Inspect the response shape subarr already consumes**

Run these three read-only checks and record the outcome:

```bash
# (a) The frontend already renders a per-chunk probability — confirm the line exists.
python -m pytest --collect-only -q >/dev/null 2>&1  # no-op sanity that env imports
grep -n "c.probability" src/subarr/static/v1/home-hifi/coverage.jsx
# (b) The client passes the raw JSON straight through (no field stripping).
grep -n "detect_language_robust" src/subarr/subgen_client.py
# (c) Existing tests show the chunk shape used across the suite.
grep -n '"chunks"' tests/test_audio_audit.py
```

- [ ] **Step 2: Confirm against a real or captured response**

Preferred: capture a live response from the connected subgen (dev box has subgen at `SUBGEN_URL`). If subgen is reachable:

```bash
curl -s -X POST "$SUBGEN_URL/detect_language_robust?path=<some_media_path>&chunks=3" | python -m json.tool | head -40
```

Look for `chunks[i].probability` (a float 0.0–1.0) alongside `chunks[i].language`.

If subgen is NOT reachable in this environment, fall back to the source of truth the repo integrates with: the subgen `/detect_language_robust` handler (in the `coaxk/subarr-subgen` patch stack / the running `subgen-next` image). Grep that source for the per-chunk dict construction and confirm a `probability` key is emitted per chunk. The frontend's existing `c.probability` read (Step 1a) is corroborating evidence that a prior subgen version already returned it.

- [ ] **Step 3: Decide the branch**

- **PASS criterion:** at least one authoritative source (live response, captured fixture, or the subgen handler source) shows a per-chunk `probability` (or an equivalent per-chunk confidence float) in the `/detect_language_robust` `chunks[]`. → **Proceed to Task 1.**
- **FAIL criterion:** no per-chunk confidence is emitted anywhere. → **STOP.** Do not implement Tasks 1–12. Record in the plan's status / hand back to the controller: *"Subgen-side prerequisite: `/detect_language_robust` must emit a per-chunk `probability`. This lands in the `coaxk/subarr-subgen` stack + a subgen image bump before #357 can proceed."* The parser change (Task 1) is still safe to land (it degrades gracefully), but the classifier cannot fire until the field exists.

- [ ] **Step 4: Record the finding (no commit)**

Write one line into the task's PR/branch notes stating which source confirmed the field and the exact key name (`probability`). No code is committed in Task 0.

---

## Task 1: Parser captures per-chunk probability

**Files:**
- Modify: `src/subarr/arena.py:65-99` (`parse_robust_detect`)
- Test: `tests/test_parse_robust_detect_conf.py`

- [ ] **Step 1: Write the failing test**

```python
"""#357 — parse_robust_detect additively captures per-chunk probability."""

from __future__ import annotations

from subarr.arena import parse_robust_detect


def test_captures_per_chunk_probability_as_chunks_conf():
    resp = {
        "aggregate": {"language": "gl", "n_agreeing": 1, "n_total": 3},
        "chunks": [
            {"language": "gl", "probability": 0.91},
            {"language": "es", "probability": 0.88},
            {"language": "fr", "probability": 0.76},
        ],
    }
    out = parse_robust_detect(resp)
    assert out is not None
    # additive: existing keys untouched
    assert out["languages_heard"] == ["es", "fr", "gl"]
    assert out["n_total"] == 3
    # new: ordered (lang, prob) per chunk, preserving chunk order
    assert out["chunks_conf"] == [("gl", 0.91), ("es", 0.88), ("fr", 0.76)]


def test_absent_probability_degrades_to_none_confidence():
    resp = {
        "aggregate": {"language": "nl", "n_agreeing": 3, "n_total": 3},
        "chunks": [{"language": "nl"}, {"language": "nl"}, {"language": "nl"}],
    }
    out = parse_robust_detect(resp)
    assert out is not None
    # graceful: probability missing -> None, no crash, existing behaviour intact
    assert out["chunks_conf"] == [("nl", None), ("nl", None), ("nl", None)]
    assert out["unanimous"] is True


def test_no_chunks_still_returns_none():
    assert parse_robust_detect({"aggregate": {"n_total": 0}, "chunks": []}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_parse_robust_detect_conf.py -q`
Expected: FAIL — `KeyError: 'chunks_conf'` (the key does not exist yet).

- [ ] **Step 3: Write minimal implementation**

In `src/subarr/arena.py`, edit `parse_robust_detect` to build `chunks_conf` and add it to the returned dict. Replace the `heard = ...` block through the `return {...}` with:

```python
    raw_chunks = resp.get("chunks") or []
    heard = sorted(
        {
            c.get("language")
            for c in raw_chunks
            if c.get("language") and c.get("language") != "und"
        }
    )
    # #357: capture each chunk's (language, probability) in file order. The
    # per-chunk probability (real post-#396) is the signal that separates a
    # confident multilingual file (3 high-conf distinct langs) from a genuinely
    # confused one (3 low-conf guesses). Absent field -> None (graceful; the
    # classifier treats None as below-threshold and the legacy vote path runs).
    chunks_conf: list[tuple[str | None, float | None]] = []
    for c in raw_chunks:
        lang = c.get("language")
        prob = c.get("probability")
        chunks_conf.append((lang, float(prob) if prob is not None else None))
    if not n_tot:
        return None
    return {
        "language": lang if (lang and lang != "und") else None,
        "n_agreeing": n_ag,
        "n_total": n_tot,
        "unanimous": n_tot >= 2 and n_ag == n_tot,
        "languages_heard": heard,
        "chunks_conf": chunks_conf,
    }
```

Note: `lang` is already bound above from `agg.get("language")`; the loop uses its own local `lang` inside the comprehension body but reassigns the module-level `lang` — rename the loop variable to `c_lang` to avoid clobbering the aggregate `lang`:

```python
    chunks_conf: list[tuple[str | None, float | None]] = []
    for c in raw_chunks:
        c_lang = c.get("language")
        prob = c.get("probability")
        chunks_conf.append((c_lang, float(prob) if prob is not None else None))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_parse_robust_detect_conf.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Guard the existing parser tests still pass**

Run: `python -m pytest tests/test_audio_audit.py -q`
Expected: PASS (the `chunks_conf` addition is additive; `test_audio_audit.py` asserts `languages_heard`/`n_total`, unaffected).

- [ ] **Step 6: Commit**

```bash
ruff format src/subarr/arena.py tests/test_parse_robust_detect_conf.py
git add src/subarr/arena.py tests/test_parse_robust_detect_conf.py
git commit -m "#357 parse_robust_detect captures per-chunk probability as chunks_conf

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Pure multilingual classifier

**Files:**
- Create: `src/subarr/multilang.py`
- Test: `tests/test_multilang_classifier.py`

- [ ] **Step 1: Write the failing test**

```python
"""#357 — classify_high_conf_langs: languages with >=1 chunk prob >= T.

>=2 distinct high-conf langs => confident multilingual (returns them, ordered).
==1 => single (noise chunks in other langs ignored).
==0 => confused (returns []) -> caller falls back to today's tag path.
"""

from __future__ import annotations

from subarr.multilang import classify_high_conf_langs


def test_the_beasts_three_high_conf_distinct_is_multilingual():
    chunks = [("gl", 0.91), ("es", 0.88), ("fr", 0.76)]
    assert classify_high_conf_langs(chunks, 0.5) == ["gl", "es", "fr"]


def test_three_low_conf_distinct_is_confused_empty():
    chunks = [("gl", 0.20), ("es", 0.18), ("fr", 0.11)]
    assert classify_high_conf_langs(chunks, 0.5) == []


def test_one_high_conf_plus_two_low_noise_is_single():
    chunks = [("de", 0.95), ("en", 0.12), ("nn", 0.09)]
    assert classify_high_conf_langs(chunks, 0.5) == ["de"]


def test_two_high_conf_same_lang_is_single():
    chunks = [("ja", 0.9), ("ja", 0.8), ("ja", 0.7)]
    assert classify_high_conf_langs(chunks, 0.5) == ["ja"]


def test_none_probabilities_treated_as_below_threshold():
    # absent per-chunk probability (pre-#396 subgen) -> no high-conf langs.
    chunks = [("gl", None), ("es", None), ("fr", None)]
    assert classify_high_conf_langs(chunks, 0.5) == []


def test_ordering_is_first_high_conf_appearance():
    chunks = [("es", 0.6), ("gl", 0.9), ("es", 0.95), ("fr", 0.55)]
    assert classify_high_conf_langs(chunks, 0.5) == ["es", "gl", "fr"]


def test_ignores_und_language():
    chunks = [("und", 0.99), ("gl", 0.9), ("es", 0.8)]
    assert classify_high_conf_langs(chunks, 0.5) == ["gl", "es"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_multilang_classifier.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'subarr.multilang'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/subarr/multilang.py`:

```python
"""#357 — confident-multilingual classifier over per-chunk detection.

`parse_robust_detect` (arena.py) now surfaces `chunks_conf`: an ordered list of
(language, probability) per Whisper chunk. This module turns that into the set of
languages that are HIGH-CONFIDENCE (>=1 chunk with probability >= T), which is
what separates a genuinely multilingual file (The Beasts: 3 confident, distinct
langs) from a confused one (3 low-confidence guesses on music/silence).

Rule (design #357):
  high_conf_langs = [lang : exists a chunk (lang, p) with p is not None and p >= T]
  len >= 2  -> confident multilingual (the returned list, first-appearance order)
  len == 1  -> single language (low-conf noise chunks in other langs are ignored)
  len == 0  -> confused (empty list) -> caller falls back to the tag (unchanged)
"""

from __future__ import annotations


def classify_high_conf_langs(
    chunks_conf: list[tuple[str | None, float | None]],
    threshold: float,
) -> list[str]:
    """Return the ordered, de-duplicated list of languages that have at least
    one chunk whose probability >= threshold. 'und' and None-language chunks are
    ignored. A None probability is treated as below threshold (pre-#396 subgen)."""
    out: list[str] = []
    seen: set[str] = set()
    for lang, prob in chunks_conf or []:
        if not lang or lang == "und":
            continue
        if prob is None or prob < threshold:
            continue
        if lang in seen:
            continue
        seen.add(lang)
        out.append(lang)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_multilang_classifier.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
ruff format src/subarr/multilang.py tests/test_multilang_classifier.py
git add src/subarr/multilang.py tests/test_multilang_classifier.py
git commit -m "#357 add classify_high_conf_langs multilingual classifier

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Config knob `SUBARR_MULTILANG_CHUNK_MIN_PROB`

**Files:**
- Modify: `src/subarr/config.py` (`Settings` dataclass ~:127 area, `load()` ~:281 area)
- Test: `tests/test_config_multilang.py`

- [ ] **Step 1: Write the failing test**

```python
"""#357 — SUBARR_MULTILANG_CHUNK_MIN_PROB config knob (float default 0.5)."""

from __future__ import annotations

import importlib


def test_default_is_half(monkeypatch):
    monkeypatch.delenv("SUBARR_MULTILANG_CHUNK_MIN_PROB", raising=False)
    from subarr import config

    importlib.reload(config)
    assert config.settings.multilang_chunk_min_prob == 0.5


def test_env_override(monkeypatch):
    monkeypatch.setenv("SUBARR_MULTILANG_CHUNK_MIN_PROB", "0.7")
    from subarr import config

    importlib.reload(config)
    assert config.settings.multilang_chunk_min_prob == 0.7


def test_blank_falls_back_to_default(monkeypatch):
    # _env_or treats empty/whitespace as missing (a commented-out .env line).
    monkeypatch.setenv("SUBARR_MULTILANG_CHUNK_MIN_PROB", "  ")
    from subarr import config

    importlib.reload(config)
    assert config.settings.multilang_chunk_min_prob == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_multilang.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'multilang_chunk_min_prob'`.

- [ ] **Step 3: Write minimal implementation**

In `src/subarr/config.py`, add the field to the `Settings` dataclass. Place it directly after the `coverage_refresh_min_interval_s: float` field (near line 127) so related float knobs stay grouped:

```python
    # #357: chunk-probability threshold T for the confident-multilingual rule.
    # A language counts as high-confidence when >=1 detection chunk reports
    # probability >= T; >=2 such languages => the file is multilingual (The
    # Beasts). Default 0.5 shipped as an env-overridable placeholder — empirical
    # tuning is deferred until per-chunk probabilities have accrued from normal
    # detection. Override with SUBARR_MULTILANG_CHUNK_MIN_PROB.
    multilang_chunk_min_prob: float
```

Then in `load()`, add the parse. Place it directly after the `coverage_refresh_min_interval_s=...` block (near line 283):

```python
        # #357: T default 0.5. Clamped to [0.0, 1.0] since it is a probability.
        multilang_chunk_min_prob=min(
            1.0, max(0.0, float(_env_or("SUBARR_MULTILANG_CHUNK_MIN_PROB", "0.5")))
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_multilang.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Guard the config drift test**

Run: `python -m pytest tests/test_config.py -q` (if present) and the full config-touching set:
Run: `python -m pytest tests/ -q -k config`
Expected: PASS. (The new field is required-positional in the frozen dataclass but `load()` supplies it, so no `TypeError`. If a test constructs `Settings(...)` directly it will need the new arg — search `Settings(` in tests; there are none that build it by hand, `load()` is the only constructor.)

- [ ] **Step 6: Commit**

```bash
ruff format src/subarr/config.py tests/test_config_multilang.py
git add src/subarr/config.py tests/test_config_multilang.py
git commit -m "#357 add SUBARR_MULTILANG_CHUNK_MIN_PROB config knob (float default 0.5)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: audit-walker multilingual classification (REVISED during execution)

> **⚠️ REVISED 2026-07-02 (supersedes the original Task 4 below).** Investigation during execution found the original plan targeted a dormant seam: nothing passes `min_prob` to `resolve_source_language`, and the real production engine that flags *The Beasts* `suspect / 1/3 agree` is the **audit walker** (`audio_audit._audit_one` → `_derive_status`), wired at `app.py:594`. Revised approach:
> - Add a `multilingual` bucket to `_derive_status(detect, mixed, mislabel, multitrack, multilingual_langs=None)` — returns `"multilingual"` when `len(multilingual_langs) >= 2`, placed AFTER `mixed` (bilingual) and BEFORE the `undetermined`/`confused` checks so a would-be-confused high-confidence split (The Beasts) is caught, without regressing bilingual (Besa).
> - In `_audit_one`, compute `multilingual_langs = classify_high_conf_langs(detect["chunks_conf"], settings.multilang_chunk_min_prob)` and pass it to `_derive_status`.
> - `resolve_source_language` is left UNCHANGED (no `min_prob` seam). Existing `_split([...])` fixtures carry no per-chunk probability → classify returns `[]` → no regression.
> - Files: `src/subarr/audio_audit.py` (`_derive_status` ~:86, `_audit_one` ~:254). Test: `tests/test_audio_audit_multilingual.py`.

<details><summary>Original Task 4 (superseded — do not implement)</summary>

**Files:**
- Modify: `src/subarr/arena_service.py:49-86` (`resolve_source_language`)
- Test: `tests/test_resolve_source_language_multi.py`

**Contract decision (locked):** `resolve_source_language` keeps its 4-tuple return `(language|None, source|None, mixed, mislabel)`. It gains one keyword-only param `min_prob: float | None = None`. When `min_prob` is provided AND the parsed `detect` has a `chunks_conf` yielding `>= 2` high-conf langs, the function:
1. returns `language = high_conf_langs[0]` (plurality/first-of-set — keeps the singular consumer working), `source = "whisper-multi"`, `mixed = True`, `mislabel = False`;
2. mutates `detect["multilingual_langs"] = high_conf_langs` so the auto-record caller (Task 7) can read the full ordered set off the same dict.

Existing callers pass no `min_prob`, so their behaviour is byte-for-byte unchanged.

- [ ] **Step 1: Write the failing test**

```python
"""#357 — resolve_source_language returns a confident-multilingual verdict
AHEAD of the unanimous/bilingual/confused path, without breaking the 4-tuple
callers pass min_prob=None (or omit it)."""

from __future__ import annotations

from subarr.arena_service import resolve_source_language


def _detect(chunks_conf, heard, unanimous=False, n_ag=1, lang=None):
    return {
        "language": lang,
        "n_agreeing": n_ag,
        "n_total": len(chunks_conf),
        "unanimous": unanimous,
        "languages_heard": heard,
        "chunks_conf": chunks_conf,
    }


def test_confident_multilingual_fires_ahead_of_confused():
    det = _detect(
        [("gl", 0.91), ("es", 0.88), ("fr", 0.76)],
        heard=["es", "fr", "gl"],
        lang="gl",
    )
    lang, src, mixed, mislabel = resolve_source_language(det, tag="gl", submit=None, min_prob=0.5)
    assert lang == "gl"  # first-of-set plurality; singular consumers keep working
    assert src == "whisper-multi"
    assert mixed is True
    assert mislabel is False
    # the full ordered set is exposed on the detect dict for the auto-record path
    assert det["multilingual_langs"] == ["gl", "es", "fr"]


def test_single_high_conf_is_not_multilingual():
    det = _detect([("de", 0.95), ("en", 0.10), ("nn", 0.08)], heard=["de", "en", "nn"], lang="de")
    lang, src, mixed, mislabel = resolve_source_language(det, tag="de", submit=None, min_prob=0.5)
    assert src != "whisper-multi"
    assert "multilingual_langs" not in det


def test_confused_low_conf_falls_back_to_tag():
    det = _detect([("gl", 0.2), ("es", 0.18), ("fr", 0.11)], heard=["es", "fr", "gl"], lang=None)
    lang, src, mixed, mislabel = resolve_source_language(det, tag="es", submit=None, min_prob=0.5)
    assert (lang, src) == ("es", "tagged")


def test_submit_still_wins_over_multilingual():
    det = _detect([("gl", 0.9), ("es", 0.9), ("fr", 0.9)], heard=["es", "fr", "gl"], lang="gl")
    lang, src, mixed, mislabel = resolve_source_language(det, tag="gl", submit="ja", min_prob=0.5)
    assert (lang, src) == ("ja", "user")


def test_existing_callers_unchanged_without_min_prob():
    # The Ring regression: unanimous Dutch overrides a Danish tag; 4-tuple intact.
    det = _detect([("nl", 0.9), ("nl", 0.9), ("nl", 0.9)], heard=["nl"], unanimous=True, n_ag=3, lang="nl")
    lang, src, mixed, mislabel = resolve_source_language(det, tag="da", submit=None)
    assert (lang, src, mixed, mislabel) == ("nl", "whisper", False, True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_resolve_source_language_multi.py -q`
Expected: FAIL — `TypeError: resolve_source_language() got an unexpected keyword argument 'min_prob'`.

- [ ] **Step 3: Write minimal implementation**

In `src/subarr/arena_service.py`, change the signature and add the branch at the very top of the body (after `det = detect or {}` and the derived vars, but before the `if submit:` line). New signature:

```python
def resolve_source_language(detect, tag, submit, multitrack=False, *, min_prob=None):
```

Add near the top of the function, right after the `mixed = ...` line and before `if submit:`:

```python
    # #357: confident-multilingual fires AHEAD of the unanimous/bilingual/
    # confused path. Only when the caller opts in (min_prob is not None) AND the
    # per-chunk confidences show >=2 distinct high-conf languages do we treat the
    # disagreement as the ANSWER (The Beasts) rather than confusion. The full set
    # is stashed on the detect dict for the auto-record caller; the returned
    # singular language is the first-of-set so every single-language consumer
    # keeps working unchanged.
    if min_prob is not None and not submit:
        from .multilang import classify_high_conf_langs

        high = classify_high_conf_langs(det.get("chunks_conf") or [], min_prob)
        if len(high) >= 2:
            det["multilingual_langs"] = high
            return high[0], "whisper-multi", True, False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_resolve_source_language_multi.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Regression — both existing callers stay green**

Run: `python -m pytest tests/test_arena.py tests/test_audio_audit.py -q`
Expected: PASS. (Neither caller passes `min_prob`, so the new branch is dormant for them.)

- [ ] **Step 6: Commit**

```bash
ruff format src/subarr/arena_service.py tests/test_resolve_source_language_multi.py
git add src/subarr/arena_service.py tests/test_resolve_source_language_multi.py
git commit -m "#357 resolve_source_language confident-multilingual branch behind min_prob opt-in

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

</details>

---

## Task 5: Migration 027 — additive columns

**Files:**
- Create: `src/subarr/migrations/027_audio_lang_multilingual.sql`
- Test: `tests/test_migration_027_multilingual.py`

**Migration number confirmed:** highest existing is `026_pending_queue_radarr_movie_id.sql`; new file is `027`.

- [ ] **Step 1: Write the failing test**

```python
"""#357 — migration 027 adds lang_class/lang_codes to audio_lang_verifications,
applies cleanly to an existing DB, and defaults existing rows to 'single'."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from subarr.migrate import MigrationRunner


def _migrations_dir() -> Path:
    import subarr.migrate as m

    return Path(m.__file__).parent / "migrations"


def test_migration_027_adds_columns_and_defaults_single(tmp_path):
    db = tmp_path / "subarr.db"
    runner = MigrationRunner(db, _migrations_dir())
    runner.run()  # applies 001..027

    conn = sqlite3.connect(str(db))
    # seed a pre-existing row via the base columns only
    conn.execute(
        "INSERT INTO audio_lang_verifications "
        "(canonical_path, lang_code, source, confidence, verified_at) "
        "VALUES ('TV/Old/ep.mkv', 'ja', 'user', 1.0, 1.0)"
    )
    conn.commit()

    cols = {r[1] for r in conn.execute("PRAGMA table_info(audio_lang_verifications)")}
    assert "lang_class" in cols
    assert "lang_codes" in cols

    row = conn.execute(
        "SELECT lang_class, lang_codes FROM audio_lang_verifications WHERE canonical_path='TV/Old/ep.mkv'"
    ).fetchone()
    assert row[0] == "single"  # NOT NULL DEFAULT
    assert row[1] is None
    conn.close()


def test_migration_027_is_idempotent_rerun(tmp_path):
    db = tmp_path / "subarr.db"
    MigrationRunner(db, _migrations_dir()).run()
    # second run is a no-op (version already recorded)
    applied = MigrationRunner(db, _migrations_dir()).run()
    assert applied == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_migration_027_multilingual.py -q`
Expected: FAIL — `assert "lang_class" in cols` fails (columns absent; migration file does not exist yet).

- [ ] **Step 3: Write the migration**

Create `src/subarr/migrations/027_audio_lang_multilingual.sql` (match the header/comment style of `016`/`026`):

```sql
-- 027_audio_lang_multilingual.sql
--
-- #357 multilingual + zxx audio: represent files that legitimately break the
-- one-language-per-file assumption. Two additive columns on the existing
-- audio_lang_verifications table:
--
--   lang_class  'single' (default) | 'multi'. A confident-multilingual detection
--               (>=2 high-confidence chunk languages, e.g. The Beasts gl+es+fr)
--               is stored as 'multi'.
--   lang_codes  JSON array, populated ONLY when lang_class='multi' — the ordered
--               high-conf set, e.g. ["gl","es","fr"]. NULL for single files.
--
-- The singular lang_code column is ALWAYS populated (multi = first-of-set), so
-- every existing single-language consumer keeps working unchanged. No PK change,
-- no backfill: existing rows default to 'single' / NULL, which is correct.
--
-- ADD COLUMN is not idempotent; the migration runner (migrate.py _apply_one)
-- tolerates the "duplicate column name" error on a transitional DB.

ALTER TABLE audio_lang_verifications ADD COLUMN lang_class TEXT NOT NULL DEFAULT 'single';
ALTER TABLE audio_lang_verifications ADD COLUMN lang_codes TEXT;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_migration_027_multilingual.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Guard the migration suite**

Run: `python -m pytest tests/ -q -k migrat`
Expected: PASS (no other migration test regressed; `027` slots in after `026`).

- [ ] **Step 6: Commit**

```bash
git add src/subarr/migrations/027_audio_lang_multilingual.sql tests/test_migration_027_multilingual.py
git commit -m "#357 migration 027 adds lang_class + lang_codes to audio_lang_verifications

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Store reads/writes `lang_class` + `lang_codes`

**Files:**
- Modify: `src/subarr/audio_lang_store.py` — `AudioLangVerification` dataclass (~:51), `upsert` (~:84), `get` (~:115), `list_all` (~:182)
- Test: `tests/test_audio_lang_store_multi.py`

**Design:** `upsert` gains `lang_class: str = "single"` and `lang_codes: list[str] | None = None`. When `lang_class == "multi"`, `lang_codes` is JSON-serialised; the singular `lang_code` is set by the caller to the first-of-set. Reads deserialise `lang_codes` (malformed JSON → treat as single, i.e. `lang_codes=None`, per the spec error-handling rule).

- [ ] **Step 1: Write the failing test**

```python
"""#357 — AudioLangStore persists lang_class/lang_codes; singular lang_code
always populated; auto-record uses source='auto-high-conf-multi'; malformed
lang_codes JSON degrades to single."""

from __future__ import annotations

from pathlib import Path

from subarr.migrate import MigrationRunner


def _store(tmp_path: Path):
    # Apply migrations first so 027's columns exist, then open the store.
    db = tmp_path / "subarr.db"
    MigrationRunner(db, Path(_mig_dir())).run()
    from subarr.audio_lang_store import AudioLangStore

    return AudioLangStore(db)


def _mig_dir() -> str:
    import subarr.migrate as m

    return str(Path(m.__file__).parent / "migrations")


def test_single_roundtrip_defaults_class_single(tmp_path):
    from subarr.audio_lang_store import AudioLangStore  # reloaded-module safe re-import

    assert AudioLangStore is not None
    store = _store(tmp_path)
    store.upsert(canonical_path="TV/S/ep.mkv", lang_code="ja", source="user")
    v = store.get("TV/S/ep.mkv")
    assert v.lang_code == "ja"
    assert v.lang_class == "single"
    assert v.lang_codes is None


def test_multi_roundtrip_populates_singular_and_set(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        canonical_path="Movies/TheBeasts.mkv",
        lang_code="gl",  # first-of-set
        source="auto-high-conf-multi",
        confidence=0.9,
        lang_class="multi",
        lang_codes=["gl", "es", "fr"],
    )
    v = store.get("Movies/TheBeasts.mkv")
    assert v.lang_code == "gl"  # singular ALWAYS populated (consumers keep working)
    assert v.lang_class == "multi"
    assert v.lang_codes == ["gl", "es", "fr"]
    assert v.source == "auto-high-conf-multi"


def test_list_all_carries_multi_fields(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        canonical_path="Movies/TheBeasts.mkv",
        lang_code="gl",
        source="auto-high-conf-multi",
        lang_class="multi",
        lang_codes=["gl", "es", "fr"],
    )
    rows = store.list_all()
    assert rows[0].lang_class == "multi"
    assert rows[0].lang_codes == ["gl", "es", "fr"]


def test_malformed_lang_codes_json_degrades_to_single(tmp_path):
    store = _store(tmp_path)
    store.upsert(canonical_path="x.mkv", lang_code="gl", source="user")
    # corrupt the JSON directly
    store._conn.execute(
        "UPDATE audio_lang_verifications SET lang_class='multi', lang_codes='{not json' WHERE canonical_path='x.mkv'"
    )
    v = store.get("x.mkv")
    assert v.lang_codes is None  # malformed -> None, no crash
    assert v.lang_code == "gl"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_audio_lang_store_multi.py -q`
Expected: FAIL — `TypeError: upsert() got an unexpected keyword argument 'lang_class'` (and `AudioLangVerification` has no `lang_class` field).

- [ ] **Step 3: Write minimal implementation**

In `src/subarr/audio_lang_store.py`:

(a) Extend the dataclass (add two fields with defaults so existing constructions stay valid):

```python
@dataclass
class AudioLangVerification:
    canonical_path: str
    lang_code: str
    source: str
    confidence: float
    verified_at: float
    verified_by: str | None
    evidence: dict | None
    lang_class: str = "single"  # #357: 'single' | 'multi'
    lang_codes: list[str] | None = None  # #357: ordered set, only when multi

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "lang_code": self.lang_code,
            "source": self.source,
            "confidence": self.confidence,
            "verified_at": self.verified_at,
            "verified_by": self.verified_by,
            "evidence": self.evidence,
            "lang_class": self.lang_class,
            "lang_codes": self.lang_codes,
        }
```

(b) Extend `upsert` signature + INSERT:

```python
    def upsert(
        self,
        *,
        canonical_path: str,
        lang_code: str,
        source: str = "user",
        confidence: float = 1.0,
        verified_by: str | None = None,
        evidence: dict | None = None,
        lang_class: str = "single",
        lang_codes: list[str] | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audio_lang_verifications "
                "(canonical_path, lang_code, source, confidence, verified_at, verified_by, evidence, "
                " lang_class, lang_codes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_path) DO UPDATE SET "
                "  lang_code=excluded.lang_code, source=excluded.source, "
                "  confidence=excluded.confidence, verified_at=excluded.verified_at, "
                "  verified_by=excluded.verified_by, evidence=excluded.evidence, "
                "  lang_class=excluded.lang_class, lang_codes=excluded.lang_codes",
                (
                    canonical_path,
                    normalize_lang(lang_code) or lang_code.lower(),
                    source,
                    confidence,
                    time.time(),
                    verified_by,
                    json.dumps(evidence) if evidence else None,
                    lang_class,
                    json.dumps(lang_codes) if lang_codes else None,
                ),
            )
```

(c) A small read helper (module-level, above the class or right after imports) that decodes `lang_codes` defensively:

```python
def _decode_lang_codes(raw: str | None) -> list[str] | None:
    """#357: deserialise the lang_codes JSON array. Malformed -> None (treat as
    single), logged, never crash (design error-handling rule)."""
    if not raw:
        return None
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        logging.getLogger(__name__).warning("malformed lang_codes JSON; treating as single")
        return None
    return val if isinstance(val, list) else None
```

(d) Extend the SELECT + object build in `get` (both the direct-row branch and the returned `AudioLangVerification`). Change the SELECT column list to include the two new columns and pass them through:

```python
            row = self._conn.execute(
                "SELECT canonical_path, lang_code, source, confidence, "
                "       verified_at, verified_by, evidence, lang_class, lang_codes "
                "FROM audio_lang_verifications WHERE canonical_path = ?",
                (canonical_path,),
            ).fetchone()
        if row:
            return AudioLangVerification(
                canonical_path=row[0],
                lang_code=normalize_lang(row[1]) or row[1],
                source=row[2],
                confidence=row[3],
                verified_at=row[4],
                verified_by=row[5],
                evidence=json.loads(row[6]) if row[6] else None,
                lang_class=row[7] or "single",
                lang_codes=_decode_lang_codes(row[8]),
            )
```

(The series-intent fall-through `AudioLangVerification(...)` further down keeps the defaults `lang_class="single"`, `lang_codes=None` — inherited single-language semantics, no change needed there.)

(e) Extend `list_all` similarly — its SELECT and the per-row build:

```python
            rows = self._conn.execute(
                "SELECT canonical_path, lang_code, source, confidence, "
                "       verified_at, verified_by, evidence, lang_class, lang_codes "
                "FROM audio_lang_verifications "
                "ORDER BY verified_at DESC"
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                AudioLangVerification(
                    canonical_path=r[0],
                    lang_code=normalize_lang(r[1]) or r[1],
                    source=r[2],
                    confidence=r[3],
                    verified_at=r[4],
                    verified_by=r[5],
                    evidence=json.loads(r[6]) if r[6] else None,
                    lang_class=r[7] or "single",
                    lang_codes=_decode_lang_codes(r[8]),
                )
            )
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_audio_lang_store_multi.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Regression — existing store + verify tests stay green**

Run: `python -m pytest tests/test_audio_lang_verify.py tests/test_audio_source.py -q`
Expected: PASS (the new upsert kwargs are optional; existing single-language upserts are unchanged).

- [ ] **Step 6: Commit**

```bash
ruff format src/subarr/audio_lang_store.py tests/test_audio_lang_store_multi.py
git add src/subarr/audio_lang_store.py tests/test_audio_lang_store_multi.py
git commit -m "#357 audio_lang_store persists lang_class + lang_codes (singular always populated)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Auto-record confident-multilingual verifications (REVISED during execution)

> **⚠️ REVISED 2026-07-02 (supersedes the original Task 7 below).** `verify_audio_language` has NO production caller (an unwired #90 stub) — extending it would be dead code. The live auto-record hook is the audit walker's Tier-2 block (`audio_audit._audit_one`, ~:283) which already writes `whisper-robust` verifications. Revised approach: in that same block, when `status == "multilingual"` and `self._audio_lang is not None`, upsert with `source="auto-high-conf-multi"`, `lang_class="multi"`, `lang_codes=multilingual_langs`, `lang_code=multilingual_langs[0]`, `confidence=0.9`, mirroring the existing `whisper-robust` write. Depends on Task 6 (store accepts `lang_class`/`lang_codes`). Files: `src/subarr/audio_audit.py`. Test: `tests/test_audio_audit_multilingual.py` (extends the Task 4 test with a `_FakeLangStore` accepting `**kw`).

<details><summary>Original Task 7 (superseded — do not implement)</summary>

**Files:**
- Modify: `src/subarr/audio_lang_verify.py` (`verify_audio_language`)
- Test: extend `tests/test_audio_lang_store_multi.py` OR new `tests/test_audio_lang_verify_multi.py`

**Design:** `verify_audio_language` today parses only the aggregate and stores single-language whisper verifications. Extend it: after fetching `resp`, run the parser + classifier; when `>= 2` high-conf langs, upsert with `source='auto-high-conf-multi'`, `lang_class='multi'`, `lang_codes=high_conf`, `lang_code=high_conf[0]`, and return the set — AHEAD of the existing single-language majority path. Threshold comes from `settings.multilang_chunk_min_prob`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_audio_lang_verify_multi.py`:

```python
"""#357 — verify_audio_language auto-records confident-multilingual files."""

from __future__ import annotations

import pytest


class _StubStore:
    def __init__(self):
        self.upserts = []

    def upsert(self, **kw):
        self.upserts.append(kw)


class _StubSubgen:
    def __init__(self, resp):
        self._resp = resp

    async def detect_language_robust(self, path):
        return self._resp


@pytest.mark.asyncio
async def test_auto_records_multilingual_with_set_and_source():
    from subarr.audio_lang_verify import verify_audio_language

    resp = {
        "aggregate": {"language": "gl", "n_agreeing": 1, "n_total": 3},
        "chunks": [
            {"language": "gl", "probability": 0.91},
            {"language": "es", "probability": 0.88},
            {"language": "fr", "probability": 0.76},
        ],
    }
    store = _StubStore()
    out = await verify_audio_language(_StubSubgen(resp), store, "Movies/TheBeasts.mkv", to_subgen=lambda p: p)
    assert out == (["gl", "es", "fr"], 0.9) or out[0] == ["gl", "es", "fr"]
    assert len(store.upserts) == 1
    u = store.upserts[0]
    assert u["source"] == "auto-high-conf-multi"
    assert u["lang_class"] == "multi"
    assert u["lang_codes"] == ["gl", "es", "fr"]
    assert u["lang_code"] == "gl"  # first-of-set, singular consumers keep working


@pytest.mark.asyncio
async def test_single_high_conf_still_takes_the_majority_path():
    from subarr.audio_lang_verify import verify_audio_language

    # 3/3 Korean, unanimous -> existing single path, NOT multilingual.
    resp = {
        "aggregate": {"language": "korean", "n_agreeing": 3, "n_total": 3},
        "chunks": [
            {"language": "ko", "probability": 0.95},
            {"language": "ko", "probability": 0.9},
            {"language": "ko", "probability": 0.88},
        ],
    }
    store = _StubStore()
    out = await verify_audio_language(_StubSubgen(resp), store, "x.mkv", to_subgen=lambda p: p)
    assert out == ("ko", 1.0)
    assert store.upserts[0]["source"] == "whisper"
    assert store.upserts[0].get("lang_class", "single") == "single"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_audio_lang_verify_multi.py -q`
Expected: FAIL — the multilingual upsert asserts (`source == "auto-high-conf-multi"`) fail; today's code stores nothing (no majority) or single-only.

- [ ] **Step 3: Write minimal implementation**

In `src/subarr/audio_lang_verify.py`, insert the multilingual check between fetching `resp` and the existing aggregate logic. Replace the body after the `try/except` fetch block:

```python
    agg = (resp or {}).get("aggregate") or {}

    # #357: confident-multilingual auto-record fires AHEAD of the single-language
    # majority path. Uses the per-chunk probabilities (parse_robust_detect ->
    # chunks_conf) and the T threshold; >=2 high-conf langs => store the set.
    from .arena import parse_robust_detect
    from .config import settings
    from .multilang import classify_high_conf_langs

    parsed = parse_robust_detect(resp)
    if parsed is not None:
        high = classify_high_conf_langs(parsed.get("chunks_conf") or [], settings.multilang_chunk_min_prob)
        if len(high) >= 2:
            confidence = 0.9  # confident by construction (>=1 chunk >= T per lang)
            store.upsert(
                canonical_path=canonical_path,
                lang_code=high[0],  # first-of-set; singular consumers keep working
                source="auto-high-conf-multi",
                confidence=confidence,
                evidence=resp,
                lang_class="multi",
                lang_codes=high,
            )
            return (high, confidence)

    lang = normalize_lang(agg.get("language"))
    n_agree = agg.get("n_agreeing") or 0
    n_total = agg.get("n_total") or 0
    if not lang or lang == "und" or not n_total or (n_agree / n_total) < min_agreement:
        return None  # no real majority — never store a guess
    confidence = round(n_agree / n_total, 2)
    store.upsert(
        canonical_path=canonical_path, lang_code=lang, source="whisper", confidence=confidence, evidence=resp
    )
    return (lang, confidence)
```

Note: `settings.multilang_chunk_min_prob` is read at call time (after any test's `object.__setattr__` toggle), and the imports are function-local to dodge the ruff import-strip hook and the conftest reload identity issue.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_audio_lang_verify_multi.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Regression**

Run: `python -m pytest tests/test_audio_lang_verify.py -q`
Expected: PASS (existing single-language + no-majority + subgen-error tests unaffected — the multilingual branch only fires on `>=2` high-conf langs, which none of those fixtures produce).

- [ ] **Step 6: Commit**

```bash
ruff format src/subarr/audio_lang_verify.py tests/test_audio_lang_verify_multi.py
git add src/subarr/audio_lang_verify.py tests/test_audio_lang_verify_multi.py
git commit -m "#357 auto-record confident-multilingual verifications (source auto-high-conf-multi)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

</details>

---

## Task 8: Coverage suppresses suspect for multilingual

**Files:**
- Modify: `src/subarr/coverage_engine.py:940-1067` (`_classify_audio_label`)
- Test: `tests/test_coverage_multilingual.py`

**Design:** `_classify_audio_label` receives per-file verification metadata today via `user_verifications` / `whisper_verifications` (both `dict[path -> lang]`). To carry the *class*, add one optional param `multi_verifications: dict[str, list[str]] | None = None` (path → ordered lang set). When a file is in `multi_verifications`, set `item.audio_langs` to the set, `item.audio_source = "multilingual"`, clear `audio_label_suspect`/`audio_label_unknown`, and return early — a Layer-0.5 that sits just below the user-verification layer and above the whisper-single layer. The CoverageItem gains an `audio_lang_codes` field so `to_dict` can emit the set to the UI.

- [ ] **Step 1: Write the failing test**

```python
"""#357 — a confident-multilingual file surfaces 'multilingual', not 'suspect'."""

from __future__ import annotations

from subarr.coverage_engine import CoverageItem, _classify_audio_label


def _item(**kw):
    base = dict(media_type="movie", title="The Beasts", canonical_path="Movies/TheBeasts")
    base.update(kw)
    return CoverageItem(**base)


def test_multilingual_file_is_not_suspect():
    it = _item(
        audio_langs=["gl"],
        original_language="Spanish",  # foreign -> would normally be suspect if all-English
        file_canonical_path="Movies/TheBeasts.mkv",
    )
    _classify_audio_label(it, multi_verifications={"Movies/TheBeasts.mkv": ["gl", "es", "fr"]})
    assert it.audio_source == "multilingual"
    assert it.audio_label_suspect is False
    assert it.audio_langs == ["gl", "es", "fr"]
    assert it.audio_lang_codes == ["gl", "es", "fr"]


def test_non_multilingual_file_unchanged():
    it = _item(audio_langs=["eng"], original_language="English", file_canonical_path="Movies/Reg.mkv")
    _classify_audio_label(it)
    assert it.audio_source == "ffprobe"
    assert it.audio_lang_codes in (None, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_coverage_multilingual.py -q`
Expected: FAIL — `TypeError: _classify_audio_label() got an unexpected keyword argument 'multi_verifications'` (and `CoverageItem` has no `audio_lang_codes`).

- [ ] **Step 3: Write minimal implementation**

(a) Add the field to `CoverageItem`. Find its dataclass definition (grep `class CoverageItem` in `coverage_engine.py`) and add near the other audio fields:

```python
    audio_lang_codes: list[str] | None = None  # #357: multilingual set (>=2 langs)
```

Ensure `to_dict` emits it (find the `to_dict` method's audio block and add `"audio_lang_codes": self.audio_lang_codes,`).

(b) Extend `_classify_audio_label` signature:

```python
def _classify_audio_label(
    item: CoverageItem,
    tautulli_hints: dict[str, str] | None = None,
    user_verifications: dict[str, str] | None = None,
    plex_hints: dict[str, str] | None = None,
    whisper_verifications: dict[str, str] | None = None,
    multi_verifications: dict[str, list[str]] | None = None,
) -> None:
```

(c) Add the multilingual layer immediately after the Layer-0 user-verification block (right after its `return`, before the Layer-1 whisper block near line 985):

```python
    # #357 Layer 0.5: a confident-multilingual verdict (auto-recorded or user-
    # corrected) is the ANSWER, not a mislabel. Surface the ordered set and
    # suppress the suspect alarm. Below an explicit single-language user pick,
    # above the whisper-single layer.
    if multi_verifications and file_path and file_path in multi_verifications:
        codes = [c for c in multi_verifications[file_path] if c]
        if len(codes) >= 2:
            item.audio_langs = list(codes)
            item.audio_lang_codes = list(codes)
            item.audio_label_notes.append(
                "multilingual audio ("
                + "·".join(codes)  # gl·es·fr
                + ") - confident multi-language detection, not a mislabel"
            )
            item.audio_label_suspect = False
            item.audio_label_unknown = False
            item.audio_source = "multilingual"
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_coverage_multilingual.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Wire the store → coverage plumbing (find + connect the call site)**

`_classify_audio_label` is called from the probe/verify pass (`coverage_engine.py:929`). Find where `whisper_verifications` / `user_verifications` are sourced for the build (grep `whisper_verifications=` and `get_all_as_lookup` across `coverage_engine.py` + `src/subarr/routers/audio_lang.py`). Add a parallel `multi_verifications` map sourced from the store: for every verification whose `lang_class == "multi"`, map `canonical_path -> lang_codes`. Add a store helper mirroring `get_all_as_lookup`:

In `src/subarr/audio_lang_store.py`:

```python
    def get_all_multi_as_lookup(self) -> dict[str, list[str]]:
        """#357: {canonical_path: lang_codes} for every lang_class='multi' row.
        Read by build_coverage so multilingual files surface the set + skip the
        suspect flag. Mirrors get_all_as_lookup()'s fast-path (no series-intent)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT canonical_path, lang_codes FROM audio_lang_verifications WHERE lang_class = 'multi'"
            ).fetchall()
        out: dict[str, list[str]] = {}
        for path, raw in rows:
            codes = _decode_lang_codes(raw)
            if codes:
                out[path] = codes
        return out
```

Then thread `multi_verifications=<store>.get_all_multi_as_lookup()` through the same path that already passes `whisper_verifications`. Add a store round-trip test for the helper:

```python
def test_get_all_multi_as_lookup(tmp_path):
    store = _store(tmp_path)
    store.upsert(canonical_path="a.mkv", lang_code="gl", source="auto-high-conf-multi",
                 lang_class="multi", lang_codes=["gl", "es"])
    store.upsert(canonical_path="b.mkv", lang_code="ja", source="user")  # single
    assert store.get_all_multi_as_lookup() == {"a.mkv": ["gl", "es"]}
```

(Append this to `tests/test_audio_lang_store_multi.py`.)

- [ ] **Step 6: Run the coverage + store suites**

Run: `python -m pytest tests/test_coverage_multilingual.py tests/test_audio_lang_store_multi.py tests/test_audio_source.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
ruff format src/subarr/coverage_engine.py src/subarr/audio_lang_store.py tests/test_coverage_multilingual.py tests/test_audio_lang_store_multi.py
git add src/subarr/coverage_engine.py src/subarr/audio_lang_store.py tests/test_coverage_multilingual.py tests/test_audio_lang_store_multi.py
git commit -m "#357 coverage surfaces multilingual state and suppresses false suspect flag

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Override suppression for multi + zxx

**Files:**
- Modify: `src/subarr/audio_lang_store.py:432-521` (`resolve_audio_language_override`)
- Test: `tests/test_override_suppression_multi_zxx.py`

**Design:** `resolve_audio_language_override` reads the verification and decides whether to forward an override to subgen. Add two early returns: `verification.lang_class == "multi"` → None (no single source language), and `verification.lang_code == "zxx"` → None (no linguistic content). Both log at INFO for auditability.

- [ ] **Step 1: Write the failing test**

```python
"""#357 — override suppressed for multilingual (lang_class='multi') and zxx."""

from __future__ import annotations

from pathlib import Path

from subarr.migrate import MigrationRunner


def _store(tmp_path: Path):
    db = tmp_path / "subarr.db"
    import subarr.migrate as m

    MigrationRunner(db, Path(m.__file__).parent / "migrations").run()
    from subarr.audio_lang_store import AudioLangStore

    return AudioLangStore(db)


def test_multi_file_forwards_no_override(tmp_path):
    from subarr.audio_lang_store import resolve_audio_language_override

    store = _store(tmp_path)
    store.upsert(
        canonical_path="Movies/TheBeasts.mkv",
        lang_code="gl",
        source="auto-high-conf-multi",
        confidence=0.9,
        lang_class="multi",
        lang_codes=["gl", "es", "fr"],
    )
    assert resolve_audio_language_override(store, "Movies/TheBeasts.mkv") is None


def test_zxx_file_forwards_no_override(tmp_path):
    from subarr.audio_lang_store import resolve_audio_language_override

    store = _store(tmp_path)
    store.upsert(canonical_path="Movies/JunkHead.mkv", lang_code="zxx", source="user", confidence=1.0)
    assert resolve_audio_language_override(store, "Movies/JunkHead.mkv") is None


def test_regular_foreign_single_still_forwards(tmp_path):
    from subarr.audio_lang_store import resolve_audio_language_override

    store = _store(tmp_path)
    store.upsert(canonical_path="TV/S/ep.mkv", lang_code="ja", source="user", confidence=1.0)
    assert resolve_audio_language_override(store, "TV/S/ep.mkv") == "ja"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_override_suppression_multi_zxx.py -q`
Expected: FAIL — `test_multi_file_forwards_no_override` returns `"gl"` (currently forwards); `test_zxx_file_forwards_no_override` returns `"zxx"`.

- [ ] **Step 3: Write minimal implementation**

In `resolve_audio_language_override`, right after `verification = store.get(canonical)` and its `if verification is None: return None`, add:

```python
    # #357: multilingual + zxx files have no single source language to declare —
    # let subgen do its own per-chunk detection rather than forwarding a wrong
    # single-language override.
    if getattr(verification, "lang_class", "single") == "multi":
        _log.info(
            "%s: no override for %s — multilingual (lang_codes=%s); subgen self-detects",
            caller,
            scrub(canonical),
            getattr(verification, "lang_codes", None),
        )
        return None
    if (verification.lang_code or "").strip().lower() == "zxx":
        _log.info(
            "%s: no override for %s — zxx (no linguistic content); subgen self-detects",
            caller,
            scrub(canonical),
        )
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_override_suppression_multi_zxx.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Regression on the override helper's existing tests**

Run: `python -m pytest tests/ -q -k override`
Expected: PASS (the new guards fire only for multi / zxx; single foreign files still forward).

- [ ] **Step 6: Commit**

```bash
ruff format src/subarr/audio_lang_store.py tests/test_override_suppression_multi_zxx.py
git add src/subarr/audio_lang_store.py tests/test_override_suppression_multi_zxx.py
git commit -m "#357 suppress audio_language_override for multilingual and zxx files

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: `zxx` selectable in the picker

**Files:**
- Modify: `src/subarr/langs.py` (`WHISPER_LANGUAGES` dict ~:127)
- Test: `tests/test_zxx_language.py`

**Note:** `zxx` is already in the ISO map (`langs.py:114`, `"zxx": "zxx"`), so `normalize_lang("zxx")` round-trips. It is merely absent from the `WHISPER_LANGUAGES` picker set.

- [ ] **Step 1: Write the failing test**

```python
"""#357 — zxx (no linguistic content) is selectable via /api/languages."""

from __future__ import annotations

from subarr.langs import WHISPER_LANGUAGES, normalize_lang


def test_zxx_in_whisper_languages():
    assert WHISPER_LANGUAGES.get("zxx") == "No linguistic content"


def test_zxx_normalizes_to_itself():
    assert normalize_lang("zxx") == "zxx"


def test_languages_endpoint_offers_zxx(app_with_stub):
    resp = app_with_stub.get("/api/languages")
    assert resp.status_code == 200
    codes = {row["code"] for row in resp.json()["languages"]}
    assert "zxx" in codes
```

Note: `test_languages_endpoint_offers_zxx` uses the `app_with_stub` fixture (TestClient) from `conftest.py`, and `/api/languages` is a plain sync route so no asyncio marker is needed for the client call.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_zxx_language.py -q`
Expected: FAIL — `WHISPER_LANGUAGES.get("zxx")` is `None` (absent from the picker set).

- [ ] **Step 3: Write minimal implementation**

In `src/subarr/langs.py`, add `zxx` to the `WHISPER_LANGUAGES` dict. Add it at the end of the dict (before the closing `}`), so it appears in `/api/languages` (which sorts by name, so it lands under "N"):

```python
    # #357: user-applied only (Whisper cannot reliably emit it). For constructed
    # gibberish / silent-film audio (Junk Head). Marking a file zxx suppresses
    # the suspect flag and skips the subgen override.
    "zxx": "No linguistic content",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_zxx_language.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Guard the languages endpoint + frontend languages tests**

Run: `python -m pytest tests/ -q -k language`
Expected: PASS. Also run the frontend languages test (it stubs the fetch, so it does not assert the full set and will not regress): `npm run test:frontend -- languages`.

- [ ] **Step 6: Commit**

```bash
ruff format src/subarr/langs.py tests/test_zxx_language.py
git add src/subarr/langs.py tests/test_zxx_language.py
git commit -m "#357 add zxx (No linguistic content) to the selectable language set

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Coverage badge — `🌐 gl·es·fr` for multilingual, `zxx` label

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/coverage.jsx` (`AudioLabelChip` ~:647-701)
- Test: `src/subarr/static/v1/home-hifi/__tests__/coverage-multilingual-badge.test.js`

**Design:** The classifier logic inside `AudioLabelChip` picks a `kind`. Add two new kinds ahead of `suspect`: `multilingual` (when `r.audio_source === 'multilingual'` or `r.audio_lang_codes?.length >= 2`) and `zxx` (when the single audio lang is `zxx`). To make this unit-testable without a DOM, extract the kind-selection into an exported pure helper `audioBadgeKind(r)` and have `AudioLabelChip` call it; the vitest test asserts the helper.

- [ ] **Step 1: Write the failing test**

```javascript
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:frontend -- coverage-multilingual-badge`
Expected: FAIL — `audioBadgeKind` is not exported from `coverage.jsx`.

- [ ] **Step 3: Write minimal implementation**

In `coverage.jsx`, extract and export the kind selection. Add above `function AudioLabelChip`:

```javascript
// #357: pure classifier for the audio badge state. Exported for unit tests.
// multilingual + zxx sit AHEAD of suspect so a confident multi-language answer
// stops the false suspect alarm.
export function audioBadgeKind(r) {
  const codes = r.audio_lang_codes || [];
  const langs = r.audio_langs || [];
  if (r.audio_source === 'multilingual' || codes.length >= 2) return 'multilingual';
  if (langs.length === 1 && String(langs[0]).toLowerCase() === 'zxx') return 'zxx';
  if (r.audio_verified || r.audio_source === 'user') return 'user';
  if (r.audio_source === 'whisper') return 'whisper';
  if (r.audio_source === 'plex') return 'plex';
  if (r.audio_label_suspect) return 'suspect';
  if (r.audio_label_unknown) return 'unknown';
  if (r.audio_source === 'ffprobe') return 'ffprobe';
  return null;
}
```

Then in `AudioLabelChip`, replace the inline `let kind = null; ... ` cascade (lines ~651-658) with:

```javascript
  const kind = audioBadgeKind(r);
  if (!kind) return null;
```

And add the two new entries to the `cfg` map (after the `ffprobe`/`suspect`/`unknown` entries, keeping the object literal valid):

```javascript
    multilingual: {
      ch: '🌐', // 🌐
      bg: 'rgba(52,211,153,0.16)', fg: '#34d399',
      label: 'Multilingual audio: '
        + (r.audio_lang_codes || []).join('·')  // gl·es·fr
        + ' - confident multi-language detection, not a mislabel',
    },
    zxx: {
      ch: '∅', // ∅
      bg: 'rgba(148,163,184,0.16)', fg: '#94a3b8',
      label: 'No linguistic content (zxx) - constructed or non-speech audio',
    },
```

For the multilingual badge, also render the code string inline next to the globe so the row reads `🌐 gl·es·fr`. In the badge `<span>` render block (after line ~688), when `kind === 'multilingual'` append a small text span:

```javascript
  const badge = (
    <span
      title={tip}
      onClick={(e) => { e.stopPropagation(); onClick && onClick(r); }}
      style={{ /* unchanged existing style object */ }}>
      {cfg.ch}
      {kind === 'multilingual' && (
        <span style={{ marginLeft: 4, fontSize: 9, fontWeight: 600 }}>
          {(r.audio_lang_codes || []).join('·')}
        </span>
      )}
    </span>
  );
```

(Leave the width/auto-size style so the code string is not clipped — remove the fixed `width: 16` for the multilingual case or let it grow via `minWidth`/padding. A minimal safe change: keep the existing style but add `paddingRight: kind === 'multilingual' ? 4 : 0` and drop the fixed width when multilingual.)

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:frontend -- coverage-multilingual-badge`
Expected: PASS (6 passed).

- [ ] **Step 5: Rebuild the bundle + guard the frontend suite**

Run: `npm run build:frontend`
Run: `npm run test:frontend`
Expected: build succeeds; all frontend tests PASS. (The served artifact is `coverage.bundle.js`; without the rebuild the browser would not see the JSX change — see the deploy-to-dev SRC_PATHS gotcha note below.)

> Deploy note (out of plan scope, flagged for the controller): the dev deploy script's `SRC_PATHS` must include any new bundle for `deploy-to-dev.sh` to sync it. This plan only rebuilds the existing `coverage.bundle.js`, so no `SRC_PATHS` edit is required here — but confirm at release.

- [ ] **Step 6: Commit**

```bash
git add src/subarr/static/v1/home-hifi/coverage.jsx src/subarr/static/v1/home-hifi/coverage.bundle.js src/subarr/static/v1/home-hifi/coverage.bundle.js.map src/subarr/static/v1/home-hifi/__tests__/coverage-multilingual-badge.test.js
git commit -m "#357 coverage badge renders multilingual globe and zxx label

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 12: Review modal multi-select + glance lane

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/review.jsx` (bulk language picker ~:917-960; glance-lane filter)
- Test: `src/subarr/static/v1/home-hifi/__tests__/review-multiselect.test.js` (if the picker logic can be extracted to a pure helper); otherwise manual verification steps

**Design:** Two changes:
1. **Multi-select correction:** the bulk "Assign audio language" `<select>` (single) gains a companion multi-select mode. When the user picks 2+ languages, `applyBulk` POSTs `lang_class='multi'` + `lang_codes` to `/api/audio-lang/verifications` (the `VerifyRequest` model must accept the two new optional fields — see the router note below). A single pick keeps writing `lang_class='single'` as today. `zxx` is one of the selectable options (from `/api/languages`, Task 10).
2. **Glance lane:** a low-priority filter/section in the review surface that lists rows whose `source === 'auto-high-conf-multi'` so the auto-classified set can be eyeballed/corrected in bulk.

**Router prerequisite (backend, small):** extend `VerifyRequest` (`src/subarr/routers/audio_lang.py:35`) with `lang_class: str = "single"` and `lang_codes: list[str] | None = None`, and pass them into `store.upsert(...)` in `upsert_verification`. This is a backend change so it gets a Python test.

- [ ] **Step 1: Write the failing backend test**

Create `tests/test_audio_lang_router_multi.py`:

```python
"""#357 — POST /api/audio-lang/verifications accepts lang_class + lang_codes."""

from __future__ import annotations


def test_post_multi_verification_persists_class_and_codes(app_with_stub):
    body = {
        "canonical_path": "Movies/TheBeasts.mkv",
        "lang_code": "gl",
        "source": "user",
        "lang_class": "multi",
        "lang_codes": ["gl", "es", "fr"],
    }
    resp = app_with_stub.post("/api/audio-lang/verifications", json=body)
    assert resp.status_code == 200

    got = app_with_stub.get("/api/audio-lang/verifications/Movies%2FTheBeasts.mkv")
    assert got.status_code == 200
    data = got.json()
    assert data["lang_class"] == "multi"
    assert data["lang_codes"] == ["gl", "es", "fr"]
    assert data["lang_code"] == "gl"


def test_post_single_verification_defaults_single(app_with_stub):
    resp = app_with_stub.post(
        "/api/audio-lang/verifications",
        json={"canonical_path": "TV/S/ep.mkv", "lang_code": "ja", "source": "user"},
    )
    assert resp.status_code == 200
    got = app_with_stub.get("/api/audio-lang/verifications/TV%2FS%2Fep.mkv").json()
    assert got["lang_class"] == "single"
    assert got["lang_codes"] is None
```

Note: confirm the single-get route path (`GET /api/audio-lang/verifications/{path}`) and its path-encoding by reading `src/subarr/routers/audio_lang.py` around line 43-48 and the get-one handler; adjust the URL-encoding in the test if the route uses a different param style.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_audio_lang_router_multi.py -q`
Expected: FAIL — the POST ignores `lang_class`/`lang_codes` (not on `VerifyRequest`), so the stored row is `single`.

- [ ] **Step 3: Write minimal backend implementation**

In `src/subarr/routers/audio_lang.py`, extend `VerifyRequest`:

```python
class VerifyRequest(BaseModel):
    canonical_path: str
    lang_code: str
    source: str = "user"
    confidence: float = 1.0
    evidence: dict | None = None
    lang_class: str = "single"  # #357
    lang_codes: list[str] | None = None  # #357
```

And in `upsert_verification`, pass them through to the store:

```python
    store.upsert(
        canonical_path=req.canonical_path,
        lang_code=lang,
        source=req.source,
        confidence=req.confidence,
        evidence=req.evidence,
        lang_class=req.lang_class,
        lang_codes=req.lang_codes,
    )
```

(The `to_dict()` on `AudioLangVerification` already emits `lang_class`/`lang_codes` from Task 6, so the GET-one response carries them.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_audio_lang_router_multi.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Frontend multi-select + glance lane**

In `review.jsx`:

(a) Add a multi-select mode to the bulk picker. The simplest robust approach that stays testable: keep the existing single `<select>` for single-language assign, and add a checkbox `Multilingual` toggle that switches the control to a `<select multiple>` bound to a `bulkLangs` array state. Extract the write-payload builder into a pure exported helper so it can be unit-tested:

```javascript
// #357: build the verification POST body from the bulk selection. Exported for tests.
export function buildVerifyBody(canonicalPath, langs) {
  const codes = (langs || []).filter(Boolean);
  if (codes.length >= 2) {
    return { canonical_path: canonicalPath, lang_code: codes[0], source: 'user',
             lang_class: 'multi', lang_codes: codes };
  }
  return { canonical_path: canonicalPath, lang_code: codes[0] || 'und', source: 'user',
           lang_class: 'single' };
}
```

Wire `applyBulk` to POST `buildVerifyBody(path, selectedLangs)` per selected path.

(b) Glance lane: add a filter section to the review surface listing rows where `r.audio_source === 'auto-high-conf-multi'` (the coverage payload's `audio_source`; note the coverage classifier sets `audio_source='multilingual'` for the display state, while the *store* source is `auto-high-conf-multi` — the glance lane keys on the pending-review payload's source field). Extract a pure predicate for testability:

```javascript
// #357: the glance lane surfaces auto-classified multilingual rows for a bulk eyeball.
export function isAutoMultilingualRow(r) {
  return r.audio_source === 'auto-high-conf-multi' || r.audio_source === 'multilingual';
}
```

- [ ] **Step 6: Write + run the frontend test**

Create `src/subarr/static/v1/home-hifi/__tests__/review-multiselect.test.js`:

```javascript
// #357 — the review multi-select payload builder + glance-lane predicate.
import { describe, it, expect } from 'vitest';
import { buildVerifyBody, isAutoMultilingualRow } from '../review.jsx';

describe('buildVerifyBody', () => {
  it('two or more langs -> multi with lang_codes', () => {
    expect(buildVerifyBody('Movies/TheBeasts.mkv', ['gl', 'es', 'fr'])).toEqual({
      canonical_path: 'Movies/TheBeasts.mkv', lang_code: 'gl', source: 'user',
      lang_class: 'multi', lang_codes: ['gl', 'es', 'fr'],
    });
  });

  it('single lang -> single', () => {
    expect(buildVerifyBody('x.mkv', ['ja'])).toEqual({
      canonical_path: 'x.mkv', lang_code: 'ja', source: 'user', lang_class: 'single',
    });
  });

  it('zxx single pick -> single zxx', () => {
    expect(buildVerifyBody('x.mkv', ['zxx']).lang_code).toBe('zxx');
  });
});

describe('isAutoMultilingualRow', () => {
  it('true for auto-high-conf-multi rows', () => {
    expect(isAutoMultilingualRow({ audio_source: 'auto-high-conf-multi' })).toBe(true);
  });
  it('false for a plain ffprobe row', () => {
    expect(isAutoMultilingualRow({ audio_source: 'ffprobe' })).toBe(false);
  });
});
```

Run: `npm run test:frontend -- review-multiselect`
Expected: PASS (5 passed).

- [ ] **Step 7: Rebuild bundle + full frontend suite + backend regression**

Run: `npm run build:frontend`
Run: `npm run test:frontend`
Run: `python -m pytest tests/test_audio_lang_router_multi.py -q`
Expected: all PASS.

- [ ] **Step 8: Manual verification (record outcome; the pure helpers are the automated proof)**

Since the interactive multi-select DOM is not unit-tested end-to-end, record these manual steps against dev (port 9923, NEVER 9922):
1. Open Review, select a suspect row, toggle Multilingual, pick 2+ languages, Apply → row badge becomes `🌐 …` (not suspect); GET the verification shows `lang_class='multi'`.
2. Mark a file `zxx` via the single picker → `∅`/zxx badge; no override forwarded on requeue.
3. The glance-lane filter lists an `auto-high-conf-multi` row.

- [ ] **Step 9: Commit**

```bash
ruff format src/subarr/routers/audio_lang.py tests/test_audio_lang_router_multi.py
git add src/subarr/routers/audio_lang.py tests/test_audio_lang_router_multi.py src/subarr/static/v1/home-hifi/review.jsx src/subarr/static/v1/home-hifi/review.bundle.js src/subarr/static/v1/home-hifi/review.bundle.js.map src/subarr/static/v1/home-hifi/__tests__/review-multiselect.test.js
git commit -m "#357 review multi-select correction + glance lane; router accepts lang_class/lang_codes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 13: Full verification + hand back to controller

**Files:**
- No new code. Verification only.

- [ ] **Step 1: Full Python suite**

Run: `python -m pytest -q`
Expected: PASS (the whole suite green — no regressions in the ~1400 tests). If any pre-existing test fails, root-cause it (do NOT weaken the assertion) before proceeding.

- [ ] **Step 2: Lint changed Python files**

Run:
```bash
ruff check src/subarr/arena.py src/subarr/multilang.py src/subarr/config.py src/subarr/arena_service.py src/subarr/audio_lang_store.py src/subarr/audio_lang_verify.py src/subarr/coverage_engine.py src/subarr/langs.py src/subarr/routers/audio_lang.py tests/test_parse_robust_detect_conf.py tests/test_multilang_classifier.py tests/test_config_multilang.py tests/test_resolve_source_language_multi.py tests/test_migration_027_multilingual.py tests/test_audio_lang_store_multi.py tests/test_coverage_multilingual.py tests/test_override_suppression_multi_zxx.py tests/test_zxx_language.py tests/test_audio_lang_verify_multi.py tests/test_audio_lang_router_multi.py
ruff format --check src/subarr/arena.py src/subarr/multilang.py src/subarr/config.py src/subarr/arena_service.py src/subarr/audio_lang_store.py src/subarr/audio_lang_verify.py src/subarr/coverage_engine.py src/subarr/langs.py src/subarr/routers/audio_lang.py tests/test_parse_robust_detect_conf.py tests/test_multilang_classifier.py tests/test_config_multilang.py tests/test_resolve_source_language_multi.py tests/test_migration_027_multilingual.py tests/test_audio_lang_store_multi.py tests/test_coverage_multilingual.py tests/test_override_suppression_multi_zxx.py tests/test_zxx_language.py tests/test_audio_lang_verify_multi.py tests/test_audio_lang_router_multi.py
```
Expected: `ruff check` reports "All checks passed"; `ruff format --check` reports no files would be reformatted. Fix any finding, re-run.

- [ ] **Step 3: Full frontend suite + bundle freshness**

Run: `npm run test:frontend`
Run: `npm run check:frontend`
Expected: all vitest tests PASS; `check:frontend` exits 0 (bundles are rebuilt and match the committed `.jsx` — no uncommitted bundle drift).

- [ ] **Step 4: Confirm the migration count + no runtime files staged**

Run: `git status --porcelain`
Expected: only the intended source/test/bundle files. NO `subarr.db`, no `*.db-wal`, no logs (the pre-commit DB guard would block a DB anyway).

- [ ] **Step 5: Hand back to the controller**

Do NOT push or open the PR. Summarise for the controller: tasks completed, full-suite + ruff + vitest all green (paste the counts), and the Task 0 feasibility outcome. The controller opens the PR and runs the risk-tiered pre-merge review (Tier-1/2 per the subarr review program — scrutinise the additive migration and the "singular `lang_code` always populated" invariant).

---

## Spec coverage (self-review appendix)

Every requirement in `docs/superpowers/specs/2026-07-02-357-multilingual-audio-design.md` maps to a task:

| Spec section / requirement | Task(s) |
|----------------------------|---------|
| Feasibility gate — per-chunk `probability` present | Task 0 |
| Component 1: capture per-chunk confidence (`parse_robust_detect`) | Task 1 |
| Component 1: `high_conf_langs` rule (≥2 multi / ==1 single / ==0 confused) | Task 2 (classifier) + Task 4 (wired into `resolve_source_language`) |
| Threshold `T=0.5`, env `SUBARR_MULTILANG_CHUNK_MIN_PROB` | Task 3 |
| Classifier slots AHEAD of unanimous/bilingual/confused | Task 4 |
| Component 2: `lang_class` + `lang_codes` columns, additive migration, no PK change, no backfill | Task 5 |
| Component 2: singular `lang_code` always populated (first-of-set) | Task 5 (schema) + Task 6 (store) + Task 7 (auto-record) |
| Component 3: auto-record `source='auto-high-conf-multi'`, suspect suppressed | Task 7 (record) + Task 8 (suppress) |
| Component 3: glance lane (low-priority review of auto-classified multi) | Task 12 |
| Component 3: override suppression for multilingual | Task 9 |
| Component 4: `zxx` selectable, user-applied, suppresses suspect + skips override | Task 10 (picker) + Task 9 (override) + Task 8 (suspect via multi path) |
| Component 5: coverage badge `🌐 gl·es·fr` replaces suspect; `zxx` own badge | Task 11 |
| Component 5: review modal multi-select; `zxx` selectable; writes class/codes | Task 12 |
| Component 5: glance-lane filter surfaces `auto-high-conf-multi` | Task 12 |
| Data flow (end-to-end) | Tasks 1→2→4→7→8→9→11 |
| Error handling: missing `probability` → graceful | Task 1 (`chunks_conf` None) + Task 2 (None < T) |
| Error handling: malformed `lang_codes` JSON → treat as single, log, no crash | Task 6 (`_decode_lang_codes`) |
| Error handling: multi/zxx no override → subgen self-detects | Task 9 |
| Testing: detection rule / parser / store round-trip / coverage / override / zxx / regression | Tasks 1,2,4,5,6,7,8,9,10,12 + Task 13 (full suite) |
| Acceptance 1 (The Beasts auto-recorded, badge, no override) | Tasks 7,8,9,11 |
| Acceptance 2 (confused file falls back to tag) | Tasks 2,4 |
| Acceptance 3 (user marks zxx, displays, skips override) | Tasks 9,10,12 |
| Acceptance 4 (single-language unchanged; suite green; ruff clean) | Task 4/6/7 regression steps + Task 13 |
| Non-goals (no coverage/sub-needed re-arch, no per-segment, no series-level intent) | Not implemented (respected — no task touches `series_lang_intent` multi or coverage gap logic) |

**Placeholder scan:** no "TBD"/"implement later"/"add error handling" left — every code step shows real code. **Type consistency:** `chunks_conf: list[tuple[str|None, float|None]]` (Task 1) is consumed identically by `classify_high_conf_langs` (Task 2) and `resolve_source_language` (Task 4); the classifier's `list[str]` return is used as `lang_codes` in the store (Task 6), auto-record (Task 7), coverage (Task 8), and UI (Tasks 11/12); `source='auto-high-conf-multi'` and `audio_source='multilingual'` are used consistently (store source vs display state, distinguished deliberately in Tasks 8 and 12).

## Open questions for the controller

1. **Feasibility (Task 0):** the frontend already reads `c.probability` per chunk (`coverage.jsx:1901`), so the field almost certainly exists post-#396 — but Task 0 must confirm against a live/fixture response or the subgen handler source before the classifier is trusted. If it is absent, Task 1 (graceful) can still land; Tasks 2–12 block on a subgen-side prerequisite.
2. **`audio_source` overloading:** Task 8 sets the coverage *display* state to `audio_source='multilingual'`, while the *store* source is `auto-high-conf-multi`. The glance-lane predicate (Task 12) accepts both. Confirm the controller is happy with this two-value convention, or unify on one and adjust the predicate.
3. **Coverage plumbing (Task 8 Step 5):** the exact call site that sources `whisper_verifications` for `build_coverage` must be located and a parallel `multi_verifications` threaded through. The plan gives the store helper (`get_all_multi_as_lookup`) and the grep to find the site, but the wiring edit depends on the current builder shape — verify during implementation.
