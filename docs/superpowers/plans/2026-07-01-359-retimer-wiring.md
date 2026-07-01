# #359 Re-timer Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate the pure `subtitle_retime.retime_srt` (PR #401) in the completion pipeline behind a default-OFF flag — re-time each finished `.srt` in place before aftercare/upload, best-effort.

**Architecture:** A new `settings.retime_enabled` flag (default off) + a best-effort `CompletionWatcher._run_retime(entry)` inserted in `complete_entry` after `mark_completed`, before `_run_aftercare`. It reuses `_find_srt_sidecar`, reads the sidecar, runs `retime_srt`, and writes back only if the text changed. Failures never block completion.

**Tech Stack:** Python 3.11, pytest (pytest-asyncio strict — async tests need `@pytest.mark.asyncio`), ruff. Spec: `docs/superpowers/specs/2026-07-01-359-retimer-wiring-design.md`.

**Branch:** `feat/359-retimer-wiring` (already created, spec committed).

**Conventions:**
- TDD: failing test → run red → minimal impl → run green → commit. Run the specific test file until the final task.
- `Settings` is a `@dataclass(frozen=True)`; toggle a flag in tests with `object.__setattr__(settings, "retime_enabled", True)`.
- The conftest `subarr_env` fixture reloads `subarr.config` per test — config tests set env then `importlib.reload(config)`.
- Ruff PostToolUse hook strips a just-added top-level import if unused in the same edit — add the method (that uses `retime_srt`) first, then the import (or accept the transient F821 until the import lands).
- Commit footer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Config flag `retime_enabled` (default off)

**Files:**
- Modify: `src/subarr/config.py` — add the field (near the other bool fields ~line 91-116), the constructor parse (near line 266, next to `sonarr_propagate_audio_lang`), and the env-map entry (dict near line 469)
- Test: `tests/test_config_retime.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""#359: SUBARR_RETIME_ENABLED flag — default off, opt-in until the params are
arena-proven (then the tuning slice flips the default)."""
from __future__ import annotations

import importlib

from subarr import config


def test_retime_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv("SUBARR_RETIME_ENABLED", raising=False)
    importlib.reload(config)
    assert config.settings.retime_enabled is False


def test_retime_enabled_on_via_env(monkeypatch):
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "1")
    importlib.reload(config)
    assert config.settings.retime_enabled is True
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "true")
    importlib.reload(config)
    assert config.settings.retime_enabled is True
```

- [ ] **Step 2: Run red**

Run: `python -m pytest tests/test_config_retime.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'retime_enabled'`.

- [ ] **Step 3: Add the field**

In `src/subarr/config.py`, in the `Settings` dataclass alongside the other bool flags (e.g. right after `sonarr_propagate_audio_lang: bool`):
```python
    retime_enabled: bool
```

- [ ] **Step 4: Add the constructor parse**

Next to the `sonarr_propagate_audio_lang=...` line in the `Settings(...)` construction:
```python
        retime_enabled=os.environ.get("SUBARR_RETIME_ENABLED", "0").strip().lower()
        in ("1", "true", "yes", "on"),
```

- [ ] **Step 5: Add the env-map entry**

In the settings env-name map dict (near line 469, with `"sonarr_propagate_audio_lang": "SONARR_PROPAGATE_AUDIO_LANG",`):
```python
    "retime_enabled": "SUBARR_RETIME_ENABLED",
```

- [ ] **Step 6: Run green**

Run: `python -m pytest tests/test_config_retime.py -q`
Expected: PASS (2 tests).

- [ ] **Step 7: Lint + commit**

Run: `python -m ruff check src/subarr/config.py tests/test_config_retime.py`
```bash
git add src/subarr/config.py tests/test_config_retime.py
git commit -m "feat(#359): SUBARR_RETIME_ENABLED flag (default off)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `CompletionWatcher._run_retime(entry)`

**Files:**
- Modify: `src/subarr/completion_watcher.py` — add `from .subtitle_retime import retime_srt` to the top-level imports (with the other `from .xxx import` lines ~33-39) and the `_run_retime` method (place it right after `_run_aftercare`)
- Test: `tests/test_completion_retime.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""#359: _run_retime re-times the finished .srt in place, off by default,
best-effort (never blocks completion)."""
from __future__ import annotations

from pathlib import Path

from subarr.completion_watcher import CompletionWatcher

_HOT = (
    "1\n00:00:00,000 --> 00:00:02,000\n"
    "This is a very long translated line that crams far too many characters\n\n"
    "2\n00:00:20,000 --> 00:00:20,300\nhi\n"
)
_CALM = "1\n00:00:00,000 --> 00:00:03,000\nHello there.\n\n2\n00:00:04,000 --> 00:00:07,000\nGeneral Kenobi.\n"


class _Entry:
    id = 1
    canonical_path = "TV/Show/S01E01.mkv"
    source = "subgenscan"


def _watcher():
    return CompletionWatcher.__new__(CompletionWatcher)


def _enable(monkeypatch, on: bool):
    from subarr.config import settings

    object.__setattr__(settings, "retime_enabled", on)


def test_flag_off_leaves_sidecar_untouched(tmp_path, monkeypatch):
    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _enable(monkeypatch, False)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    w._run_retime(_Entry())
    assert srt.read_text(encoding="utf-8") == _HOT  # byte-for-byte unchanged


def test_flag_on_retimes_hot_sidecar(tmp_path, monkeypatch):
    from subarr.subtitle_readability import CRITICAL_CPS, analyze_srt

    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _enable(monkeypatch, True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    before = analyze_srt(_HOT)
    w._run_retime(_Entry())
    after = analyze_srt(srt.read_text(encoding="utf-8"))
    before_crit = sum(1 for i in before.issues if i.kind == "cps" and i.severity == "critical")
    after_crit = sum(1 for i in after.issues if i.kind == "cps" and i.severity == "critical")
    assert after_crit < before_crit
    assert after.counts.get("overlap", 0) == 0  # no new overlaps


def test_flag_on_comfortable_sidecar_not_rewritten(tmp_path, monkeypatch):
    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_CALM, encoding="utf-8")
    _enable(monkeypatch, True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    mtime_before = srt.stat().st_mtime_ns
    w._run_retime(_Entry())
    assert srt.stat().st_mtime_ns == mtime_before  # write-only-if-changed → no touch


def test_no_sidecar_is_noop(tmp_path, monkeypatch):
    _enable(monkeypatch, True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: None)
    w._run_retime(_Entry())  # must not raise


def test_retime_failure_never_raises(tmp_path, monkeypatch):
    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _enable(monkeypatch, True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    monkeypatch.setattr(
        "subarr.completion_watcher.retime_srt",
        lambda t: (_ for _ in ()).throw(ValueError("boom")),
    )
    w._run_retime(_Entry())  # best-effort: swallows the error
    assert srt.read_text(encoding="utf-8") == _HOT  # original preserved on failure
```

- [ ] **Step 2: Run red**

Run: `python -m pytest tests/test_completion_retime.py -q`
Expected: FAIL — `AttributeError: 'CompletionWatcher' object has no attribute '_run_retime'`.

- [ ] **Step 3: Implement the method**

Add `_run_retime` to `CompletionWatcher` (right after `_run_aftercare`):
```python
    def _run_retime(self, entry) -> None:
        """#359: re-time the produced .srt in place (extend over-CPS cues into
        the gap before the next cue) BEFORE aftercare + upload, so both see the
        improved sub. Off by default (SUBARR_RETIME_ENABLED). Best-effort — a
        failure here must NEVER block completion. Write only if changed."""
        from .config import settings as _settings

        if not _settings.retime_enabled:
            return
        try:
            srt_path = self._find_srt_sidecar(entry.canonical_path)
            if not srt_path:
                return
            text = Path(srt_path).read_text(encoding="utf-8", errors="replace")
            new_text = retime_srt(text)
            if new_text != text:
                Path(srt_path).write_text(new_text, encoding="utf-8")
                log.info("re-timed %s", entry.canonical_path)
        except Exception as e:  # noqa: BLE001 - re-timing must never break completion
            log.warning("re-time failed for %s: %s", getattr(entry, "canonical_path", "?"), e)
```

- [ ] **Step 4: Add the import**

Add to the top-level imports (with the `from .xxx import` block near lines 33-39):
```python
from .subtitle_retime import retime_srt
```
(If the ruff hook strips it because Step 3's method edit hasn't landed yet, just add it after Step 3 — the method uses it, so it stays.)

- [ ] **Step 5: Run green**

Run: `python -m pytest tests/test_completion_retime.py -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Lint + commit**

Run: `python -m ruff check src/subarr/completion_watcher.py tests/test_completion_retime.py`
```bash
git add src/subarr/completion_watcher.py tests/test_completion_retime.py
git commit -m "feat(#359): CompletionWatcher._run_retime — best-effort in-place re-time

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire `_run_retime` into `complete_entry` (before aftercare)

**Files:**
- Modify: `src/subarr/completion_watcher.py` — `complete_entry` (insert one line after `mark_completed`)
- Test: append to `tests/test_completion_retime.py`

- [ ] **Step 1: Write the failing ordering test**

```python
import types

import pytest


@pytest.mark.asyncio
async def test_complete_entry_retimes_before_aftercare_and_upload(monkeypatch):
    w = CompletionWatcher.__new__(CompletionWatcher)
    calls: list[str] = []
    w._provenance = types.SimpleNamespace(mark_completed=lambda i: calls.append("mark"))
    monkeypatch.setattr(w, "_run_retime", lambda e: calls.append("retime"))
    monkeypatch.setattr(w, "_run_aftercare", lambda e: calls.append("aftercare"))

    async def _up(e):
        calls.append("upload")
        return True

    async def _plex(p):
        calls.append("plex")

    monkeypatch.setattr(w, "_try_upload_to_bazarr", _up)
    monkeypatch.setattr(w, "_maybe_plex_partial_scan", _plex)
    entry = types.SimpleNamespace(id=1, canonical_path="TV/x.mkv", series_id=None, source="s")
    await w.complete_entry(entry)
    # re-time must run before aftercare reads the sidecar and before the upload.
    assert calls.index("retime") < calls.index("aftercare")
    assert calls.index("retime") < calls.index("upload")
```

- [ ] **Step 2: Run red**

Run: `python -m pytest tests/test_completion_retime.py::test_complete_entry_retimes_before_aftercare_and_upload -q`
Expected: FAIL — `ValueError: 'retime' is not in list` (KeyError/ValueError from `.index`) because `complete_entry` doesn't call `_run_retime` yet.

- [ ] **Step 3: Insert the call**

In `complete_entry`, add the line between `mark_completed` and `_run_aftercare`:
```python
        self._provenance.mark_completed(entry.id)
        self._run_retime(entry)
        self._run_aftercare(entry)
```

- [ ] **Step 4: Run green**

Run: `python -m pytest tests/test_completion_retime.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint + commit**

Run: `python -m ruff check src/subarr/completion_watcher.py`
```bash
git add src/subarr/completion_watcher.py tests/test_completion_retime.py
git commit -m "feat(#359): wire _run_retime into complete_entry (before aftercare)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Full verification + PR

**Files:** none (verification).

- [ ] **Step 1: Full suite + ruff**

Run: `python -m pytest -q && python -m ruff check src/subarr/ tests/`
Expected: all pass (prior total + 8 new); All checks passed. Investigate any failure before proceeding.

- [ ] **Step 2: Confirm default-off is truly inert**

Run: `python -m pytest tests/test_completion_retime.py tests/test_aftercare_completion.py -q`
Expected: pass — the flag-off path and existing aftercare behaviour are unchanged.

- [ ] **Step 3: Push + PR**

```bash
git push -u origin feat/359-retimer-wiring
gh pr create --title "#359: wire SRT re-timer into completion flow (default-off)" \
  --body "Second #359 slice — activates the PR #401 re-timer. New \`SUBARR_RETIME_ENABLED\` flag (default OFF; the tuning slice flips it on once params are proven). \`CompletionWatcher._run_retime\` runs in \`complete_entry\` after mark_completed, before aftercare — reads the sidecar, \`retime_srt\`, writes back only if changed. Best-effort (never blocks completion); no backup (transform is pure/idempotent/safe). With the flag off, completion is byte-for-byte identical to today. Deferred: corpus tuning that flips the default on, per-language params, the bake. Spec: docs/superpowers/specs/2026-07-01-359-retimer-wiring-design.md. <N> tests, ruff clean."
```

- [ ] **Step 4: Merge (Tier-1 — behind default-off flag, best-effort, pure transform)**

```bash
gh pr merge --squash --admin --delete-branch
git checkout main && git pull --ff-only
```

---

## Self-Review notes (for the executor)

- **Spec coverage:** Task 1 = config flag (spec §Components.1); Task 2 = `_run_retime` method + error handling (spec §Components.2, §Error handling, most of §Testing); Task 3 = wire into `complete_entry` + ordering (spec §Components.3, §Data flow, the ordering test); Task 4 = verify + ship (spec §Acceptance). Deferred items untasked — correct.
- **Type consistency:** `settings.retime_enabled` (bool), env `SUBARR_RETIME_ENABLED`, `_run_retime(entry)`, `retime_srt(text)` (from `subtitle_retime`, no params arg = `RetimeParams()` default), `_find_srt_sidecar(canonical_path)` — all match the existing code and each other.
- **Watch item:** the Task-3 ordering test stubs `_try_upload_to_bazarr` to return `True` so `complete_entry` skips `_trigger_bazarr_scan` (which only runs `if not uploaded and series_id is not None`); `series_id=None` also guards it. If `complete_entry` gains steps before `_run_retime` later, the ordering assertion still holds (it only checks relative order).
