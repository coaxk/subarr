# #157 Phase-1 Gap-Fill — Crash Visibility Finishing Touches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three remaining #157 Phase-1 observability gaps — a `SUBARR_DEBUG` verbose knob, task-health supervision of the opt-in audio-audit walker, and visibility of subarr's *own* logs (a recent-errors panel on the Health page plus a live SSE tail on the Logs page) — via an in-process bounded log ring.

**Architecture:** Additive throughout (Tier-1, no data-model change, no migration). Component A adds a `debug: bool` field to the frozen `Settings` dataclass and flips the module-level logging setup in `app.py` when it is on. Component C adds a new `log_ring.py` — a `logging.Handler` subclass wrapping a bounded `deque(maxlen)` plus an asyncio fan-out for SSE — installed on the root logger during `app.py`'s module-level logging setup (the same block Component A edits), and two read-only endpoints (`GET /api/logs/recent`, `GET /api/logs/subarr/events`) added to the existing `routers/logs.py`. Component B registers `("audio-audit", None)` in `task_health` at boot and wires `AudioAuditWalker._health` at construction, mirroring the 7 supervised loops' best-effort `getattr(self, "_health", None)` pattern at the RUN level. Component D adds a "Recent errors" panel to `health.jsx` and a `subgen | subarr` source switcher to `logs.jsx`, with the pure switch/format helpers extracted into a shared `.mjs` for vitest.

**Tech Stack:** Python 3.11 / FastAPI / Starlette `StreamingResponse` (SSE) / stdlib `logging` + `collections.deque` + `asyncio.Queue`; frontend vanilla React (esbuild bundles) + vitest; pytest + pytest-asyncio (STRICT mode) + `TestClient`.

---

## Ground truth (verified against current code — do not re-derive)

- **Logging is configured at MODULE level in `app.py`, NOT in `config.py` and NOT in the lifespan.** `src/subarr/app.py:126-133`:
  ```python
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
  # r/sonarr feedback: httpx/httpcore log EVERY request at INFO ...
  logging.getLogger("httpx").setLevel(logging.WARNING)
  logging.getLogger("httpcore").setLevel(logging.WARNING)
  log = logging.getLogger(__name__)
  ```
  This runs once at `import subarr.app`. The conftest `subarr_env` fixture `importlib.reload(app_mod)`s it, so it re-runs per app-test — the install must be idempotent (installing the same handler twice would double-emit; guard on a sentinel).
- **`Settings` is `@dataclass(frozen=True)`** (`config.py:51`). `config.load()` builds it; `config.settings = load()` is the module singleton (`config.py:558`). The `retime_enabled` env-parse pattern (`config.py:281-282`) is: `os.environ.get("SUBARR_RETIME_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")`. `SUBARR_DEBUG` mirrors it with default `"0"`.
- **`task_health` boot registration** (`app.py:352-364`) is a `for _tname, _tiv in (...)` loop calling `app_.state.task_health.register(_tname, expected_interval_s=_tiv)`. `app_.state.task_health` is created at `app.py:323`: `TaskHealthStore(settings.db_path, crash_recorder=app_.state.crashes.record)`.
- **`TaskHealthStore.register(task_name, *, expected_interval_s=None)`**, `record_success(task_name, *, expected_interval_s=None)`, `record_failure(task_name, exc, *, expected_interval_s=None)` — all best-effort (swallow exceptions to `log.debug`). `TaskHealth.is_unhealthy` staleness branch (`task_health.py:81-83`) only fires `if self.expected_interval_s and self.last_success_at is not None` — so `expected_interval_s=None` NEVER goes stale. Confirmed there is currently **no** `_health` attribute on `AudioAuditWalker`.
- **The 7 loops' pattern** (e.g. `scheduler.py:171-177`):
  ```python
  _h = getattr(self, "_health", None)  # #157 supervision hook
  if _h:
      _h.record_success("scheduler", expected_interval_s=self._tick_s)
  # ...on except:
  _h = getattr(self, "_health", None)
  if _h:
      _h.record_failure("scheduler", e, expected_interval_s=self._tick_s)
  ```
- **`AudioAuditWalker` run driver** = `_run(self, state, worklist)` (`audio_audit.py:207-260`). Clean completion sets `state.status = "done"` (line 244); run-level crash is caught at `audio_audit.py:256-260` (`except Exception as e:` → `log.exception(...)` → `state.status = "error"`). Per-file errors accumulate at `audio_audit.py:237` (`state.errors.append(...)`) and MUST stay unchanged. `_run` is `create_task`'d by `start()` (`audio_audit.py:174`).
- **`AudioAuditWalker` construction** (`app.py:594-601`) — `_health` assignment goes immediately after, mirroring `app.py:456` etc.
- **`routers/logs.py`** — `router = APIRouter(prefix="/api", tags=["logs"])`; the existing subgen SSE endpoint is `@router.get("/logs/events")` returning `StreamingResponse(gen(), media_type="text/event-stream")` where `gen()` yields `f"event: log\ndata: {json.dumps(line)}\n\n"`. Registered at `app.py:995` (`app.include_router(logs.router)`). So the new endpoints live at **`/api/logs/recent`** and **`/api/logs/subarr/events`**.
- **`logs.jsx`** consumes the SSE via `new EventSource('/api/logs/events')` (line 52), listening on the named `'log'` event (line 86) and a `'stream_error'` event (line 57). `SOURCES = ['subgen']` (line 30); `classifyLine()` (line 18) tags every line `source: 'subgen'`. The switcher replaces the single-source EventSource wiring.
- **`health.jsx`** — `HealthPage()` (line 295) fetches `/api/health/tasks` on an 8s interval; the task roster renders inside a `<section style={cardStyle}>` (line 338-355). The "Recent errors" panel slots as a new `<section>` below it, before `<DbMaintenance/>` (line 357).
- **Frontend tests** live in `src/subarr/static/v1/home-hifi/__tests__/*.test.js` and import pure helpers either from a dedicated `../<name>.mjs` (e.g. `instance-health-util.mjs`) or directly from `../<name>.jsx` (e.g. `../coverage.jsx`). `package.json` scripts: `test:frontend` = `vitest run`, `build:frontend` = `node scripts/build-frontend.mjs`, `check:frontend` builds then `git diff --exit-code` on `*.bundle.js` + `*.html`.
- **`tests/test_audio_audit.py` / `test_audio_audit_multilingual.py` / `test_audio_audit_router.py` DO exist** (walker harness: `_FakeSubgen`, `_store` = `run_migrations` + `AudioAuditStore`, `worklist=lambda: [...]`, `await w.start(); await w._task`). Component B's test deliberately uses a lighter self-contained `_FakeHealth` + `_FakeStore` (no DB) since it only needs to observe the run-level success/failure hooks — verified the `_walker` constructor kwargs match the real `AudioAuditWalker.__init__` (`subgen, audit_store, *, worklist, probe_store, busy_check, audio_lang, to_subgen`). No existing `tests/test_task_health*.py`.

## File Structure

| File | Create / Modify | Responsibility |
|------|-----------------|----------------|
| `src/subarr/config.py` | Modify | Add `debug: bool` field + `SUBARR_DEBUG` env-parse in `load()`. |
| `src/subarr/log_ring.py` | **Create** | `LogRing(logging.Handler)`: bounded deque of structured records, `snapshot(level, limit)`, asyncio subscribe/unsubscribe fan-out; exception-proof `emit`. |
| `src/subarr/app.py` | Modify | (1) `debug`-aware logging setup + install the `LogRing` root handler idempotently; (2) register `("audio-audit", None)`; (3) wire `walker._health`. |
| `src/subarr/audio_audit.py` | Modify | RUN-level `record_success`/`record_failure` in `_run`, best-effort via `getattr(self, "_health", None)`. |
| `src/subarr/routers/logs.py` | Modify | Add `GET /logs/recent` (snapshot) + `GET /logs/subarr/events` (SSE), tolerating a missing ring. |
| `src/subarr/static/v1/home-hifi/log-helpers.mjs` | **Create** | Pure helpers `formatRecentRow(rec)` + `nextLogSource(current)` for vitest + jsx reuse. |
| `src/subarr/static/v1/home-hifi/health.jsx` | Modify | "Recent errors" panel fed by `/api/logs/recent?level=WARNING`. |
| `src/subarr/static/v1/home-hifi/logs.jsx` | Modify | `subgen | subarr` source switcher; connect `/api/logs/subarr/events` for subarr. |
| `tests/test_config_debug.py` | **Create** | `SUBARR_DEBUG` env-parse (mirror `test_config_retime`). |
| `tests/test_log_ring.py` | **Create** | LogRing unit tests (capture, cap, filter, limit, exception-proof emit, subscriber). |
| `tests/test_logs_recent.py` | **Create** | `/api/logs/recent` snapshot filter + SSE smoke via `app_with_stub`. |
| `tests/test_audio_audit_health.py` | **Create** | Walker RUN-level success/failure with a fake `_health`; per-file errors unchanged; missing `_health` never raises. |
| `src/subarr/static/v1/home-hifi/__tests__/log-helpers.test.js` | **Create** | vitest for `formatRecentRow` + `nextLogSource`. |

---

## Component A — `SUBARR_DEBUG` verbose knob

### Task 1: `debug` field + `SUBARR_DEBUG` env-parse in config

**Files:**
- Modify: `src/subarr/config.py` (add field to `Settings` dataclass; add parse in `load()`)
- Test: `tests/test_config_debug.py` (Create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_debug.py`:

```python
"""#157 gap-fill: SUBARR_DEBUG verbose knob. Off by default (today's INFO
behaviour byte-for-byte); on via 1/true/yes/on. Mirrors test_config_retime."""

from __future__ import annotations

import importlib

from subarr import config


def test_debug_defaults_off(monkeypatch):
    monkeypatch.delenv("SUBARR_DEBUG", raising=False)
    importlib.reload(config)
    assert config.settings.debug is False


def test_debug_off_via_blank(monkeypatch):
    # A blank line in .env (SUBARR_DEBUG=) must count as off, not on.
    monkeypatch.setenv("SUBARR_DEBUG", "")
    importlib.reload(config)
    assert config.settings.debug is False


def test_debug_on_via_env(monkeypatch):
    monkeypatch.setenv("SUBARR_DEBUG", "1")
    importlib.reload(config)
    assert config.settings.debug is True
    monkeypatch.setenv("SUBARR_DEBUG", "true")
    importlib.reload(config)
    assert config.settings.debug is True
    monkeypatch.setenv("SUBARR_DEBUG", "on")
    importlib.reload(config)
    assert config.settings.debug is True


def test_debug_off_via_env(monkeypatch):
    monkeypatch.setenv("SUBARR_DEBUG", "0")
    importlib.reload(config)
    assert config.settings.debug is False
    monkeypatch.setenv("SUBARR_DEBUG", "false")
    importlib.reload(config)
    assert config.settings.debug is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_debug.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'debug'` (the field does not exist yet).

- [ ] **Step 3: Add the field to the `Settings` dataclass**

In `src/subarr/config.py`, add the field inside the `@dataclass(frozen=True) class Settings:` block. Place it right after the `retime_enabled: bool` field (`config.py:106`) and its comment:

```python
    # #157 gap-fill: verbose logging knob. When on, the root logger goes to
    # DEBUG and the httpx/httpcore request loggers are UN-pinned from WARNING so
    # request detail shows. Default off = today's INFO behaviour byte-for-byte.
    debug: bool
```

- [ ] **Step 4: Parse it in `load()`**

In `src/subarr/config.py`, inside the `Settings(...)` constructor call in `load()`, add the parse right after the `retime_enabled=...` line (`config.py:281-282`), mirroring its exact idiom:

```python
        # #157 gap-fill: SUBARR_DEBUG verbose knob. Off by default; the logging
        # setup (app.py) reads settings.debug to raise the root level to DEBUG.
        debug=os.environ.get("SUBARR_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on"),
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_config_debug.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Format + commit**

```bash
cd /c/Projects/subarr
ruff format tests/test_config_debug.py
ruff check src/subarr/config.py tests/test_config_debug.py
git add src/subarr/config.py tests/test_config_debug.py
git commit -m "feat(#157): SUBARR_DEBUG config flag (verbose logging knob)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 2: debug-aware logging setup in app.py

**Files:**
- Modify: `src/subarr/app.py:126-133` (the module-level logging block)
- Test: covered by an assertion appended to `tests/test_config_debug.py` (logging responds to the flag)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config_debug.py`:

```python
def test_apply_logging_debug_on_raises_root_and_unpins_httpx(monkeypatch):
    import logging

    from subarr import app as app_mod

    importlib.reload(config)  # ensure settings singleton is fresh
    # Restore INFO baseline first so the assertion isn't order-dependent.
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app_mod._apply_logging(debug=True)
    assert logging.getLogger().level == logging.DEBUG
    # httpx un-pinned (left at INFO, not WARNING) so request detail shows.
    assert logging.getLogger("httpx").level == logging.INFO
    assert logging.getLogger("httpcore").level == logging.INFO


def test_apply_logging_debug_off_is_todays_behaviour(monkeypatch):
    import logging

    from subarr import app as app_mod

    app_mod._apply_logging(debug=False)
    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_debug.py -q`
Expected: FAIL — `AttributeError: module 'subarr.app' has no attribute '_apply_logging'`.

- [ ] **Step 3: Refactor the module-level logging block into `_apply_logging`**

In `src/subarr/app.py`, replace the current block (`app.py:126-133`):

```python
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# r/sonarr feedback: httpx/httpcore log EVERY request at INFO, so the health/
# queue polls to subgen/sonarr/radarr/bazarr/tautulli/plex flood the info log
# with "200 OK" lines and bury real signal. Pin them to WARNING — routine
# request success is debug-level detail, not an info event.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger(__name__)
```

with:

```python
def _apply_logging(debug: bool) -> None:
    """#157 gap-fill: configure logging levels from the SUBARR_DEBUG knob.

    Off (default): today's behaviour byte-for-byte — root INFO, and the
    httpx/httpcore request loggers pinned to WARNING so the health/queue polls
    to subgen/sonarr/radarr/bazarr/tautulli/plex don't flood the log with
    "200 OK" lines and bury real signal.

    On: root -> DEBUG and httpx/httpcore UN-pinned (left at INFO) so request
    detail shows for "go nuts locally" debugging.
    """
    root_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=root_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # basicConfig is a no-op once a handler exists (e.g. after importlib.reload),
    # so set the root level explicitly too.
    logging.getLogger().setLevel(root_level)
    http_level = logging.INFO if debug else logging.WARNING
    logging.getLogger("httpx").setLevel(http_level)
    logging.getLogger("httpcore").setLevel(http_level)


_apply_logging(settings.debug)
log = logging.getLogger(__name__)
```

Note: `settings` is already imported at the top of `app.py` (it references `settings.db_path` etc. throughout). Confirm the `from .config import settings` import exists above this block; it does (the module uses `settings` extensively). If a linter flags an ordering issue, keep `_apply_logging(settings.debug)` immediately after the `def`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_config_debug.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Regression — the app still imports and boots**

Run: `python -m pytest tests/test_config_debug.py tests/test_config_retime.py -q`
Expected: PASS. (Confirms the `app.py` refactor didn't break import.)

- [ ] **Step 6: Format + commit**

```bash
cd /c/Projects/subarr
ruff format tests/test_config_debug.py
ruff check src/subarr/app.py tests/test_config_debug.py
git add src/subarr/app.py tests/test_config_debug.py
git commit -m "feat(#157): apply SUBARR_DEBUG to logging (root DEBUG + unpin httpx)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Component C core — the in-process `LogRing`

### Task 3: `LogRing` handler — bounded deque, snapshot, exception-proof emit

**Files:**
- Create: `src/subarr/log_ring.py`
- Test: `tests/test_log_ring.py` (Create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_log_ring.py`:

```python
"""#157 gap-fill: LogRing — a bounded, exception-proof in-process logging
handler feeding /api/logs/recent (snapshot) and /api/logs/subarr/events (SSE)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from subarr.log_ring import LogRing


def _record(name="subarr.test", level=logging.INFO, msg="hello", args=()):
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1, msg=msg, args=args, exc_info=None
    )


def test_captures_structured_record():
    ring = LogRing(maxlen=10)
    ring.emit(_record(msg="a message"))
    snap = ring.snapshot()
    assert len(snap) == 1
    rec = snap[0]
    assert rec["message"] == "a message"
    assert rec["level"] == "INFO"
    assert rec["logger_name"] == "subarr.test"
    assert "ts" in rec and isinstance(rec["ts"], float)
    assert rec["exc_text"] is None


def test_caps_at_maxlen():
    ring = LogRing(maxlen=3)
    for i in range(5):
        ring.emit(_record(msg=f"m{i}"))
    snap = ring.snapshot()
    assert len(snap) == 3
    assert [r["message"] for r in snap] == ["m2", "m3", "m4"]  # oldest dropped


def test_snapshot_filters_by_level():
    ring = LogRing(maxlen=10)
    ring.emit(_record(level=logging.INFO, msg="info line"))
    ring.emit(_record(level=logging.WARNING, msg="warn line"))
    ring.emit(_record(level=logging.ERROR, msg="err line"))
    warn_plus = ring.snapshot(level="WARNING")
    assert [r["message"] for r in warn_plus] == ["warn line", "err line"]
    assert ring.snapshot(level="ERROR") == [r for r in warn_plus if r["level"] == "ERROR"]


def test_snapshot_honours_limit():
    ring = LogRing(maxlen=10)
    for i in range(6):
        ring.emit(_record(msg=f"m{i}"))
    snap = ring.snapshot(limit=2)
    assert [r["message"] for r in snap] == ["m4", "m5"]  # newest 2, chronological


def test_emit_never_raises_on_bad_record():
    ring = LogRing(maxlen=10)
    # %-format mismatch: msg has a placeholder but no args -> getMessage() raises.
    bad = _record(msg="oops %s", args=())
    bad.args = ("only",)  # ok
    good_count_before = len(ring.snapshot())
    # Now a genuinely broken record: msg wants an int arg but gets a dict.
    broken = _record(msg="%d", args=({"not": "an int"},))
    ring.emit(broken)  # must NOT raise
    # The record is dropped (or stored best-effort) but emit returned cleanly.
    assert len(ring.snapshot()) >= good_count_before


def test_captures_exc_text_when_present():
    ring = LogRing(maxlen=10)
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = logging.LogRecord(
            name="subarr.test", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="failed", args=(), exc_info=sys.exc_info(),
        )
    ring.emit(rec)
    snap = ring.snapshot()
    assert snap[0]["exc_text"] is not None
    assert "ValueError: boom" in snap[0]["exc_text"]


@pytest.mark.asyncio
async def test_subscriber_receives_new_record():
    ring = LogRing(maxlen=10)
    q = ring.subscribe()
    try:
        ring.emit(_record(msg="live line"))
        rec = await asyncio.wait_for(q.get(), timeout=1.0)
        assert rec["message"] == "live line"
    finally:
        ring.unsubscribe(q)


@pytest.mark.asyncio
async def test_slow_subscriber_drops_oldest_not_backpressure():
    ring = LogRing(maxlen=100, subscriber_maxsize=2)
    q = ring.subscribe()
    try:
        for i in range(5):
            ring.emit(_record(msg=f"m{i}"))  # never blocks the handler
        # Queue capped at 2: it holds the NEWEST 2, oldest silently dropped.
        got = [await asyncio.wait_for(q.get(), timeout=1.0) for _ in range(2)]
        assert [r["message"] for r in got] == ["m3", "m4"]
        assert q.empty()
    finally:
        ring.unsubscribe(q)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_log_ring.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'subarr.log_ring'`.

- [ ] **Step 3: Write the implementation**

Create `src/subarr/log_ring.py`:

```python
"""#157 gap-fill: an in-process, bounded, exception-proof logging handler.

Feeds two read surfaces of subarr's OWN logs:
  * GET /api/logs/recent          — a filtered snapshot (Health "Recent errors")
  * GET /api/logs/subarr/events   — a live SSE tail (Logs page, source: subarr)

Design constraints (see the #157 gap-fill spec, "Error handling"):
  * `emit` must NEVER raise into logging — a throwing handler breaks every log
    call in the process. Everything inside emit is wrapped; a bad record is
    dropped, never propagated.
  * Bounded memory: the ring is a `deque(maxlen)`, never persisted. Ephemeral +
    local, so no privacy/transmit cost (issue #157 transmit-boundary principle).
  * Live subscribers get a BOUNDED per-subscriber queue; a slow consumer drops
    its oldest event rather than back-pressuring the handler (which would stall
    logging for the whole app).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

# Default ring capacity (bounded memory; a ring, never persisted).
_DEFAULT_MAXLEN = 1000
# Default per-subscriber SSE queue size. Small: a live tail that falls behind
# should drop, not stall the handler.
_DEFAULT_SUBSCRIBER_MAXSIZE = 500

_LEVEL_ORDER = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class LogRing(logging.Handler):
    """A logging.Handler holding a bounded deque of structured records plus an
    asyncio fan-out for live SSE subscribers."""

    def __init__(
        self,
        *,
        maxlen: int = _DEFAULT_MAXLEN,
        subscriber_maxsize: int = _DEFAULT_SUBSCRIBER_MAXSIZE,
        level: int = logging.INFO,
    ) -> None:
        super().__init__(level=level)
        self._buf: deque[dict] = deque(maxlen=maxlen)
        self._subscriber_maxsize = subscriber_maxsize
        # Live SSE subscribers. Each is a bounded asyncio.Queue[dict].
        self._subscribers: list[asyncio.Queue] = []

    # ── logging.Handler interface ─────────────────────────────────────────
    def emit(self, record: logging.LogRecord) -> None:
        """Store a structured view of the record + fan it out. NEVER raises."""
        try:
            rec = self._to_dict(record)
        except Exception:
            # A malformed record must not break logging. Drop it.
            return
        try:
            self._buf.append(rec)
        except Exception:
            return
        # Fan out to live subscribers (best-effort; a full queue drops oldest).
        for q in list(self._subscribers):
            self._offer(q, rec)

    def _to_dict(self, record: logging.LogRecord) -> dict:
        # record.getMessage() applies %-args and can raise on a mismatch;
        # that's caught by emit's try/except, so a broken record is dropped.
        exc_text = None
        if record.exc_info:
            try:
                exc_text = logging.Formatter().formatException(record.exc_info)
            except Exception:
                exc_text = None
        return {
            "ts": record.created if getattr(record, "created", None) else time.time(),
            "level": record.levelname,
            "logger_name": record.name,
            "message": record.getMessage(),
            "exc_text": exc_text,
        }

    # ── snapshot (GET /api/logs/recent) ───────────────────────────────────
    def snapshot(self, *, level: str | None = None, limit: int | None = None) -> list[dict]:
        """The ring, oldest-first, optionally filtered to `level` and above, and
        tailed to the newest `limit`. Returns copies so callers can't mutate the
        ring. Never raises."""
        try:
            records = list(self._buf)
        except Exception:
            return []
        if level:
            threshold = _LEVEL_ORDER.get(level.upper())
            if threshold is not None:
                records = [
                    r for r in records if _LEVEL_ORDER.get(r.get("level", "INFO"), logging.INFO) >= threshold
                ]
        if limit is not None and limit >= 0:
            records = records[-limit:]
        return [dict(r) for r in records]

    # ── live fan-out (GET /api/logs/subarr/events) ────────────────────────
    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE subscriber; returns its bounded queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=self._subscriber_maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _offer(self, q: asyncio.Queue, rec: dict) -> None:
        """Enqueue without blocking. If the subscriber is full, drop its oldest
        so a slow consumer never back-pressures the handler."""
        try:
            q.put_nowait(rec)
        except asyncio.QueueFull:
            try:
                q.get_nowait()  # drop oldest
                q.put_nowait(rec)
            except Exception:
                pass
        except Exception:
            pass
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_log_ring.py -q`
Expected: PASS (8 passed).

Note on `test_emit_never_raises_on_bad_record`: `record.getMessage()` on `msg="%d"` with a dict arg raises `TypeError`, which is caught inside `emit`'s outer try (via `_to_dict`), so the record is dropped and `emit` returns cleanly — the assertion `len(snapshot()) >= good_count_before` holds.

- [ ] **Step 5: Format + commit**

```bash
cd /c/Projects/subarr
ruff format src/subarr/log_ring.py tests/test_log_ring.py
ruff check src/subarr/log_ring.py tests/test_log_ring.py
git add src/subarr/log_ring.py tests/test_log_ring.py
git commit -m "feat(#157): LogRing — bounded exception-proof in-process log handler

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Component C install + endpoints

### Task 4: install LogRing on the root logger + `/api/logs/recent` + `/api/logs/subarr/events`

**Files:**
- Modify: `src/subarr/app.py` (install the handler in `_apply_logging`; expose it for the router)
- Modify: `src/subarr/routers/logs.py` (two new endpoints)
- Test: `tests/test_logs_recent.py` (Create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_logs_recent.py`:

```python
"""#157 gap-fill: /api/logs/recent (snapshot) + /api/logs/subarr/events (SSE).
Sync TestClient tests (app_with_stub) — NOT async: the endpoints are exercised
through the running app, not called directly."""

from __future__ import annotations

import logging


def test_recent_returns_seeded_records_filtered_by_level(app_with_stub):
    # The LogRing is installed on the root logger at import; emit through it.
    logging.getLogger("subarr.test").info("an info line for recent")
    logging.getLogger("subarr.test").warning("a warning line for recent")
    logging.getLogger("subarr.test").error("an error line for recent")

    r = app_with_stub.get("/api/logs/recent?level=WARNING&limit=200")
    assert r.status_code == 200
    body = r.json()
    assert "records" in body
    msgs = [rec["message"] for rec in body["records"]]
    assert "a warning line for recent" in msgs
    assert "an error line for recent" in msgs
    assert "an info line for recent" not in msgs  # filtered out below WARNING
    for rec in body["records"]:
        assert set(rec) >= {"ts", "level", "logger_name", "message", "exc_text"}


def test_recent_default_no_level_returns_all(app_with_stub):
    logging.getLogger("subarr.test").info("unfiltered info line")
    r = app_with_stub.get("/api/logs/recent?limit=500")
    assert r.status_code == 200
    msgs = [rec["message"] for rec in r.json()["records"]]
    assert "unfiltered info line" in msgs


def test_recent_tolerates_missing_ring(app_with_stub, monkeypatch):
    # If the app has no ring wired, the endpoint returns an empty list, not 500.
    import subarr.app as app_mod

    monkeypatch.setattr(app_mod, "LOG_RING", None, raising=False)
    r = app_with_stub.get("/api/logs/recent")
    assert r.status_code == 200
    assert r.json()["records"] == []


def test_subarr_events_sse_opens_and_replays_tail(app_with_stub):
    logging.getLogger("subarr.test").warning("tail replay line")
    # A streaming GET: read the first chunk then close (TestClient supports
    # stream=... via the httpx client under the hood).
    with app_with_stub.stream("GET", "/api/logs/subarr/events?tail=50") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        chunk = next(resp.iter_lines())
        # The replay emits at least the SSE framing.
        assert chunk is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logs_recent.py -q`
Expected: FAIL — 404 on `/api/logs/recent` (endpoint not defined yet).

- [ ] **Step 3: Install the ring in `_apply_logging` + expose it**

In `src/subarr/app.py`, extend the `_apply_logging` function from Task 2 so it also installs a module-level `LOG_RING` on the root logger, idempotently (the conftest reloads `app.py`, so guard against double-install). First add the import near the other local imports at the top of `app.py` (alongside `from .task_health import TaskHealthStore`):

```python
from .log_ring import LogRing
```

Then add the module-level global and extend `_apply_logging`. Replace the `_apply_logging` body's final lines and the call site so it reads:

```python
# #157 gap-fill: the process-wide in-process log ring. Installed on the root
# logger by _apply_logging; the logs router reads it. None until installed.
LOG_RING: LogRing | None = None


def _apply_logging(debug: bool) -> None:
    """#157 gap-fill: configure logging levels from the SUBARR_DEBUG knob AND
    install the in-process LogRing on the root logger (idempotent).

    Off (default): today's behaviour byte-for-byte — root INFO, and the
    httpx/httpcore request loggers pinned to WARNING so the health/queue polls
    don't flood the log with "200 OK" lines and bury real signal.

    On: root -> DEBUG and httpx/httpcore UN-pinned (left at INFO).
    """
    global LOG_RING
    root_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=root_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger().setLevel(root_level)
    http_level = logging.INFO if debug else logging.WARNING
    logging.getLogger("httpx").setLevel(http_level)
    logging.getLogger("httpcore").setLevel(http_level)

    # Install the LogRing exactly once (importlib.reload re-runs this module).
    root = logging.getLogger()
    if not any(isinstance(h, LogRing) for h in root.handlers):
        LOG_RING = LogRing(level=logging.INFO)  # capture INFO+ so the live tail is useful
        root.addHandler(LOG_RING)
    else:
        LOG_RING = next(h for h in root.handlers if isinstance(h, LogRing))


_apply_logging(settings.debug)
log = logging.getLogger(__name__)
```

- [ ] **Step 4: Add the two endpoints to the logs router**

In `src/subarr/routers/logs.py`, add the imports and the two endpoints. The file currently imports `asyncio`, `json`, `APIRouter`, `Query`, `Request`, `StreamingResponse`, `DockerUnavailable`, `safe_error`. Add these endpoints below the existing `logs_events`:

```python
def _ring():
    """The process-wide LogRing, or None. Imported lazily so a missing/absent
    ring (early boot, tests that clear it) is tolerated, not a hard import error."""
    from .. import app as app_mod

    return getattr(app_mod, "LOG_RING", None)


@router.get("/logs/recent")
async def logs_recent(
    level: str | None = Query(None),
    limit: int = Query(200, ge=0, le=5000),
) -> dict:
    """#157 gap-fill: a snapshot of subarr's OWN recent log records, optionally
    filtered to `level` and above. Read-only; tolerates a missing ring."""
    ring = _ring()
    if ring is None:
        return {"records": []}
    return {"records": ring.snapshot(level=level, limit=limit)}


@router.get("/logs/subarr/events")
async def logs_subarr_events(
    request: Request, tail: int = Query(200, ge=0, le=5000)
) -> StreamingResponse:
    """#157 gap-fill: live SSE tail of subarr's OWN log. Replays the last `tail`
    records then streams new ones, mirroring the subgen /logs/events shape."""
    ring = _ring()

    async def gen():
        if ring is None:
            # Nothing to stream (early boot / no ring). Close cleanly.
            return
        # Replay the tail as an initial burst.
        for rec in ring.snapshot(limit=tail):
            yield f"event: log\ndata: {json.dumps(rec)}\n\n"
        q = ring.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    rec = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Heartbeat comment keeps the connection alive through proxies.
                    yield ": keepalive\n\n"
                    continue
                yield f"event: log\ndata: {json.dumps(rec)}\n\n"
        except asyncio.CancelledError:
            return
        finally:
            ring.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_logs_recent.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Regression — existing subgen log stream + logs router import unchanged**

Run: `python -m pytest tests/test_logs_recent.py tests/ -q -k "logs"`
Expected: PASS (the existing subgen `/api/logs/events` tests, if any, still pass).

- [ ] **Step 7: Format + commit**

```bash
cd /c/Projects/subarr
ruff format tests/test_logs_recent.py
ruff check src/subarr/app.py src/subarr/routers/logs.py tests/test_logs_recent.py
git add src/subarr/app.py src/subarr/routers/logs.py tests/test_logs_recent.py
git commit -m "feat(#157): install LogRing + /api/logs/recent + /api/logs/subarr/events

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Component B — supervise the audio-audit walker

### Task 5: RUN-level health wiring in `AudioAuditWalker._run`

**Files:**
- Modify: `src/subarr/audio_audit.py:207-260` (the `_run` driver)
- Modify: `src/subarr/app.py:352-364` (register `("audio-audit", None)`) and `app.py:594-601` (wire `walker._health`)
- Test: `tests/test_audio_audit_health.py` (Create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_audio_audit_health.py`:

```python
"""#157 gap-fill: RUN-level supervision of the opt-in audio-audit walker. A
clean run records success; a run-level crash records failure. Per-file errors
still land in AuditState.errors. A missing _health never breaks a run."""

from __future__ import annotations

import pytest

from subarr.audio_audit import AudioAuditWalker


class _FakeHealth:
    def __init__(self):
        self.successes: list[str] = []
        self.failures: list[tuple[str, BaseException]] = []

    def record_success(self, name, *, expected_interval_s=None):
        self.successes.append(name)

    def record_failure(self, name, exc, *, expected_interval_s=None):
        self.failures.append((name, exc))


class _FakeStore:
    """Minimal audit store: get() returns None (never resumes/skips); upsert()
    records nothing (the audit body is exercised elsewhere)."""

    def get(self, canonical_path):
        return None

    def upsert(self, **kwargs):
        pass


def _walker(worklist, *, health=None, audit_one=None):
    w = AudioAuditWalker(
        subgen=object(),
        audit_store=_FakeStore(),
        worklist=lambda scope="coverage": worklist,
        probe_store=None,
        busy_check=None,
        audio_lang=None,
    )
    if health is not None:
        w._health = health
    if audit_one is not None:
        # Replace the per-file body so the test controls success/failure.
        w._audit_one = audit_one  # type: ignore[assignment]
    return w


@pytest.mark.asyncio
async def test_clean_run_records_success():
    health = _FakeHealth()

    async def ok(state, canonical_path, tag_lang, mtime):
        return None  # clean per-file

    w = _walker([("a.mkv", "en", 1.0)], health=health, audit_one=ok)
    state = await w.start(scope="coverage")
    await w._task  # await the run to completion
    assert state.status == "done"
    assert health.successes == ["audio-audit"]
    assert health.failures == []


@pytest.mark.asyncio
async def test_run_level_crash_records_failure():
    health = _FakeHealth()

    # Force a RUN-level crash INSIDE _run (not in start()'s resolver, which
    # eagerly materializes the worklist via list(self._worklist(scope) or [])
    # and swallows resolution-time raises at audio_audit.py:164-171). A malformed
    # 2-tuple sails through the resolver (it is iterable) then raises ValueError
    # at _run's `for canonical_path, tag, mtime in worklist` unpack — OUTSIDE the
    # per-file try — hitting _run's outer except → record_failure.
    w = _walker([("a.mkv", "en")], health=health)  # 2-tuple -> unpack ValueError in _run
    state = await w.start(scope="coverage")
    await w._task
    assert state.status == "error"
    assert health.successes == []
    assert len(health.failures) == 1
    assert health.failures[0][0] == "audio-audit"
    assert isinstance(health.failures[0][1], ValueError)


@pytest.mark.asyncio
async def test_per_file_error_does_not_flip_run_to_failure():
    health = _FakeHealth()

    async def boom_one(state, canonical_path, tag_lang, mtime):
        raise ValueError("per-file boom")

    w = _walker([("a.mkv", "en", 1.0)], health=health, audit_one=boom_one)
    state = await w.start(scope="coverage")
    await w._task
    # Per-file error accumulated, run still completed cleanly -> success.
    assert state.status == "done"
    assert len(state.errors) == 1
    assert state.errors[0]["path"] == "a.mkv"
    assert health.successes == ["audio-audit"]
    assert health.failures == []


@pytest.mark.asyncio
async def test_missing_health_never_raises():
    async def ok(state, canonical_path, tag_lang, mtime):
        return None

    w = _walker([("a.mkv", "en", 1.0)], health=None, audit_one=ok)
    state = await w.start(scope="coverage")
    await w._task  # must complete without AttributeError
    assert state.status == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_audio_audit_health.py -q`
Expected: FAIL — `test_clean_run_records_success` fails (`health.successes` is empty; no wiring yet) and `test_run_level_crash_records_failure` fails (`health.failures` empty).

- [ ] **Step 3: Wire RUN-level success/failure into `_run`**

In `src/subarr/audio_audit.py`, edit the `_run` method (`audio_audit.py:207-260`). Add the success call on clean completion (right after the existing `log.info("audio-audit done: ...")` block) and the failure call in the outer `except Exception` block. The full edited tail of `_run`:

Change the clean-completion section (currently ends at `audio_audit.py:251` after the `log.info(...)`):

```python
            state.status = "done"
            state.finished_at = time.time()
            log.info(
                "audio-audit done: %d files, %d findings, %d errors",
                state.processed,
                state.found,
                len(state.errors),
            )
            # #157 gap-fill: RUN-level supervision. A clean full run records a
            # success on the unified Health roster. Best-effort (getattr guard),
            # mirroring the 7 supervised loops — a missing store never fails a run.
            _h = getattr(self, "_health", None)
            if _h:
                _h.record_success("audio-audit")
```

Change the outer `except Exception` block (`audio_audit.py:256-260`) to:

```python
        except Exception as e:  # pragma: no cover - defensive
            log.exception("audio-audit failed: %s", e)
            state.status = "error"
            state.error = repr(e)
            state.finished_at = time.time()
            # #157 gap-fill: a RUN-level crash (the outer drive, not a per-file
            # error) surfaces on the Health roster. Best-effort.
            _h = getattr(self, "_health", None)
            if _h:
                _h.record_failure("audio-audit", e)
```

Leave the `except asyncio.CancelledError:` branch (`audio_audit.py:252-255`) unchanged — a user-cancelled run is not a failure.

Note: the `# pragma: no cover` on the outer except must be REMOVED now that `test_run_level_crash_records_failure` exercises it. Change the line to just `except Exception as e:`.

- [ ] **Step 4: Register `("audio-audit", None)` at boot**

In `src/subarr/app.py`, in the boot-registration loop (`app.py:352-364`), add the audio-audit entry. Append to the tuple of `(_tname, _tiv)` pairs, after `("queue-feeder", 5)`:

```python
        ("queue-feeder", 5),  # #66/#116
        # #157 gap-fill: opt-in foreground walker. expected_interval_s=None so
        # the staleness branch never fires — an idle walker shows failures/streak
        # WITHOUT a false "stale" alarm, and sits on the unified Health roster.
        ("audio-audit", None),
```

- [ ] **Step 5: Wire `walker._health` at construction**

In `src/subarr/app.py`, right after the `AudioAuditWalker(...)` construction (`app.py:594-601`, which ends with `)` on the line assigning `app_.state.audio_audit`), add:

```python
    app_.state.audio_audit._health = app_.state.task_health  # #157 supervision
```

Place it immediately after the closing `)` of the `AudioAuditWalker(...)` call and before `app_.state.pending = PendingStore(...)` (`app.py:602`).

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m pytest tests/test_audio_audit_health.py -q`
Expected: PASS (4 passed).

- [ ] **Step 7: Regression — audio-audit behaviour + app boot unchanged**

Run: `python -m pytest tests/test_audio_audit_health.py -q && python -m pytest tests/ -q -k "audio_audit or audit"`
Expected: PASS.

- [ ] **Step 8: Format + commit**

```bash
cd /c/Projects/subarr
ruff format tests/test_audio_audit_health.py
ruff check src/subarr/audio_audit.py src/subarr/app.py tests/test_audio_audit_health.py
git add src/subarr/audio_audit.py src/subarr/app.py tests/test_audio_audit_health.py
git commit -m "feat(#157): supervise audio-audit walker (run-level task_health)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Component D — frontend surfaces

### Task 6: pure log helpers (`log-helpers.mjs`) + vitest

**Files:**
- Create: `src/subarr/static/v1/home-hifi/log-helpers.mjs`
- Test: `src/subarr/static/v1/home-hifi/__tests__/log-helpers.test.js` (Create)

- [ ] **Step 1: Write the failing test**

Create `src/subarr/static/v1/home-hifi/__tests__/log-helpers.test.js`:

```javascript
// #157 gap-fill — pure helpers for the Logs source switcher + Health recent
// errors row. Node-env, no DOM.
import { describe, it, expect } from 'vitest';
import { nextLogSource, formatRecentRow, LOG_SOURCES } from '../log-helpers.mjs';

describe('nextLogSource', () => {
  it('toggles subgen -> subarr and back', () => {
    expect(nextLogSource('subgen')).toBe('subarr');
    expect(nextLogSource('subarr')).toBe('subgen');
  });

  it('falls back to subgen for an unknown source', () => {
    expect(nextLogSource('bogus')).toBe('subgen');
    expect(nextLogSource(null)).toBe('subgen');
  });

  it('exposes the two supported sources', () => {
    expect(LOG_SOURCES).toEqual(['subgen', 'subarr']);
  });
});

describe('formatRecentRow', () => {
  it('maps a ring record to a display row', () => {
    const row = formatRecentRow({
      ts: 1751846400,
      level: 'WARNING',
      logger_name: 'subarr.coverage_engine',
      message: 'coverage build slow',
      exc_text: null,
    });
    expect(row.level).toBe('WARNING');
    expect(row.logger).toBe('subarr.coverage_engine');
    expect(row.message).toBe('coverage build slow');
    expect(row.hasTrace).toBe(false);
    expect(row.exc_text).toBe(null);
  });

  it('flags a record that carries a traceback', () => {
    const row = formatRecentRow({
      ts: 1751846400,
      level: 'ERROR',
      logger_name: 'subarr.app',
      message: 'boom',
      exc_text: 'Traceback (most recent call last): ...',
    });
    expect(row.hasTrace).toBe(true);
    expect(row.exc_text).toContain('Traceback');
  });

  it('tolerates a missing/partial record', () => {
    const row = formatRecentRow({});
    expect(row.level).toBe('INFO');
    expect(row.logger).toBe('');
    expect(row.message).toBe('');
    expect(row.hasTrace).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Projects/subarr && npm run test:frontend -- log-helpers`
Expected: FAIL — cannot resolve `../log-helpers.mjs`.

- [ ] **Step 3: Write the helper module**

Create `src/subarr/static/v1/home-hifi/log-helpers.mjs`:

```javascript
// #157 gap-fill — pure, DOM-free helpers shared by logs.jsx (source switcher)
// and health.jsx (recent-errors panel). Extracted so vitest can unit-test them
// without a React/DOM harness (mirrors instance-health-util.mjs).

// The two log sources subarr can live-stream. subgen = the container log
// (/api/logs/events); subarr = subarr's own in-process ring
// (/api/logs/subarr/events).
export const LOG_SOURCES = ['subgen', 'subarr'];

// Toggle between the two sources; unknown input falls back to 'subgen'.
export function nextLogSource(current) {
  return current === 'subgen' ? 'subarr' : 'subgen';
}

// Shape a /api/logs/recent ring record into a display row for the Health
// "Recent errors" panel. Defensive against partial records.
export function formatRecentRow(rec) {
  const r = rec || {};
  const exc = r.exc_text || null;
  return {
    ts: typeof r.ts === 'number' ? r.ts : null,
    level: r.level || 'INFO',
    logger: r.logger_name || '',
    message: r.message || '',
    exc_text: exc,
    hasTrace: !!exc,
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /c/Projects/subarr && npm run test:frontend -- log-helpers`
Expected: PASS.

- [ ] **Step 5: Commit** (no bundle — `.mjs` helper + test only)

```bash
cd /c/Projects/subarr
git add src/subarr/static/v1/home-hifi/log-helpers.mjs src/subarr/static/v1/home-hifi/__tests__/log-helpers.test.js
git commit -m "feat(#157): pure log helpers (source switcher + recent row)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 7: Logs page — `subgen | subarr` source switcher

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/logs.jsx`
- Modify (rebuild): `src/subarr/static/v1/home-hifi/logs.bundle.js` + `.map`

- [ ] **Step 1: Rewire the EventSource + source state in `logs.jsx`**

In `src/subarr/static/v1/home-hifi/logs.jsx`, import the helper at the top (after the `const { useState, useEffect, useRef, useMemo } = React;` line, line 4). Import ONLY `LOG_SOURCES` — the chips are single-select via `setSource(src)`, so `nextLogSource` (a keyboard/programmatic toggle helper) is NOT imported here; it is unit-tested standalone in Task 6. Importing an unused symbol would be dead code:

```javascript
import { LOG_SOURCES } from './log-helpers.mjs';
```

Replace the `const SOURCES = ['subgen'];` line (line 30) with the shared list:

```javascript
const SOURCES = LOG_SOURCES;  // ['subgen', 'subarr'] — #157 gap-fill
```

Add a `source` state and make the EventSource depend on it. Replace the state block (lines 33-47) — specifically add a `source` state alongside the existing state, and clear `lines` when it changes. Add after `const [streamError, setStreamError] = useState(null);` (line 42):

```javascript
  // #157 gap-fill: which log to stream — 'subgen' (container) or 'subarr'
  // (subarr's own in-process ring). The EventSource re-subscribes on change.
  const [source, setSource] = useState('subgen');
```

Change the `classifyLine` function (lines 18-24) to accept the active source instead of hardcoding `'subgen'`:

```javascript
// Derive the level from the line text; the source is passed in (which stream
// this line came from), not guessed from content (#209).
function classifyLine(line, source) {
  let level = 'INFO';
  if (line.match(/\bERROR\b/i)) level = 'ERROR';
  else if (line.match(/\bWARN(?:ING)?\b/i)) level = 'WARN';
  else if (line.match(/\bDEBUG\b/i)) level = 'DEBUG';
  return { source, level };
}
```

Replace the SSE `useEffect` (lines 49-90) so it depends on `source`, picks the right endpoint, clears lines on switch, and (for subarr) parses the structured record's message. New effect:

```javascript
  useEffect(() => {
    setLines([]);          // #157: clear the view when switching source
    bufRef.current = [];
    setStreamError(null);
    const endpoint = source === 'subarr' ? '/api/logs/subarr/events' : '/api/logs/events';
    let es;
    try {
      es = new EventSource(endpoint);
      es.onopen = () => { setConnected(true); setStreamError(null); };
      es.onerror = () => setConnected(false);
      es.addEventListener('stream_error', (e) => {
        let msg = e.data || '';
        try { msg = JSON.parse(msg); } catch { /* already plain */ }
        setStreamError(msg || 'Docker is unavailable.');
        setConnected(false);
      });
      const onLine = (e) => {
        const t = Date.now();
        let payload = e.data || '';
        try { payload = JSON.parse(payload); } catch {}
        // subgen streams a raw string line; subarr streams a structured record.
        const text = (payload && typeof payload === 'object')
          ? `${payload.level || 'INFO'} ${payload.logger_name || ''} ${payload.message || ''}`.trim()
          : String(payload);
        const { source: src, level } = classifyLine(text, source);
        bufRef.current.push({ t, text, source: src, level, id: t + '-' + Math.random() });
        if (!flushTimer.current) {
          flushTimer.current = setTimeout(() => {
            flushTimer.current = null;
            if (!pausedRef.current) {
              setLines(prev => {
                const next = prev.concat(bufRef.current);
                bufRef.current = [];
                return next.length > 5000 ? next.slice(-5000) : next;
              });
            }
          }, 200);
        }
      };
      es.addEventListener('log', onLine);
      es.onmessage = onLine;
    } catch {}
    return () => { try { es && es.close(); } catch {} };
  }, [source]);
```

Replace the source chips block (lines 145-151) so the chips SWITCH the source (single-select) rather than toggling a filter set. The `filtered` memo (lines 98-103) filters by `sources.has(l.source)`; since we now stream one source at a time and tag lines with it, keep that filter working by making `sources` follow `source`. Simplest: replace the chips with a single-select switcher and drop the multi-select `sources` state usage in `filtered`.

Replace the chips render block:

```javascript
        {SOURCES.map(src => (
          <button key={src} onClick={() => setSource(src)}
            className={`chip ${source === src ? 'violet' : ''}`}
            style={{ cursor: 'pointer', textTransform: 'lowercase' }}
            aria-pressed={source === src}>
            {src}
          </button>
        ))}
```

And simplify the `filtered` memo (lines 98-103) to filter only by search (the stream is already a single source):

```javascript
  const filtered = useMemo(() => {
    const s = search.trim().toLowerCase();
    return lines.filter(l => !s || l.text.toLowerCase().includes(s));
  }, [lines, search]);
```

Remove the now-unused `sources` state (line 37) and `toggleSource` (lines 105-111) — deleting them together with their sole usages so the ruff-equivalent (esbuild) has no dangling refs. Update the subheading text (lines 125-127) to:

```javascript
            Live SSE stream — switch between subgen (container) and subarr (its own log).
```

- [ ] **Step 2: Build the bundle**

Run: `cd /c/Projects/subarr && npm run build:frontend`
Expected: builds `logs.bundle.js` (+ `.map`) with no errors.

- [ ] **Step 3: Run the frontend test suite (regression)**

Run: `cd /c/Projects/subarr && npm run test:frontend`
Expected: PASS (existing tests + `log-helpers` green; no test imports `LogsPage` directly, so the jsx rewrite is covered by the helper tests + the build succeeding).

- [ ] **Step 4: Verify no bundle drift**

Run: `cd /c/Projects/subarr && npm run check:frontend`
Expected: build runs then `git diff --exit-code` passes AFTER we stage — i.e. the committed bundle matches the source. (Stage in the next step; `check:frontend` is re-run in the final gate.)

- [ ] **Step 5: Commit the jsx + bundle + map**

```bash
cd /c/Projects/subarr
git add src/subarr/static/v1/home-hifi/logs.jsx \
        src/subarr/static/v1/home-hifi/logs.bundle.js \
        src/subarr/static/v1/home-hifi/logs.bundle.js.map
git commit -m "feat(#157): Logs page subgen|subarr source switcher

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 8: Health page — "Recent errors" panel

**Files:**
- Modify: `src/subarr/static/v1/home-hifi/health.jsx`
- Modify (rebuild): `src/subarr/static/v1/home-hifi/health.bundle.js` + `.map`

- [ ] **Step 1: Add the RecentErrors component + panel in `health.jsx`**

In `src/subarr/static/v1/home-hifi/health.jsx`, import the helper at the top (after the `import { apiFetch } from './api.jsx';` line, line 10):

```javascript
import { formatRecentRow } from './log-helpers.mjs';
```

Add a `RecentErrors` component above `HealthPage` (before line 295). It fetches `/api/logs/recent?level=WARNING` on the same 8s cadence pattern the page uses:

```javascript
// #157 gap-fill — recent WARN+ records from subarr's OWN log ring. Gives the
// CONTEXT around a red task that the single last-traceback can't. Fed by
// /api/logs/recent (the in-process LogRing snapshot).
function RecentErrors() {
  const [rows, setRows] = useState(null);
  const [open, setOpen] = useState({}); // id -> expanded

  const load = useCallback(() => {
    fetch('/api/logs/recent?level=WARNING&limit=200', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
      .then((d) => setRows((d.records || []).map(formatRecentRow)))
      .catch(() => setRows([]));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  const levelColor = (lvl) =>
    lvl === 'ERROR' || lvl === 'CRITICAL' ? 'var(--error-400, #f87171)'
      : lvl === 'WARNING' ? 'var(--warn-500, #f59e0b)'
      : 'var(--fg-3)';

  return (
    <section style={cardStyle}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <span className="label">Recent errors</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-3)' }}>
          {rows == null ? 'loading…' : `${rows.length} WARN+`}
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {(rows || []).slice().reverse().map((row, i) => {
          const id = `${row.ts}-${i}`;
          const isOpen = !!open[id];
          return (
            <React.Fragment key={id}>
              <div onClick={() => row.hasTrace && setOpen((o) => ({ ...o, [id]: !o[id] }))}
                style={{
                  display: 'flex', alignItems: 'baseline', gap: 10, padding: '6px 10px',
                  borderRadius: 'var(--radius-md)', cursor: row.hasTrace ? 'pointer' : 'default',
                  fontFamily: 'JetBrains Mono, monospace', fontSize: 12,
                }}>
                <span style={{ flex: 'none', width: 64, color: levelColor(row.level), fontWeight: 600 }}>
                  {row.level}
                </span>
                <span style={{ flex: 'none', color: 'var(--fg-3)' }}>{timeAgo(row.ts)}</span>
                <span style={{ flex: 'none', color: 'var(--violet-400)' }}>{row.logger}</span>
                <span style={{ flex: 1, minWidth: 0, color: 'var(--fg-1)', wordBreak: 'break-all' }}>
                  {row.message}
                </span>
                <span style={{ flex: 'none', width: 14, color: 'var(--fg-3)' }}>
                  {row.hasTrace ? (isOpen ? '▾' : '▸') : ''}
                </span>
              </div>
              {isOpen && row.hasTrace && (
                <pre style={{
                  margin: '0 0 8px 74px', padding: '10px 12px', background: '#0d0d10',
                  border: 'var(--border)', borderRadius: 'var(--radius-md)',
                  whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  fontFamily: 'JetBrains Mono, monospace', fontSize: 11, lineHeight: 1.45,
                  color: 'var(--fg-2)',
                }}>{row.exc_text}</pre>
              )}
            </React.Fragment>
          );
        })}
        {rows && rows.length === 0 && (
          <div style={{ color: 'var(--fg-3)', padding: 12 }}>No recent warnings or errors.</div>
        )}
      </div>
    </section>
  );
}
```

- [ ] **Step 2: Render the panel inside `HealthPage`**

In `HealthPage`'s returned JSX, add `<RecentErrors />` between the Background-tasks `</section>` (line 355) and `<DbMaintenance ... />` (line 357):

```javascript
      </section>

      <RecentErrors />

      <DbMaintenance dbIntegrityUnhealthy={dbIntegrityUnhealthy} />
```

- [ ] **Step 3: Build the bundle**

Run: `cd /c/Projects/subarr && npm run build:frontend`
Expected: builds `health.bundle.js` (+ `.map`) with no errors.

- [ ] **Step 4: Run the frontend test suite (regression)**

Run: `cd /c/Projects/subarr && npm run test:frontend`
Expected: PASS.

- [ ] **Step 5: Commit the jsx + bundle + map**

```bash
cd /c/Projects/subarr
git add src/subarr/static/v1/home-hifi/health.jsx \
        src/subarr/static/v1/home-hifi/health.bundle.js \
        src/subarr/static/v1/home-hifi/health.bundle.js.map
git commit -m "feat(#157): Health page Recent errors panel (subarr log ring)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final task

### Task 9: full gate + hand back to controller

**Files:** none (verification only)

- [ ] **Step 1: Full Python suite**

Run: `cd /c/Projects/subarr && python -m pytest -q`
Expected: PASS — the whole suite green, including the four new test files. If anything failed, STOP and fix (do not proceed).

- [ ] **Step 2: Frontend tests + build + drift check**

Run:
```bash
cd /c/Projects/subarr
npm run test:frontend
npm run build:frontend
npm run check:frontend
```
Expected: `test:frontend` all green; `build:frontend` no errors; `check:frontend` exits 0 (no uncommitted bundle drift). If `check:frontend` reports drift, `git add` the rebuilt `*.bundle.js`/`*.bundle.js.map` and amend the relevant frontend commit.

- [ ] **Step 3: ruff on all changed Python**

Run:
```bash
cd /c/Projects/subarr
ruff check src/subarr/config.py src/subarr/app.py src/subarr/log_ring.py \
  src/subarr/audio_audit.py src/subarr/routers/logs.py \
  tests/test_config_debug.py tests/test_log_ring.py tests/test_logs_recent.py \
  tests/test_audio_audit_health.py
ruff format --check src/subarr/config.py src/subarr/app.py src/subarr/log_ring.py \
  src/subarr/audio_audit.py src/subarr/routers/logs.py \
  tests/test_config_debug.py tests/test_log_ring.py tests/test_logs_recent.py \
  tests/test_audio_audit_health.py
```
Expected: `All checks passed!` and no format diff. If `ruff format --check` fails, run `ruff format <file>` on the offender, re-stage, and amend its commit.

- [ ] **Step 4: Confirm no runtime files or bundles left unstaged**

Run: `cd /c/Projects/subarr && git status --porcelain`
Expected: clean (or only intended files). Do NOT commit any `.db`, `*.db-wal`, `*.db-shm`, or cache artifacts.

- [ ] **Step 5: Hand back to the controller**

Do NOT push and do NOT open a PR. Report to the controller: branch `feat/157-observability-gapfill`, all 9 tasks committed, suite + vitest + ruff green. The controller runs the risk-tiered pre-merge review before merge.

---

## Spec coverage

| Spec requirement (`2026-07-07-...-design.md`) | Task |
|-----------------------------------------------|------|
| **Component A** — `debug: bool` from `SUBARR_DEBUG` (`1/true/yes/on`, default off), mirrors `retime_enabled` | Task 1 |
| **Component A** — logging: debug → root DEBUG + httpx/httpcore un-pinned (INFO); off = byte-for-byte today | Task 2 |
| **Component C core** — `LogRing(logging.Handler)`, bounded `deque(maxlen)`, structured record (`ts`/`level`/`logger_name`/`message`/`exc_text`) | Task 3 |
| **Component C core** — `snapshot(level, limit)` filter + tail | Task 3 |
| **Component C core** — asyncio subscribe/unsubscribe fan-out; bounded per-subscriber queue drops oldest | Task 3 |
| **Component C core** — `emit` wraps everything, NEVER raises | Task 3 |
| **Component C install** — handler on root logger during logging setup (INFO capture) | Task 4 |
| **Component C endpoints** — `GET /api/logs/recent?level=&limit=` snapshot, read-only | Task 4 |
| **Component C endpoints** — `GET /api/logs/subarr/events?tail=` SSE (replay tail then stream), subgen-shape | Task 4 |
| **Component C endpoints** — tolerate a missing ring (empty/close) | Task 4 |
| **Component B** — register `("audio-audit", None)` at boot (no false stale) | Task 5 |
| **Component B** — wire `AudioAuditWalker._health` at construction | Task 5 |
| **Component B** — run-level `record_success` (clean) / `record_failure` (crash); per-file errors unchanged; missing `_health` never raises | Task 5 |
| **Component D** — Health "Recent errors" panel fed by `/api/logs/recent?level=WARNING`, expandable `exc_text` | Task 8 |
| **Component D** — Logs `subgen | subarr` source switcher; subarr → `/api/logs/subarr/events`; reuse filter/pause/search/autoscroll | Task 7 |
| **Testing** — config env-parse; LogRing unit; endpoint snapshot+SSE; walker supervision; frontend pure helpers | Tasks 1,3,4,5,6 |
| **Acceptance 1** — SUBARR_DEBUG raises to DEBUG + httpx detail; unset = INFO | Tasks 1,2 |
| **Acceptance 2** — run-level crash → unhealthy `audio-audit` row, no false stale; clean → healthy | Task 5 |
| **Acceptance 3** — Health "Recent errors" WARN+ with expandable tracebacks | Task 8 |
| **Acceptance 4** — Logs page live tail of subarr's own log alongside subgen | Task 7 |
| **Acceptance 5** — full suite + vitest green, ruff clean, no bundle drift | Task 9 |
| **Error handling** — emit exception-proof (Task 3); SSE disconnect cleanup + bounded drop (Tasks 3,4); walker best-effort (Task 5); endpoints tolerate missing ring (Task 4) | Tasks 3,4,5 |

## Self-Review notes (run before handoff)

1. **Spec coverage:** every Component (A–D), all 5 acceptance criteria, the Testing list, and every Error-handling bullet map to a task above — no gaps.
2. **Placeholders:** none — every code step shows the actual code; no "similar to Task N".
3. **Name/type consistency (verified identical across tasks):**
   - `debug` field — declared Task 1, read by `_apply_logging(settings.debug)` Task 2.
   - `LogRing` methods — `snapshot(level, limit)`, `subscribe()`, `unsubscribe(q)`, `emit()` — identical in Task 3 (impl), Task 4 (endpoints call `snapshot`/`subscribe`/`unsubscribe`).
   - Module global `LOG_RING` (app.py) — set in Task 4, read via `getattr(app_mod, "LOG_RING", None)` in the router (Task 4) and cleared in a Task 4 test.
   - Task name string `"audio-audit"` — identical in the boot register (Task 5), the walker `record_success`/`record_failure` (Task 5), and asserted in the test (Task 5).
   - Endpoint paths `/api/logs/recent` and `/api/logs/subarr/events` — identical across Task 4 (impl + tests) and the frontend (`logs.jsx` Task 7, `health.jsx` Task 8).
   - JS helper names `LOG_SOURCES`, `nextLogSource`, `formatRecentRow` — declared in `log-helpers.mjs` (Task 6), imported by `logs.jsx` (Task 7: `LOG_SOURCES` only — `nextLogSource` is unit-tested standalone, not imported, to avoid dead code) and `health.jsx` (Task 8: `formatRecentRow`), all three tested in Task 6.
   - Record dict keys `ts`/`level`/`logger_name`/`message`/`exc_text` — produced by `LogRing._to_dict` (Task 3), asserted in `test_logs_recent` (Task 4), consumed by `formatRecentRow` (Task 6).
