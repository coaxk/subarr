"""Onboarding wizard state machine.

Reads/writes a single row in `onboarding_state`. The wizard frontend
calls these store methods through the /api/onboarding/* router.

Design choices:

- **Resumable.** Step + progress_json persist; user can close the
  browser mid-wizard and pick up where they left off. Power-user
  shortcut: set integration env vars before first boot — wizard
  loads with all fields pre-filled.

- **Progress merged, not replaced.** Each PUT merges the request
  body into progress_json so subsequent steps can rely on prior
  fields being present. Explicit DELETE clears a key.

- **No live writes to settings during wizard.** API keys + URLs land
  in progress_json, then on /complete we flush the lot to the in-
  memory Settings + (later) persist to a config file. This keeps
  partial completions from breaking the running app.

- **Step numbering** is contiguous 0..10. The wizard frontend owns
  the rendering; the backend only tracks the integer. Future re-
  ordering would bump a schema version, not the column type.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# Canonical step indices — keep in sync with frontend.
STEP_WELCOME = 0
STEP_MEDIA = 1
STEP_BAZARR = 2
STEP_SONARR = 3
STEP_RADARR = 4
STEP_TAUTULLI = 5
STEP_SUBGEN = 6
STEP_OLLAMA = 7
STEP_GPU = 8
STEP_SPEECH = 9        # #111 — speech-aware audio (silero VAD) opt-in
STEP_FIRST_WALK = 10
STEP_DONE = 11

# Maximum step value the API accepts. STEP_DONE marks completion.
MAX_STEP = STEP_DONE


@dataclass
class OnboardingState:
    step: int
    completed_at: float | None
    progress: dict[str, Any]
    created_at: float
    updated_at: float

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "completed_at": self.completed_at,
            "is_complete": self.is_complete,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class OnboardingStore:
    """One-row store with merge-on-write semantics."""

    def __init__(self, db_path: Path):
        self._path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._ensure_row()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ─── Read ──────────────────────────────────────────────────────

    def get(self) -> OnboardingState:
        row = self._conn.execute(
            "SELECT step, completed_at, progress_json, created_at, updated_at "
            "FROM onboarding_state WHERE id = 1"
        ).fetchone()
        if row is None:  # belt-and-braces; _ensure_row already ran
            self._ensure_row()
            return self.get()
        try:
            progress = json.loads(row[2]) if row[2] else {}
        except json.JSONDecodeError:
            progress = {}
        return OnboardingState(
            step=row[0], completed_at=row[1], progress=progress,
            created_at=row[3], updated_at=row[4],
        )

    # ─── Write ─────────────────────────────────────────────────────

    def update(self, *, step: int | None = None,
               progress_patch: dict[str, Any] | None = None,
               unset_keys: list[str] | None = None) -> OnboardingState:
        """Merge progress_patch into progress, optionally advance step.

        - `step`: when set, becomes the new current step. Bounds-checked
          0..MAX_STEP; values outside are clamped.
        - `progress_patch`: shallow-merged into existing progress.
          Nested dicts are REPLACED, not deep-merged — caller composes
          full sub-trees if they need granular control.
        - `unset_keys`: explicit removals.
        """
        state = self.get()

        if progress_patch:
            for k, v in progress_patch.items():
                if v is None:
                    state.progress.pop(k, None)
                else:
                    state.progress[k] = v
        if unset_keys:
            for k in unset_keys:
                state.progress.pop(k, None)
        new_step = state.step if step is None else max(0, min(MAX_STEP, int(step)))

        self._conn.execute(
            "UPDATE onboarding_state "
            "SET step = ?, progress_json = ?, updated_at = ? "
            "WHERE id = 1",
            (new_step, json.dumps(state.progress, separators=(",", ":")), time.time()),
        )
        return self.get()

    def complete(self) -> OnboardingState:
        """Mark the wizard finished. Idempotent."""
        ts = time.time()
        self._conn.execute(
            "UPDATE onboarding_state "
            "SET step = ?, completed_at = COALESCE(completed_at, ?), updated_at = ? "
            "WHERE id = 1",
            (STEP_DONE, ts, ts),
        )
        return self.get()

    def reset(self) -> OnboardingState:
        """Wipe state + start over. Used by Settings → 'Re-run setup'."""
        ts = time.time()
        self._conn.execute(
            "UPDATE onboarding_state "
            "SET step = 0, completed_at = NULL, progress_json = '{}', updated_at = ? "
            "WHERE id = 1",
            (ts,),
        )
        return self.get()

    # ─── Internal ───────────────────────────────────────────────────

    def _ensure_row(self) -> None:
        existing = self._conn.execute(
            "SELECT 1 FROM onboarding_state WHERE id = 1"
        ).fetchone()
        if not existing:
            ts = time.time()
            self._conn.execute(
                "INSERT INTO onboarding_state "
                "(id, step, progress_json, created_at, updated_at) "
                "VALUES (1, 0, '{}', ?, ?)",
                (ts, ts),
            )
