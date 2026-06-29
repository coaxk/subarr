# #358 Audio-Language Canonicalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make 2-letter ISO-639-1 the canonical internal audio-language format end-to-end, give the manual picker the full Whisper language set, and close the silent Sonarr→Bazarr propagation gap for languages the curated map misses (Galician + the long tail).

**Architecture:** A single `WHISPER_LANGUAGES` table in `langs.py` is the source of truth. The store normalizes to 2-letter on write and read; the *only* place 3-letter is produced is the subgen-override boundary (`to_iso3`). The picker fetches the full set from a new `GET /api/languages`. The Sonarr propagation resolves names from `WHISPER_LANGUAGES` and matches Sonarr's live `/language` list, degrading honestly when Sonarr can't represent a language.

**Tech Stack:** Python 3.11 / FastAPI / SQLite (backend), vanilla JSX bundles + vitest (frontend), pytest + ruff. Spec: `docs/superpowers/specs/2026-06-29-358-audio-language-canonicalization-design.md`.

**Branch:** `feat/358-audio-language-canonicalization` (already created, spec committed).

**Conventions:**
- TDD: write the failing test, run it red, implement, run it green, commit. Run the *specific* test file, not the whole suite, until the final task.
- Windows shell quirks: prefer the Bash tool from the repo root; a `nul` artifact can appear from redirects (`rm -f ./nul` if `git add` complains).
- **Ruff import-stripping hook:** the PostToolUse ruff hook deletes a just-added top-level import if the same edit doesn't yet use it. Add the import and its first usage in the *same* edit, or use a function-local import.
- Commit message footer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 0: Verify subgen's override-vs-skip matching (gates `to_iso3`)

This confirms the 3-letter direction before we build on it — verify, don't assume. The `subgen-next` container runs the patched subgen and has whisper installed.

**Files:** none (investigation; record the finding in the Task 1 commit body).

- [ ] **Step 1: Inspect how subgen compares the override against its skip-list**

Run:
```bash
docker exec subgen-next sh -lc 'grep -rn "SKIP_IF_AUDIO_LANGUAGES\|audio_language_override\|skip_if" /subgen 2>/dev/null | head -40' \
  || wsl docker exec subgen-next sh -lc 'grep -rn "SKIP_IF_AUDIO_LANGUAGES\|audio_language_override\|skip_if" /subgen 2>/dev/null | head -40'
```
Expected: lines showing where subgen reads `audio_language_override` and compares it to `SKIP_IF_AUDIO_LANGUAGES`. Note the code form used on each side (2- vs 3-letter; is there a normalization/`.alpha_3`/`langcodes` call?).

- [ ] **Step 2: Confirm the canonical Whisper language set is available there**

Run:
```bash
docker exec subgen-next python -c "from whisper.tokenizer import LANGUAGES; print(len(LANGUAGES)); print(LANGUAGES.get('gl'))" \
  || wsl docker exec subgen-next python -c "from whisper.tokenizer import LANGUAGES; print(len(LANGUAGES)); print(LANGUAGES.get('gl'))"
```
Expected: a count (~99) and `galician`. This dict is the authority for Task 1.

- [ ] **Step 3: Record the finding**

Write one line into your notes for Task 1's commit body, e.g. *"subgen matches override against SKIP_IF_AUDIO_LANGUAGES as 3-letter ISO-639-2 — to_iso3 direction confirmed"* or *"subgen normalizes both sides via langcodes — 2-letter also accepted; to_iso3 still preferred for reliability."* If — and only if — subgen is found to *require* full-coverage 3-letter for every language, expand `to_iso3` in Task 1 to cover all 99 (otherwise the curated set + 2-letter fallback is correct and sufficient).

---

### Task 1: `WHISPER_LANGUAGES`, `to_iso3`, `display_name` in `langs.py`

**Files:**
- Modify: `src/subarr/langs.py` (add after `_ISO3_TO_ISO1`, around line 115)
- Test: `tests/test_langs_iso3_and_table.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""#358: canonical language table + the 2→3 letter boundary helper."""
from __future__ import annotations

from subarr.langs import WHISPER_LANGUAGES, to_iso3, display_name, normalize_lang


def test_table_is_the_whisper_set_with_galician():
    # The full Whisper detection set (~99), keyed by 2-letter code.
    assert 95 <= len(WHISPER_LANGUAGES) <= 110
    assert WHISPER_LANGUAGES["gl"] == "Galician"
    assert WHISPER_LANGUAGES["ko"] == "Korean"
    # every key is a lowercase short code, every value a non-empty title-cased name
    for code, name in WHISPER_LANGUAGES.items():
        assert code == code.lower() and 2 <= len(code) <= 3
        assert name and name[0].isupper()


def test_to_iso3_round_trips_known_codes():
    assert to_iso3("gl") == "glg"
    assert to_iso3("fr") == "fre"   # ISO-639-2/B, not 'fra'
    assert to_iso3("de") == "ger"   # B, not 'deu'
    assert to_iso3("ko") == "kor"
    assert to_iso3("en") == "eng"


def test_to_iso3_falls_back_unchanged_for_unmapped():
    # Defensive: never blanks, never raises — a tail language with no B-code
    # in our map is forwarded as-is (no regression vs today's 2-letter forward).
    assert to_iso3("xx") == "xx"
    assert to_iso3("") == ""


def test_to_iso3_inverts_consistently_with_normalize():
    # normalize_lang(to_iso3(code)) must return the original 2-letter code.
    for code in ("gl", "fr", "de", "ko", "ja", "zh", "ca", "sr"):
        assert normalize_lang(to_iso3(code)) == code


def test_display_name_resolves_and_falls_back():
    assert display_name("gl") == "Galician"
    assert display_name("zz") == "zz"  # unknown → the code itself
```

- [ ] **Step 2: Run it red**

Run: `python -m pytest tests/test_langs_iso3_and_table.py -q`
Expected: FAIL — `ImportError: cannot import name 'WHISPER_LANGUAGES'`.

- [ ] **Step 3: Author the table from the canonical Whisper dict**

Extract the authoritative set (guarantees accuracy — single source = whisper itself):
```bash
docker exec subgen-next python -c "from whisper.tokenizer import LANGUAGES; import json; print(json.dumps(LANGUAGES, ensure_ascii=False))" \
  || wsl docker exec subgen-next python -c "from whisper.tokenizer import LANGUAGES; import json; print(json.dumps(LANGUAGES, ensure_ascii=False))"
```
Title-case each value (`"galician"` → `"Galician"`, `"haitian creole"` → `"Haitian Creole"`). Add to `langs.py` after `_ISO3_TO_ISO1` (line 115). Reference copy (verify against the extraction; the extraction wins if they differ):

```python
# #358: the full set Whisper can detect (openai-whisper tokenizer.LANGUAGES),
# 2-letter code → English display name. Single source of truth for the manual
# picker (/api/languages), display, and Sonarr-name resolution.
WHISPER_LANGUAGES: dict[str, str] = {
    "en": "English", "zh": "Chinese", "de": "German", "es": "Spanish",
    "ru": "Russian", "ko": "Korean", "fr": "French", "ja": "Japanese",
    "pt": "Portuguese", "tr": "Turkish", "pl": "Polish", "ca": "Catalan",
    "nl": "Dutch", "ar": "Arabic", "sv": "Swedish", "it": "Italian",
    "id": "Indonesian", "hi": "Hindi", "fi": "Finnish", "vi": "Vietnamese",
    "he": "Hebrew", "uk": "Ukrainian", "el": "Greek", "ms": "Malay",
    "cs": "Czech", "ro": "Romanian", "da": "Danish", "hu": "Hungarian",
    "ta": "Tamil", "no": "Norwegian", "th": "Thai", "ur": "Urdu",
    "hr": "Croatian", "bg": "Bulgarian", "lt": "Lithuanian", "la": "Latin",
    "mi": "Maori", "ml": "Malayalam", "cy": "Welsh", "sk": "Slovak",
    "te": "Telugu", "fa": "Persian", "lv": "Latvian", "bn": "Bengali",
    "sr": "Serbian", "az": "Azerbaijani", "sl": "Slovenian", "kn": "Kannada",
    "et": "Estonian", "mk": "Macedonian", "br": "Breton", "eu": "Basque",
    "is": "Icelandic", "hy": "Armenian", "ne": "Nepali", "mn": "Mongolian",
    "bs": "Bosnian", "kk": "Kazakh", "sq": "Albanian", "sw": "Swahili",
    "gl": "Galician", "mr": "Marathi", "pa": "Punjabi", "si": "Sinhala",
    "km": "Khmer", "sn": "Shona", "yo": "Yoruba", "so": "Somali",
    "af": "Afrikaans", "oc": "Occitan", "ka": "Georgian", "be": "Belarusian",
    "tg": "Tajik", "sd": "Sindhi", "gu": "Gujarati", "am": "Amharic",
    "yi": "Yiddish", "lo": "Lao", "uz": "Uzbek", "fo": "Faroese",
    "ht": "Haitian Creole", "ps": "Pashto", "tk": "Turkmen", "nn": "Nynorsk",
    "mt": "Maltese", "sa": "Sanskrit", "lb": "Luxembourgish", "my": "Burmese",
    "bo": "Tibetan", "tl": "Tagalog", "mg": "Malagasy", "as": "Assamese",
    "tt": "Tatar", "haw": "Hawaiian", "ln": "Lingala", "ha": "Hausa",
    "ba": "Bashkir", "jw": "Javanese", "su": "Sundanese", "yue": "Cantonese",
}
```

- [ ] **Step 4: Add `to_iso3` (invert `_ISO3_TO_ISO1`, B-codes) and `display_name`**

Add below the table:
```python
# #358: 2-letter ISO-639-1 → 3-letter ISO-639-2/B, ONLY for the subgen-override
# boundary (subgen's SKIP_IF_AUDIO_LANGUAGES uses 3-letter B codes). Inverse of
# _ISO3_TO_ISO1, taking the bibliographic (B) code where B and T differ.
_ISO1_TO_ISO3B: dict[str, str] = {
    "en": "eng", "fr": "fre", "de": "ger", "es": "spa", "it": "ita",
    "pt": "por", "nl": "dut", "sv": "swe", "no": "nor", "nn": "nno",
    "da": "dan", "fi": "fin", "is": "isl", "ru": "rus", "uk": "ukr",
    "pl": "pol", "cs": "cze", "sk": "slo", "hr": "hrv", "sr": "srp",
    "bg": "bul", "sl": "slv", "el": "gre", "tr": "tur", "he": "heb",
    "ar": "ara", "fa": "per", "hi": "hin", "ko": "kor", "ja": "jpn",
    "zh": "chi", "th": "tha", "vi": "vie", "id": "ind", "ro": "rum",
    "hu": "hun", "ca": "cat", "gl": "glg", "zxx": "zxx",
}


def to_iso3(code: str | None) -> str:
    """ISO-639-1 (or any short code) → ISO-639-2/B 3-letter, for the subgen
    override. Falls back to the input lowercased if unmapped — never raises,
    never blanks. Idempotent: normalize_lang(to_iso3(x)) == normalize_lang(x)."""
    if not code:
        return code or ""
    c = str(code).strip().lower()
    return _ISO1_TO_ISO3B.get(c, c)


def display_name(code: str | None) -> str:
    """English display name from WHISPER_LANGUAGES; falls back to the code."""
    if not code:
        return code or ""
    c = str(code).strip().lower()
    return WHISPER_LANGUAGES.get(c, c)
```

- [ ] **Step 5: Run it green**

Run: `python -m pytest tests/test_langs_iso3_and_table.py -q`
Expected: PASS (5 tests). If `test_table_is_the_whisper_set_with_galician` fails on count, reconcile against the Step-3 extraction.

- [ ] **Step 6: Lint + commit**

Run: `python -m ruff check src/subarr/langs.py tests/test_langs_iso3_and_table.py`
```bash
git add src/subarr/langs.py tests/test_langs_iso3_and_table.py
git commit -m "feat(#358): WHISPER_LANGUAGES table + to_iso3/display_name helpers

Single source of truth for languages; to_iso3 produces 3-letter B codes only
at the subgen-override boundary. subgen format confirmed in Task 0: <finding>.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Store normalizes on write and read (`audio_lang_store.py`)

**Files:**
- Modify: `src/subarr/audio_lang_store.py` — import (line 37 area), `upsert` write (line 104), `get` (line 124-ish), `get_all_as_lookup` (line 159), `list_all` (line 188-ish); fix schema comment (line 13)
- Test: `tests/test_audio_lang_normalize.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""#358: the store is canonical 2-letter — normalize on write AND read so the
3-letter picker, Whisper's 2-letter, and legacy mixed rows all agree."""
from __future__ import annotations


def _store(tmp_path):
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "subarr.db"
    run_migrations(db)
    return AudioLangStore(db)


def test_upsert_normalizes_on_write(tmp_path):
    s = _store(tmp_path)
    s.upsert(canonical_path="Movies/Beasts.mkv", lang_code="glg", source="user")
    s.upsert(canonical_path="TV/a.mkv", lang_code="Galician", source="user")
    s.upsert(canonical_path="TV/b.mkv", lang_code="gl", source="whisper")
    assert s.get("Movies/Beasts.mkv").lang_code == "gl"
    assert s.get("TV/a.mkv").lang_code == "gl"
    assert s.get("TV/b.mkv").lang_code == "gl"  # idempotent


def test_read_paths_normalize_legacy_rows(tmp_path):
    # Simulate a pre-#358 row written raw 3-letter directly to the table.
    s = _store(tmp_path)
    import time
    with s._lock:
        s._conn.execute(
            "INSERT INTO audio_lang_verifications "
            "(canonical_path, lang_code, source, confidence, verified_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("x.mkv", "glg", "user", 1.0, time.time()),
        )
    assert s.get("x.mkv").lang_code == "gl"
    assert s.get_all_as_lookup()["x.mkv"] == "gl"
    assert s.list_all()[0].lang_code == "gl"
```

- [ ] **Step 2: Run it red**

Run: `python -m pytest tests/test_audio_lang_normalize.py -q`
Expected: FAIL — store returns `glg`, not `gl`.

- [ ] **Step 3: Import `normalize_lang` (with its first usage, to dodge the hook)**

Edit the import block and the `upsert` write tuple together so ruff sees the usage. Add at line 37:
```python
from .langs import normalize_lang
from .log_safe import scrub
```
And change the `upsert` value (line 104) `lang_code.lower(),` to:
```python
                    # #358: canonical 2-letter ISO-639-1 (was raw .lower()).
                    normalize_lang(lang_code) or lang_code.lower(),
```
(If the hook still strips the import because the edits land separately, make the `upsert` change first, then add the import.)

- [ ] **Step 4: Normalize the read paths**

In `get`, both `AudioLangVerification(... lang_code=row[1] ...)` and the series-intent branch `lang_code=lang` → wrap with `normalize_lang(...) or ...`:
```python
                lang_code=normalize_lang(row[1]) or row[1],
```
```python
                lang_code=normalize_lang(lang) or lang,
```
In `get_all_as_lookup`, change the return to:
```python
        return {r[0]: (normalize_lang(r[1]) or r[1]) for r in rows}
```
In `list_all`, change `lang_code=r[1],` to:
```python
                    lang_code=normalize_lang(r[1]) or r[1],
```

- [ ] **Step 5: Fix the stale schema comment (line 13)**

`-- 3-letter ISO 639-2/B` → `-- canonical 2-letter ISO-639-1 (#358; normalized on write/read)`.

- [ ] **Step 6: Run it green**

Run: `python -m pytest tests/test_audio_lang_normalize.py -q`
Expected: PASS (2 tests).

- [ ] **Step 7: Lint + commit**

Run: `python -m ruff check src/subarr/audio_lang_store.py tests/test_audio_lang_normalize.py`
```bash
git add src/subarr/audio_lang_store.py tests/test_audio_lang_normalize.py
git commit -m "feat(#358): audio-lang store is canonical 2-letter (normalize write+read)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: subgen-override boundary returns 3-letter (`resolve_audio_language_override`)

**Files:**
- Modify: `src/subarr/audio_lang_store.py` — `resolve_audio_language_override` final `return lang` (line ~511) and the `lang` derivation (line 462)
- Test: `tests/test_audio_lang_override_iso3.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""#358: the override forwarded to subgen is 3-letter ISO-639-2/B regardless of
how the audio language was stored (2-letter)."""
from __future__ import annotations


def _store(tmp_path):
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "subarr.db"
    run_migrations(db)
    return AudioLangStore(db)


def test_override_is_iso3_for_picker_and_whisper(tmp_path):
    from subarr.audio_lang_store import resolve_audio_language_override

    s = _store(tmp_path)
    # picker stored 'gl' (normalized) and Whisper stored 'ko' (2-letter)
    s.upsert(canonical_path="m.mkv", lang_code="gl", source="user", confidence=1.0)
    s.upsert(canonical_path="k.mkv", lang_code="ko", source="whisper", confidence=1.0)
    assert resolve_audio_language_override(s, "m.mkv") == "glg"
    assert resolve_audio_language_override(s, "k.mkv") == "kor"  # latent bug fixed


def test_override_still_skips_english(tmp_path):
    from subarr.audio_lang_store import resolve_audio_language_override

    s = _store(tmp_path)
    s.upsert(canonical_path="e.mkv", lang_code="en", source="user", confidence=1.0)
    assert resolve_audio_language_override(s, "e.mkv") is None
```

- [ ] **Step 2: Run it red**

Run: `python -m pytest tests/test_audio_lang_override_iso3.py -q`
Expected: FAIL — returns `gl`/`ko`, not `glg`/`kor`.

- [ ] **Step 3: Convert at the boundary**

`resolve_audio_language_override` already computes `lang = (verification.lang_code or "").strip().lower()` (line 462) and returns it (line 511). The English short-circuit at line 466 (`if not lang or lang in ("en", "eng")`) must run on the **2-letter** value, so keep `lang` 2-letter for the gate and convert only at return. Add the import at the top of `audio_lang_store.py` (it already imports `normalize_lang` from Task 2 — add `to_iso3` to that line):
```python
from .langs import normalize_lang, to_iso3
```
Then change the two `return lang` success paths (the risky-log branch and the normal branch both fall through to the final `return lang` at line 511) — change that final line to:
```python
    return to_iso3(lang)
```
Leave every `return None` and the logging (which logs the 2-letter `lang`) unchanged.

- [ ] **Step 4: Run the new test green**

Run: `python -m pytest tests/test_audio_lang_override_iso3.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the two regression tests that previously broke**

Run:
```bash
python -m pytest tests/test_progress_and_coverage_queue.py::test_coverage_queue_forwards_audio_language_override_when_verified tests/test_requeue.py::test_requeue_carries_audio_language_override -q
```
Expected: PASS. These seed `fre`; the store normalizes to `fr`; `to_iso3("fr")` returns `fre` — the asserted `audio_language_override == "fre"` round-trips.

- [ ] **Step 6: Lint + commit**

Run: `python -m ruff check src/subarr/audio_lang_store.py tests/test_audio_lang_override_iso3.py`
```bash
git add src/subarr/audio_lang_store.py tests/test_audio_lang_override_iso3.py
git commit -m "feat(#358): subgen override emits 3-letter ISO-639-2/B at the boundary

Fixes latent bug where Whisper-detected non-English overrides forwarded 2-letter.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Endpoint normalizes `req.lang_code` (`routers/audio_lang.py`)

**Files:**
- Modify: `src/subarr/routers/audio_lang.py` — `upsert_verification` (lines 50-91)
- Test: append to `tests/test_audio_lang_normalize.py`

- [ ] **Step 1: Write the failing test (append)**

```python
def test_verify_endpoint_normalizes_picker_code(app_with_stub):
    # POST the picker's 3-letter 'glg' → response + store both canonical 'gl'.
    canonical = "Movies/The Beasts/The Beasts (2022).mkv"
    r = app_with_stub.post(
        "/api/audio-lang/verifications",
        json={"canonical_path": canonical, "lang_code": "glg", "source": "user"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["lang_code"] == "gl"
    assert app_with_stub.app.state.audio_lang.get(canonical).lang_code == "gl"
```

- [ ] **Step 2: Run it red**

Run: `python -m pytest tests/test_audio_lang_normalize.py::test_verify_endpoint_normalizes_picker_code -q`
Expected: FAIL — response `lang_code` is `glg`.

- [ ] **Step 3: Normalize once at the top of the endpoint**

In `upsert_verification`, use a function-local import (keeps import+usage in one edit) and a local `lang`:
```python
async def upsert_verification(req: VerifyRequest, request: Request) -> dict[str, Any]:
    from ..langs import normalize_lang

    store = request.app.state.audio_lang
    # #358: 3-letter picker codes ('glg') → 2-letter canonical, used for the
    # store, the Sonarr propagation, and the response so all three agree.
    lang = normalize_lang(req.lang_code) or req.lang_code
    store.upsert(
        canonical_path=req.canonical_path,
        lang_code=lang,
        source=req.source,
        confidence=req.confidence,
        evidence=req.evidence,
    )
```
Change the propagation call arg `lang_code=req.lang_code,` (line 74) → `lang_code=lang,`.
Change the response `"lang_code": req.lang_code.lower(),` (line 89) → `"lang_code": lang,`.

- [ ] **Step 4: Run it green**

Run: `python -m pytest tests/test_audio_lang_normalize.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + commit**

Run: `python -m ruff check src/subarr/routers/audio_lang.py`
```bash
git add src/subarr/routers/audio_lang.py tests/test_audio_lang_normalize.py
git commit -m "feat(#358): verify endpoint normalizes lang_code for store+propagation+response

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `GET /api/languages` endpoint (new router)

**Files:**
- Create: `src/subarr/routers/languages.py`
- Modify: `src/subarr/app.py` — import (line 77 area) + `include_router` (line 987 area)
- Test: `tests/test_languages_endpoint.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""#358: the picker's data source — the full Whisper set, 2-letter native."""
from __future__ import annotations


def test_languages_endpoint_serves_full_set_sorted(app_with_stub):
    r = app_with_stub.get("/api/languages")
    assert r.status_code == 200, r.text
    data = r.json()["languages"]
    codes = {row["code"] for row in data}
    assert "gl" in codes and "ko" in codes and "en" in codes
    assert 95 <= len(data) <= 110
    # sorted by display name
    names = [row["name"] for row in data]
    assert names == sorted(names)
    gl = next(row for row in data if row["code"] == "gl")
    assert gl["name"] == "Galician"
```

- [ ] **Step 2: Run it red**

Run: `python -m pytest tests/test_languages_endpoint.py -q`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Create the router**

`src/subarr/routers/languages.py`:
```python
"""GET /api/languages — the full Whisper detection set for the manual
audio-language picker. Single source: langs.WHISPER_LANGUAGES (#358)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..langs import WHISPER_LANGUAGES

router = APIRouter(prefix="/api", tags=["languages"])


@router.get("/languages")
async def list_languages() -> dict[str, Any]:
    langs = sorted(
        ({"code": c, "name": n} for c, n in WHISPER_LANGUAGES.items()),
        key=lambda x: x["name"],
    )
    return {"count": len(langs), "languages": langs}
```

- [ ] **Step 4: Register it in `app.py`**

Add to the router-imports block near line 77 (alongside `coverage_actions`):
```python
    languages as r_languages,
```
Add near line 987 with the other includes:
```python
app.include_router(r_languages.router)
```

- [ ] **Step 5: Run it green**

Run: `python -m pytest tests/test_languages_endpoint.py -q`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

Run: `python -m ruff check src/subarr/routers/languages.py src/subarr/app.py tests/test_languages_endpoint.py`
```bash
git add src/subarr/routers/languages.py src/subarr/app.py tests/test_languages_endpoint.py
git commit -m "feat(#358): GET /api/languages serves the full Whisper set for the picker

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Sonarr-propagation coverage + honest degradation (`routers/audio_lang.py`)

**Files:**
- Modify: `src/subarr/routers/audio_lang.py` — `_iso_to_sonarr_name` (lines 232-94) and `_propagate_to_sonarr` no-match branch (line ~154)
- Test: `tests/test_audio_lang_propagation_coverage.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""#358: propagation resolves names from WHISPER_LANGUAGES (covers Galician +
the long tail) and degrades honestly when Sonarr can't represent a language."""
from __future__ import annotations

from subarr.routers.audio_lang import _iso_to_sonarr_name


def test_name_resolution_covers_galician_and_tail():
    # Previously absent from the curated map → fell back to the raw code.
    assert _iso_to_sonarr_name("gl") == "Galician"
    assert _iso_to_sonarr_name("glg") == "Galician"  # 3-letter also resolves
    assert _iso_to_sonarr_name("eu") == "Basque"
    # known short names still match Sonarr's spelling
    assert _iso_to_sonarr_name("el") == "Greek"
    assert _iso_to_sonarr_name("en") == "English"
```

- [ ] **Step 2: Run it red**

Run: `python -m pytest tests/test_audio_lang_propagation_coverage.py -q`
Expected: FAIL — `_iso_to_sonarr_name("gl")` returns `"gl"` (curated map lacks Galician).

- [ ] **Step 3: Rewrite `_iso_to_sonarr_name` over the single source**

Replace the whole curated-dict body with normalization + `display_name` + a small alias override for the handful where Whisper's English name differs from Sonarr's language name:
```python
def _iso_to_sonarr_name(code: str) -> str:
    """Map an audio-language code to Sonarr's language *name* for the
    episodeFile PUT. Sourced from WHISPER_LANGUAGES via display_name (#358 —
    covers the full set incl. Galician), with aliases where Whisper's English
    name differs from Sonarr's. Accepts 2- or 3-letter input."""
    from ..langs import display_name, normalize_lang

    iso1 = normalize_lang(code) or (code or "").strip().lower()
    # Whisper name → Sonarr name where they diverge.
    aliases = {
        "my": "Burmese",          # WHISPER_LANGUAGES already "Burmese"; explicit
        "ht": "Haitian",          # Sonarr: "Haitian" not "Haitian Creole"
        "nn": "Norwegian Nynorsk",  # Sonarr's spelling
    }
    return aliases.get(iso1, display_name(iso1))
```
(Adjust aliases to match your Sonarr's actual `/language` names if the live list in Step 5 shows a different spelling — the match in Step 4 is case-insensitive, so only true word differences need an alias.)

- [ ] **Step 4: Make the no-match degradation honest**

In `_propagate_to_sonarr`, the branch where `target is None` (line ~154) currently returns `detail: f"Sonarr has no language named {name!r}"`. Change it so the message tells the user the local work still applies:
```python
    if target is None:
        return {
            "attempted": True,
            "ok": False,
            "detail": (
                f"Sonarr can't represent {name!r} (not in its language list) — "
                "your local verification and the subgen override still apply; "
                "only the Bazarr courtesy-sync was skipped."
            ),
        }
```

- [ ] **Step 5: Run the new test green**

Run: `python -m pytest tests/test_audio_lang_propagation_coverage.py -q`
Expected: PASS. Then run the existing propagation suite for regressions:
Run: `python -m pytest tests/test_audio_lang_propagation.py -q`
Expected: PASS (if a test asserted the old `_iso_to_sonarr_name` returned a specific name that the alias map now spells differently, reconcile the alias to Sonarr's real name).

- [ ] **Step 6: Lint + commit**

Run: `python -m ruff check src/subarr/routers/audio_lang.py tests/test_audio_lang_propagation_coverage.py`
```bash
git add src/subarr/routers/audio_lang.py tests/test_audio_lang_propagation_coverage.py
git commit -m "feat(#358): propagation resolves names from WHISPER_LANGUAGES + honest degrade

Closes the silent Sonarr->Bazarr gap for Galician and the long tail.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Frontend — fetch the full set, retire both `LANG_PICKS` copies

**Files:**
- Create: `src/subarr/static/v1/home-hifi/languages.mjs`
- Create: `src/subarr/static/v1/home-hifi/__tests__/languages.test.js`
- Modify: `src/subarr/static/v1/home-hifi/coverage.jsx` (def at 1521; uses at 1322, 2022)
- Modify: `src/subarr/static/v1/home-hifi/review.jsx` (dup def at 21; use at 979)

- [ ] **Step 1: Write the failing test**

```javascript
// #358: the picker's language source — fetch once, cache, sorted full set.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchLanguages, _resetLanguagesCache } from '../languages.mjs';

describe('fetchLanguages', () => {
  beforeEach(() => { _resetLanguagesCache(); });

  it('fetches /api/languages and returns [code,name] pairs', async () => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ languages: [{ code: 'gl', name: 'Galician' }, { code: 'ko', name: 'Korean' }] }),
    }));
    const pairs = await fetchLanguages();
    expect(pairs).toEqual([['gl', 'Galician'], ['ko', 'Korean']]);
  });

  it('caches — second call does not re-fetch', async () => {
    const spy = vi.fn(async () => ({ ok: true, json: async () => ({ languages: [{ code: 'en', name: 'English' }] }) }));
    global.fetch = spy;
    await fetchLanguages();
    await fetchLanguages();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run it red**

Run: `npx vitest run src/subarr/static/v1/home-hifi/__tests__/languages.test.js`
Expected: FAIL — cannot resolve `../languages.mjs`.

- [ ] **Step 3: Create `languages.mjs`**

```javascript
// #358: single front-end source for the audio-language picker. Fetches the full
// Whisper set from GET /api/languages once and caches it (the set never changes
// within a session). Returns [code, name] pairs to match the old LANG_PICKS shape.
let _cache = null;
let _inflight = null;

export function _resetLanguagesCache() { _cache = null; _inflight = null; }

export async function fetchLanguages() {
  if (_cache) return _cache;
  if (_inflight) return _inflight;
  _inflight = (async () => {
    const r = await fetch('/api/languages');
    if (!r.ok) throw new Error(`/api/languages ${r.status}`);
    const data = await r.json();
    _cache = (data.languages || []).map((l) => [l.code, l.name]);
    _inflight = null;
    return _cache;
  })();
  return _inflight;
}
```

- [ ] **Step 4: Run it green**

Run: `npx vitest run src/subarr/static/v1/home-hifi/__tests__/languages.test.js`
Expected: PASS (2 tests).

- [ ] **Step 5: Wire `coverage.jsx` — load the list into state, render from it**

At the top of the component that owns the two `<select>`s (the one rendering line 1322 and the one at 2022 — confirm both are reachable; they may be in the same component or two), add state + an effect. Pattern (adapt to the existing component/hook style — these files use React via globals):
```javascript
const [langPicks, setLangPicks] = React.useState([]);
React.useEffect(() => {
  let alive = true;
  import('./languages.mjs').then(({ fetchLanguages }) =>
    fetchLanguages().then((p) => { if (alive) setLangPicks(p); }).catch(() => {})
  );
  return () => { alive = false; };
}, []);
```
Replace both `{LANG_PICKS.map(([c, n]) => ...)}` (lines 1322, 2022) with `{langPicks.map(([c, n]) => <option key={c} value={c}>{n} ({c})</option>)}`. Delete the `const LANG_PICKS = [ ... ]` block at line 1521.

(If the two selects live in different components, give each the same `langPicks` state+effect, or lift a shared hook `useLanguagePicks()` into `languages.mjs` exporting a tiny React hook. Prefer the shared hook to stay DRY.)

- [ ] **Step 6: Wire `review.jsx` — same treatment, delete its duplicate `LANG_PICKS`**

Add the same `langPicks` state+effect to the component owning the select at line 979; replace `{LANG_PICKS.map(([code, name]) => ( ... ))}` with the fetched `langPicks`; delete the duplicate `const LANG_PICKS = [ ... ]` at line 21.

- [ ] **Step 7: Verify no `LANG_PICKS` references remain**

Run: `grep -rn "LANG_PICKS" src/subarr/static/`
Expected: no output.

- [ ] **Step 8: Run the frontend suite**

Run: `npx vitest run`
Expected: PASS (existing + 2 new).

- [ ] **Step 9: Commit**

```bash
git add src/subarr/static/v1/home-hifi/languages.mjs \
        src/subarr/static/v1/home-hifi/__tests__/languages.test.js \
        src/subarr/static/v1/home-hifi/coverage.jsx \
        src/subarr/static/v1/home-hifi/review.jsx
git commit -m "feat(#358): picker fetches full Whisper set from /api/languages; retire LANG_PICKS

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Full verification + live-check + PR

**Files:** none (verification).

- [ ] **Step 1: Full backend suite**

Run: `python -m pytest -q`
Expected: all pass (≥ prior 1412 + the new tests), 6 skipped. Investigate any failure before proceeding — no green-washing.

- [ ] **Step 2: Lint the whole change**

Run: `python -m ruff check src/subarr/ tests/`
Expected: All checks passed.

- [ ] **Step 3: Frontend suite**

Run: `npx vitest run`
Expected: all pass.

- [ ] **Step 4: Live-verify on the dev box (:9923)**

Deploy the branch to the dev box per the repo's deploy-to-dev flow (fetch+check branch freshness first). In Claude-in-Chrome on `http://localhost:9923`: open a coverage/review row with a detected non-English audio (ideally a `gl` file), open the "Set the actual audio language" dropdown, confirm Galician (and the long tail) are present and selectable; pick it; confirm the row resolves and the stored value reads back as `gl`. Native `<select>` change needs the native value setter + a dispatched `change` event (a synthetic click won't fire onChange).

- [ ] **Step 5: Update issue + push + PR**

```bash
git push -u origin feat/358-audio-language-canonicalization
gh pr create --title "#358: canonical 2-letter audio-language model + complete picker" \
  --body "Closes #358. 2-letter ISO-639-1 canonical end-to-end; WHISPER_LANGUAGES single source; to_iso3 only at the subgen-override boundary (fixes latent 2-letter-to-subgen bug); full-Whisper-set picker via GET /api/languages; Sonarr->Bazarr propagation now covers Galician + the long tail with honest degradation. Spec: docs/superpowers/specs/2026-06-29-358-audio-language-canonicalization-design.md. Backend <N>✓ vitest <N>✓."
```

- [ ] **Step 6: Tier-2 pre-merge review**

This change touches writeback-to-arr, a cross-service (subgen) contract, and a data-model format — run the repo's Tier-2 review program (multi-lens + failure-mode subagents; controller triages) before merging. Do **not** self-merge without it.

---

## Self-Review notes (for the executor)

- **Spec coverage:** Task 1 = WHISPER_LANGUAGES/to_iso3/display_name (spec §1,§2); Task 2 = store 2-letter (spec §3); Task 3 = subgen boundary (spec §4); Task 4 = endpoint normalize (spec §5); Task 5 = /api/languages (spec §6); Task 6 = propagation coverage (spec §7); Task 7 = picker frontend (spec §6); Task 0 = the design-validation step. Bazarr direct-path alignment needs no code (verified in the spec). Migration = normalize-on-read (Task 2), no SQL.
- **Type consistency:** `to_iso3`, `display_name`, `WHISPER_LANGUAGES`, `normalize_lang` are the only language helpers; names are identical across Tasks 1/3/5/6.
- **Known soft spot:** the Step-3 reference `WHISPER_LANGUAGES` table is authored from memory — the docker extraction in Task 1 Step 3 is authoritative and the test guards count + spot-checks. Likewise the Task 6 Sonarr-name aliases must be reconciled against the *live* `/language` list (case-insensitive match means only genuine word-differences need an alias).
