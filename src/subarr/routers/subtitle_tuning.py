"""Subtitle-tuning settings provider/API seam.

GET  /api/settings/subtitle-tuning — current values + per-field env_controlled
PUT/POST /api/settings/subtitle-tuning — persist validated updates atomically
      through config_store, live-apply only fields whose env var is unset.

Modelled on routers/integrations.py + forced_segment.py /config: env authority
is preserved (an operator-pinned SUBARR_RETIME_ENABLED var is never clobbered
by a persisted or live UI write), writes are atomic via
config_store.save_overrides (one bulk fsynced write per multi-field update),
and live application uses the frozen-Settings
bypass — the same deliberate runtime patch the credential and libraries
editors use.

The surface is deliberately narrow: exactly the six fields the Settings
subtitle-tuning page edits — the retime toggle plus the five retimer numerics.
Nothing else on Settings is writable through this seam.

Canonical Bounds (mirrored by the frontend TUNING_BOUNDS):
  - target_cps: float in [5.0, 25.0]
  - min_cue_ms: int in [100, 5000]
  - min_gap_ms: int in [0, 1000]
  - max_cue_ms: int in [1000, 15000]
  - max_borrow_ms: int in [0, 5000]
Each numeric is pydantic bounds-checked here (ge/le) so a bad value never
reaches config_store or the running Settings; config.py additionally clamps
persisted values into these bounds at load. Cross-field rule: effective
max_cue_ms >= effective min_cue_ms, enforced against the MERGED (current +
supplied/overridden) state.

Preview transient-override semantics: the preview accepts validated draft
overrides that are merged with the live settings for THIS response only — they
are never persisted and never mutate the running Settings singleton.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config, config_store
from ..config import env_is_set
from ..subtitle_readability import cue_metrics
from ..subtitle_retime import retime_params_from_settings, retime_srt

router = APIRouter(prefix="/api/settings", tags=["settings"])
log = logging.getLogger(__name__)

# Serialize a whole PUT/DELETE transaction: store snapshot -> persist/clear ->
# live apply -> (on failure) live + store rollback. FastAPI runs sync handlers
# in a threadpool, so two concurrent PUTs could otherwise interleave: A
# snapshots, B snapshots, A persists+applies, B persists+applies, then B's live
# apply fails and rolls the store back to B's pre-A snapshot — wiping A's
# persisted keys. RLock (not Lock) so a re-entrant path can never self-deadlock.
# This is the operation-level lock; config_store has its OWN module lock that
# serializes individual file writes and remains independent.
_tx_lock = threading.RLock()

# The subtitle-tuning surface: request/response keys are the Settings attribute
# names themselves. Values are always read off the RUNNING settings singleton,
# which is already env/persisted/default-resolved, so GET reports ground truth.
_FIELDS = (
    "retime_enabled",
    "target_cps",
    "min_cue_ms",
    "min_gap_ms",
    "max_cue_ms",
    "max_borrow_ms",
)


def _field_view() -> dict:
    """Per-field value + env_controlled metadata for the tuning page: the UI
    renders a field read-only (locked) when env_controlled is True, because a
    non-empty env var is the operator's authoritative declaration."""
    return {
        name: {"value": getattr(config.settings, name), "env_controlled": env_is_set(name)}
        for name in _FIELDS
    }


@router.get("/subtitle-tuning")
def get_subtitle_tuning() -> dict:
    """Current values + per-field env_controlled metadata. No env fields are
    hidden — the UI shows them locked, so an operator can see WHY a control is
    disabled (rather than an invisible override)."""
    return {"fields": _field_view()}


class SubtitleTuningBody(BaseModel):
    """Partial update body. All fields optional (a PATCH-like PUT/POST: only the
    supplied keys are persisted/applied). Unknown keys are rejected with a 422
    (extra="forbid"), and each numeric field is bounds-checked against the
    Canonical Bounds so a bad value never reaches config_store or the running
    Settings.

    Cross-field rule: effective max_cue_ms >= effective min_cue_ms (enforced in
    the handler against the MERGED current+supplied state, because these are
    partial updates).
    """

    model_config = {"extra": "forbid"}

    retime_enabled: bool | None = None
    target_cps: float | None = Field(default=None, ge=5.0, le=25.0)
    min_cue_ms: int | None = Field(default=None, ge=100, le=5000)
    min_gap_ms: int | None = Field(default=None, ge=0, le=1000)
    max_cue_ms: int | None = Field(default=None, ge=1000, le=15000)
    max_borrow_ms: int | None = Field(default=None, ge=0, le=5000)


def _merge(name: str, supplied: dict) -> int | float:
    """Effective value of a numeric field: supplied if present, else current.
    `target_cps` is a float (CPS); the ms fields are ints."""
    return supplied.get(name, getattr(config.settings, name))


def _rollback_live(snapshot: dict) -> None:
    """Best-effort restore of live Settings values after a failed apply. Never
    raises — a per-field rollback failure is logged so it stays visible."""
    for name, value in snapshot.items():
        try:
            object.__setattr__(config.settings, name, value)
        except Exception:  # noqa: BLE001 - best-effort restore
            log.error("subtitle-tuning: live rollback of %s failed", name, exc_info=True)


def _rollback_store(snapshot: dict, touched: set[str]) -> None:
    """Best-effort restore of ONLY the config-store override keys touched by the
    failed transaction — never the whole pre-write snapshot, never stray keys
    outside the touched set.

    config_store exposes only merge-save and clear-remove (no replace-all), so
    restore is per touched key: re-save it when it was present in the
    pre-transaction `snapshot`, otherwise clear it. Keys outside the touched set
    (e.g. an unrelated override or a concurrent transaction's write) are never
    rewritten or removed — that is the "never overwrite unrelated concurrent
    config changes" guarantee. Never raises; per-key failures are logged so
    they stay visible."""
    for key in touched:
        try:
            if key in snapshot:
                config_store.save_overrides({key: snapshot[key]})
            else:
                config_store.clear_overrides([key])
        except Exception:  # noqa: BLE001 - best-effort restore
            log.error("subtitle-tuning: store rollback of %s failed", key, exc_info=True)


@router.put("/subtitle-tuning")
@router.post("/subtitle-tuning")
def set_subtitle_tuning(body: SubtitleTuningBody) -> dict:
    """Persist + live-apply validated tuning changes without a restart.

    For each supplied field:
      - env-set fields are NOT overwritten (reported in `managed_by_env`);
      - otherwise the value is persisted to the config-store override file
        (survives restart, below env) AND applied to the running Settings
        singleton so it takes effect immediately.

    Returns {ok, applied, managed_by_env, fields}. Validation runs BEFORE any
    mutation: an invalid body (bad range, unknown key, max_cue_ms < min_cue_ms)
    raises a field-identifying 422 and neither the store nor the live config is
    touched.
    """
    supplied = body.model_dump(exclude_none=True)
    if not supplied:
        raise HTTPException(400, detail="no subtitle-tuning fields supplied")

    # Cross-field sanity on the MERGED effective state (partial update): a cue
    # must never be allowed to end before it can start. Identifies the field so
    # the UI can render the error inline. Checked before any mutation.
    if _merge("max_cue_ms", supplied) < _merge("min_cue_ms", supplied):
        raise HTTPException(
            422,
            detail=[
                {
                    "loc": ["body", "max_cue_ms"],
                    "msg": "max_cue_ms must be >= min_cue_ms",
                    "type": "value_error",
                }
            ],
        )

    # Partition supplied fields: env-managed (skipped entirely — operator's env
    # is authoritative and must never be clobbered) vs the to-persist set.
    to_persist: dict[str, int | float | bool] = {}
    managed_by_env: list[str] = []
    for name, value in supplied.items():
        if env_is_set(name):
            managed_by_env.append(name)
            log.info("subtitle-tuning: %s managed by env, ignored UI write", name)
            continue
        to_persist[name] = value

    if not to_persist:
        # Every supplied field was env-managed — nothing to write or apply.
        return {
            "ok": True,
            "applied": [],
            "managed_by_env": managed_by_env,
            "fields": _field_view(),
        }

    # Snapshot the pre-write store + live values so a failed live-apply can roll
    # back BOTH and never leave disk/live diverged. The whole mutation section —
    # snapshot -> persist -> live apply -> rollback — runs under _tx_lock so two
    # concurrent PUTs/DELETEs cannot interleave (see _tx_lock docstring).
    touched = set(to_persist)
    with _tx_lock:
        store_snapshot = config_store.load_overrides()
        live_snapshot = {name: getattr(config.settings, name) for name in to_persist}

        # Persist every field in ONE atomic fsynced write (config_store). If this
        # raises, nothing on disk or live was mutated — surface as a clean 500.
        try:
            config_store.save_overrides(to_persist)
        except (config_store.ConfigStoreError, OSError) as e:
            log.error("subtitle-tuning: atomic persist failed: %s", e)
            raise HTTPException(500, detail="tuning update failed to persist") from e

        # Live-apply. On ANY failure roll back live + store to the snapshots and
        # fail loudly — a silent disk/live split is never acceptable.
        try:
            for name, value in to_persist.items():
                object.__setattr__(config.settings, name, value)
        except Exception:  # noqa: BLE001 - roll back + fail loudly, never diverge
            log.error("subtitle-tuning: live-apply failed; rolling back", exc_info=True)
            _rollback_live(live_snapshot)
            _rollback_store(store_snapshot, touched)
            raise HTTPException(
                500,
                detail="tuning update persisted but failed to apply live; changes rolled back",
            ) from None

    return {
        "ok": True,
        "applied": list(to_persist),
        "managed_by_env": managed_by_env,
        "fields": _field_view(),
    }


@router.delete("/subtitle-tuning")
def reset_subtitle_tuning() -> dict:
    """Reset subtitle-tuning to the built-in defaults (RETIME_DEFAULTS).

    Removes any persisted tuning overrides (one atomic store write) and reverts
    the live Settings to the current built-in defaults. Env-managed fields
    (an operator-pinned SUBARR_RETIME_ENABLED) are NEVER touched — neither
    cleared nor live-reverted — and are reported in `managed_by_env`.

    Uses clear_override semantics (not writing literal defaults), so the reset
    tracks future built-in default changes instead of pinning the user to
    stale literals. Same snapshot/rollback/fail-loudly handling as the PUT.

    Returns {ok, reset, managed_by_env, fields} — the DELETE analogue of the
    PUT response shape.
    """
    managed_by_env: list[str] = []
    to_reset: list[str] = []
    for name in _FIELDS:
        if env_is_set(name):
            managed_by_env.append(name)
        else:
            to_reset.append(name)

    # Snapshot pre-reset store + live values for rollback. The whole mutation
    # section — snapshot -> clear -> live revert -> rollback — runs under
    # _tx_lock so a concurrent PUT/DELETE cannot interleave (see _tx_lock doc).
    touched = set(to_reset)
    with _tx_lock:
        store_snapshot = config_store.load_overrides()
        live_snapshot = {name: getattr(config.settings, name) for name in to_reset}

        # One atomic write removing the to-reset keys (no-op when none persisted).
        try:
            config_store.clear_overrides(to_reset)
        except (config_store.ConfigStoreError, OSError) as e:
            log.error("subtitle-tuning: reset persist failed: %s", e)
            raise HTTPException(500, detail="tuning reset failed to persist") from e

        # Revert live settings to the current built-in defaults.
        try:
            for name in to_reset:
                object.__setattr__(config.settings, name, config.RETIME_DEFAULTS[name])
        except Exception:  # noqa: BLE001 - roll back + fail loudly, never diverge
            log.error("subtitle-tuning: reset live-apply failed; rolling back", exc_info=True)
            _rollback_live(live_snapshot)
            _rollback_store(store_snapshot, touched)
            raise HTTPException(
                500,
                detail="tuning reset persisted but failed to apply live; changes rolled back",
            ) from None

    return {
        "ok": True,
        "reset": to_reset,
        "managed_by_env": managed_by_env,
        "fields": _field_view(),
    }


# ─── Bounded static sample seam + preview operation ──────────────────────────
#
# The sample seam serves ONLY manifest-registered files shipped in the package
# (src/subarr/data/subtitle_samples) — no arbitrary filesystem access, no
# provider ranking, no pagination, no HI acquisition, no OCR. The preview
# operation runs the retimer over the merged (live settings + transient draft
# overrides) RetimeParams but is a pure read/transform: it never writes media,
# sidecars, or persisted settings, and never mutates the live Settings.

# Max accepted preview body length (chars) — a sample must be small. Well beyond
# any real subtitle, far below anything that could be an abuse vector.
_PREVIEW_MAX_CHARS = 200_000

_SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "subtitle_samples"
# id -> {file, name, description, language}. The manifest is the ONLY file set
# served — a path not listed here is unreachable regardless of what exists on
# disk. All four samples are English-language.
_SAMPLES: dict[str, dict[str, str]] = {
    "dialogue": {
        "file": "dialogue.en.srt",
        "name": "Dialogue",
        "description": "Natural conversational spoken dialogue.",
        "language": "en",
    },
    "dense": {
        "file": "dense.en.srt",
        "name": "Dense prose",
        "description": "Long-form prose lines with over-CPS cues.",
        "language": "en",
    },
    "sdh": {
        "file": "sdh.en.srt",
        "name": "HI/SDH",
        "description": "Speaker labels and audio descriptions (HI/SDH).",
        "language": "en",
    },
    "rapid": {
        "file": "rapid.en.srt",
        "name": "Rapid exchange",
        "description": "Quick back-and-forth with over-CPS and micro cues.",
        "language": "en",
    },
}


def _read_sample(sample_id: str) -> tuple[dict[str, str], str]:
    """Return (meta, text) for a manifest-registered sample. Locks every read to
    the static sample dir + the manifest filename (defense-in-depth beyond the
    manifest whitelist itself). 404 for unknown ids / missing files."""
    meta = _SAMPLES.get(sample_id)
    if meta is None:
        raise HTTPException(404, detail=f"unknown sample {sample_id!r}")
    base = _SAMPLE_DIR.resolve()
    target = (base / meta["file"]).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(404, detail=f"sample {sample_id!r} unavailable")
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("subtitle sample %s read failed: %s", sample_id, e)
        raise HTTPException(404, detail=f"sample {sample_id!r} unavailable") from None
    return meta, text


@router.get("/subtitle-tuning/samples")
def list_subtitle_samples() -> dict:
    """List the bounded static preview samples (+ their cue metrics). The UI
    renders one card per sample; loading one feeds the preview operation."""
    items = []
    for sid, meta in _SAMPLES.items():
        try:
            _, text = _read_sample(sid)
            metrics = cue_metrics(text)
        except HTTPException:
            metrics = {"cue_count": 0, "critical_cues": 0}
        items.append(
            {
                "id": sid,
                "name": meta["name"],
                "description": meta["description"],
                "language": meta["language"],
                "cue_count": metrics["cue_count"],
            }
        )
    return {"samples": items}


@router.get("/subtitle-tuning/samples/{sample_id}")
def get_subtitle_sample(sample_id: str) -> dict:
    """Fetch one bundled sample's subtitle text for the preview UI. Bounded to
    the manifest + static dir (see _read_sample); never touches media."""
    meta, text = _read_sample(sample_id)
    return {
        "id": sample_id,
        "name": meta["name"],
        "language": meta["language"],
        "text": text,
        **cue_metrics(text),
    }


class RetimerOverrides(BaseModel):
    """Transient draft overrides for the preview. The same five numeric fields
    with the same Canonical Bounds as SubtitleTuningBody — each supplied value
    is merged over the live settings for the preview response ONLY. Never
    persisted, never applied to the running Settings."""

    model_config = {"extra": "forbid"}

    target_cps: float | None = Field(default=None, ge=5.0, le=25.0)
    min_cue_ms: int | None = Field(default=None, ge=100, le=5000)
    min_gap_ms: int | None = Field(default=None, ge=0, le=1000)
    max_cue_ms: int | None = Field(default=None, ge=1000, le=15000)
    max_borrow_ms: int | None = Field(default=None, ge=0, le=5000)


class SubtitlePreviewBody(BaseModel):
    """Preview request — exactly ONE of `text` or `sample_id` must be supplied.
    `overrides` optionally carries validated transient draft values (see
    RetimerOverrides): merged with the live settings for this preview only."""

    text: str | None = Field(default=None, max_length=_PREVIEW_MAX_CHARS)
    sample_id: str | None = None
    overrides: RetimerOverrides | None = None


@router.post("/subtitle-tuning/preview")
def preview_subtitle_tuning(body: SubtitlePreviewBody) -> dict:
    """Side-effect-free preview: merge the transient draft overrides over the
    live RetimeParams, run the retimer on bounded sample text, and return the
    original, the retimed result, concise cue metrics, and the merged params.
    NEVER writes media, sidecars, or persisted settings; NEVER mutates the live
    Settings singleton."""
    if (body.text is not None) == (body.sample_id is not None):
        raise HTTPException(400, detail="supply exactly one of text or sample_id (not both, not neither)")
    if body.sample_id is not None:
        _, text = _read_sample(body.sample_id)
    else:
        text = body.text or ""
        if not text.strip():
            raise HTTPException(400, detail="preview text must not be empty")

    # Merged params: live settings + validated transient overrides. Overrides
    # are applied by constructing a NEW RetimeParams (replace on the frozen
    # dataclass) — never mutating the settings singleton or its store.
    params = retime_params_from_settings(config.settings)
    overridden = body.overrides.model_dump(exclude_none=True) if body.overrides else {}
    if overridden:
        params = replace(params, **overridden)
    overrides_applied = bool(overridden)

    # Cross-field sanity on the MERGED state (same detail shape as the PUT
    # check): a cue must never be allowed to end before it can start.
    if params.max_cue_ms < params.min_cue_ms:
        raise HTTPException(
            422,
            detail=[
                {
                    "loc": ["body", "max_cue_ms"],
                    "msg": "max_cue_ms must be >= min_cue_ms",
                    "type": "value_error",
                }
            ],
        )

    retimed = retime_srt(text, params)

    return {
        "original": text,
        "retimed": retimed,
        "params": {
            "target_cps": params.target_cps,
            "min_cue_ms": params.min_cue_ms,
            "min_gap_ms": params.min_gap_ms,
            "max_cue_ms": params.max_cue_ms,
            "max_borrow_ms": params.max_borrow_ms,
        },
        "overrides_applied": overrides_applied,
        "metrics": {
            "before": cue_metrics(text),
            "after": cue_metrics(retimed),
        },
        "source": {"sample_id": body.sample_id, "custom_text": body.sample_id is None},
    }
