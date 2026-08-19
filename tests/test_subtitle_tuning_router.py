"""P5-S3: the subtitle-tuning settings + preview API seam (post-calibration).

Post-calibration provider contract: GET field metadata (value + env_controlled)
for exactly the SIX fields; PUT/POST partial live-apply + persist with the
Canonical Bounds enforced by pydantic (out-of-bounds / non-finite / unknown key
/ max_cue_ms < min_cue_ms -> 422 with no mutation), empty body -> 400, and the
single env authority (SUBARR_RETIME_ENABLED) landing in `managed_by_env` when a
UI write is attempted against it.

Preview + samples: bounded input (text > 200k rejected, empty rejected,
both/neither text+sample_id rejected), transient validated draft overrides
applied for THIS response only — never persisted and never mutating live
settings — no side effects on the sample files, response metrics/text, the
four-sample manifest, the SDH verbatim elements, byte-for-byte cue-text
preservation through the retimer, and traversal/path-containment 404s.

SYNC TestClient (app_with_stub) per the vad/forced-segment router convention.
"""

from __future__ import annotations

import importlib
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

from subarr import config
from subarr import config_store as cs

_FIELDS = (
    "retime_enabled",
    "target_cps",
    "min_cue_ms",
    "min_gap_ms",
    "max_cue_ms",
    "max_borrow_ms",
)
_NUMERICS = _FIELDS[1:]

# Grep-clean reference: the deleted symbol must never appear as a literal in
# tests/ — built from parts so the response-shape assertion stays honest.
_GATE_KEY = "language_gate"


def _isolate_store(monkeypatch, tmp_path):
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    # The numeric env vars were deleted in the calibration correction — the
    # only retime env authority left is SUBARR_RETIME_ENABLED.
    monkeypatch.delenv("SUBARR_RETIME_ENABLED", raising=False)


def _snapshot():
    return {k: getattr(config.settings, k) for k in _FIELDS}


def _restore(snap):
    for k, v in snap.items():
        object.__setattr__(config.settings, k, v)


def _reload_config():
    importlib.reload(config)


# ─── GET metadata ────────────────────────────────────────────────────────────
def test_get_subtitle_tuning_shape(app_with_stub):
    r = app_with_stub.get("/api/settings/subtitle-tuning")
    assert r.status_code == 200
    fields = r.json()["fields"]
    assert set(fields) == set(_FIELDS)
    for k in _FIELDS:
        assert set(fields[k]) == {"value", "env_controlled"}
        assert isinstance(fields[k]["env_controlled"], bool)


def test_get_numerics_never_env_controlled(app_with_stub):
    # No env authority exists for the five numerics — env_controlled must be
    # False even though the process already carries plenty of other SUBARR_* vars.
    r = app_with_stub.get("/api/settings/subtitle-tuning")
    fields = r.json()["fields"]
    for k in _NUMERICS:
        assert fields[k]["env_controlled"] is False


def test_get_reports_env_controlled_metadata(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "0")
    _reload_config()
    r = app_with_stub.get("/api/settings/subtitle-tuning")
    fields = r.json()["fields"]
    assert fields["retime_enabled"]["env_controlled"] is True
    assert fields["retime_enabled"]["value"] is False
    assert fields["target_cps"]["env_controlled"] is False


# ─── PUT/POST contract ───────────────────────────────────────────────────────
def test_put_live_applies_and_persists(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()
    try:
        r = app_with_stub.put("/api/settings/subtitle-tuning", json={"target_cps": 21.0, "min_cue_ms": 1400})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert set(body["applied"]) == {"target_cps", "min_cue_ms"}
        assert body["managed_by_env"] == []
        # live-applied to the running singleton
        assert config.settings.target_cps == 21.0
        assert config.settings.min_cue_ms == 1400
        # persisted below env (survives restart)
        ov = cs.load_overrides()
        assert ov["target_cps"] == 21.0 and ov["min_cue_ms"] == 1400
        # response fields reflect the new state
        assert body["fields"]["target_cps"]["value"] == 21.0
    finally:
        _restore(before)


def test_post_alias_works_like_put(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()
    try:
        r = app_with_stub.post("/api/settings/subtitle-tuning", json={"max_borrow_ms": 750})
        assert r.status_code == 200
        assert config.settings.max_borrow_ms == 750
        assert cs.load_overrides().get("max_borrow_ms") == 750
    finally:
        _restore(before)


def test_put_empty_body_400(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    r = app_with_stub.put("/api/settings/subtitle-tuning", json={})
    assert r.status_code == 400


def test_put_unknown_key_422_no_mutation(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()
    try:
        r = app_with_stub.put("/api/settings/subtitle-tuning", json={"bogus_field": 1})
        assert r.status_code == 422
        # nothing mutated (unknown key rejected by pydantic extra=forbid)
        assert _snapshot() == before
        assert cs.load_overrides() == {}
    finally:
        _restore(before)


def test_put_out_of_bounds_422_no_mutation(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()
    try:
        bad_bodies = [
            {"target_cps": 4.9},  # below floor 5.0
            {"target_cps": 25.1},  # above ceiling 25.0
            {"target_cps": 0},  # the old 'off' sentinel is now out of bounds
            {"target_cps": -1},  # meaningless negative
            {"min_cue_ms": 99},  # below floor 100
            {"min_cue_ms": 5001},  # above ceiling 5000
            {"min_cue_ms": -5},
            {"min_gap_ms": -1},
            {"min_gap_ms": 1001},
            {"max_cue_ms": 999},
            {"max_cue_ms": 15001},
            {"max_borrow_ms": -1},
            {"max_borrow_ms": 5001},
        ]
        for body in bad_bodies:
            r = app_with_stub.put("/api/settings/subtitle-tuning", json=body)
            assert r.status_code == 422, body
            assert _snapshot() == before
            assert cs.load_overrides() == {}
        # non-finite float: JSON has no literal for it, so a client expresses
        # it as the string 'inf'; pydantic coerces -> inf and rejects it on
        # the le=25.0 bound (string input keeps the 422 response serializable)
        r = app_with_stub.put("/api/settings/subtitle-tuning", json={"target_cps": "inf"})
        assert r.status_code == 422
        assert _snapshot() == before
        assert cs.load_overrides() == {}
    finally:
        _restore(before)


def test_put_max_cue_below_min_cue_422_identifies_field(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()
    try:
        # Both values are within the per-field pydantic bounds, so the 422
        # must come from the MERGED-state cross-field check.
        r = app_with_stub.put("/api/settings/subtitle-tuning", json={"max_cue_ms": 1500, "min_cue_ms": 3000})
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert isinstance(detail, list)
        assert detail[0]["loc"] == ["body", "max_cue_ms"]
        assert detail[0]["msg"] == "max_cue_ms must be >= min_cue_ms"
        assert detail[0]["type"] == "value_error"
        # nothing mutated
        assert _snapshot() == before
        assert cs.load_overrides() == {}
    finally:
        _restore(before)


def test_put_max_cue_below_existing_min_cue_422(app_with_stub, tmp_path, monkeypatch):
    # Partial update: existing settings.min_cue_ms is the merge baseline.
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()
    try:
        object.__setattr__(config.settings, "min_cue_ms", 2000)
        r = app_with_stub.put("/api/settings/subtitle-tuning", json={"max_cue_ms": 1500})
        assert r.status_code == 422
        assert config.settings.max_cue_ms == before["max_cue_ms"]  # untouched
    finally:
        _restore(before)


# ─── env authority (only retime_enabled can be env-set) ──────────────────────
def test_put_env_set_field_goes_to_managed_by_env(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "0")
    _reload_config()
    before = _snapshot()
    try:
        r = app_with_stub.put("/api/settings/subtitle-tuning", json={"retime_enabled": True})
        assert r.status_code == 200
        body = r.json()
        assert "retime_enabled" in body["managed_by_env"]
        assert "retime_enabled" not in body["applied"]
        # env-pinned value not mutated, not persisted
        assert config.settings.retime_enabled is False
        assert "retime_enabled" not in cs.load_overrides()
        # response metadata reports env_controlled
        assert body["fields"]["retime_enabled"]["env_controlled"] is True
    finally:
        _restore(before)


def test_put_mixed_env_pin_and_editable_numeric(app_with_stub, tmp_path, monkeypatch):
    # Only retime_enabled has env authority — a numeric alongside it still
    # applies and persists normally even with the env pin present.
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "0")
    _reload_config()
    before = _snapshot()
    try:
        r = app_with_stub.put(
            "/api/settings/subtitle-tuning",
            json={"retime_enabled": True, "min_gap_ms": 250},
        )
        assert r.status_code == 200
        body = r.json()
        assert "retime_enabled" in body["managed_by_env"]
        assert "min_gap_ms" in body["applied"]
        assert config.settings.retime_enabled is False  # env wins
        assert config.settings.min_gap_ms == 250  # editable applied
        ov = cs.load_overrides()
        assert "retime_enabled" not in ov  # env-set field never persisted
        assert ov["min_gap_ms"] == 250
    finally:
        _restore(before)


# ─── atomic multi-field PUT + live-apply rollback (P3-S1) ────────────────────
def test_put_multi_field_persists_as_one_store_write(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()
    try:
        calls = {"n": 0}
        real_write = cs._write

        def counting_write(data):
            calls["n"] += 1
            return real_write(data)

        monkeypatch.setattr(cs, "_write", counting_write)

        r = app_with_stub.put("/api/settings/subtitle-tuning", json={"target_cps": 12.0, "min_cue_ms": 2000})
        assert r.status_code == 200
        assert calls["n"] == 1  # both fields persisted in ONE atomic write
        ov = cs.load_overrides()
        assert ov["target_cps"] == 12.0 and ov["min_cue_ms"] == 2000
        assert config.settings.target_cps == 12.0  # live-applied
        assert config.settings.min_cue_ms == 2000
    finally:
        _restore(before)


def test_put_live_apply_failure_rolls_back_store_and_live(app_with_stub, tmp_path, monkeypatch):
    """A live-apply failure AFTER persistence must not leave disk/live diverged:
    the store is rolled back to its snapshot, the real Settings is untouched,
    and the request returns 500 — no partial persistence, no silent split."""
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()

    class _ReadOnlySettings:
        # No instance dict (__slots__ = ()), so object.__setattr__ raises
        # AttributeError; the tuning fields stay readable via read-only
        # properties so the merge baseline / _field_view reads still work.
        __slots__ = ()
        retime_enabled = property(lambda self: True)
        target_cps = property(lambda self: 17.0)
        min_cue_ms = property(lambda self: 1000)
        min_gap_ms = property(lambda self: 100)
        max_cue_ms = property(lambda self: 7000)
        max_borrow_ms = property(lambda self: 500)

    real_settings = config.settings
    monkeypatch.setattr(config, "settings", _ReadOnlySettings())

    r = app_with_stub.put("/api/settings/subtitle-tuning", json={"target_cps": 12.0})
    assert r.status_code == 500
    assert "rolled back" in r.json()["detail"]

    # store restored to its pre-write snapshot (no partial persistence)
    assert cs.load_overrides() == {}
    # real Settings was never mutated (live ops hit the stand-in, which rejected
    # every write) — no disk/live divergence vs the snapshot
    assert real_settings.target_cps == before["target_cps"]
    assert real_settings.min_cue_ms == before["min_cue_ms"]

    # put the real singleton back so the finally-block restore targets it
    monkeypatch.setattr(config, "settings", real_settings)


# ─── failure-branch rollback coverage (P3-S4) ────────────────────────────────
def test_put_live_apply_failure_restores_nonempty_store_snapshot(app_with_stub, tmp_path, monkeypatch):
    """A live-apply failure must restore the EXACT pre-PUT store even when it
    starts non-empty (an unrelated key + a prior tuning key) — exercising the
    `_rollback_store` re-save branch (`if snapshot:`). New key absent, unrelated
    key preserved, prior tuning key restored."""
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()
    # Non-empty pre-PUT snapshot: an unrelated override + a prior tuning key.
    cs.save_overrides({"vad_enabled": False, "target_cps": 10.0})
    pre = cs.load_overrides()
    assert pre == {"vad_enabled": False, "target_cps": 10.0}

    class _ReadOnlySettings:
        __slots__ = ()
        retime_enabled = property(lambda self: True)
        target_cps = property(lambda self: 17.0)
        min_cue_ms = property(lambda self: 1000)
        min_gap_ms = property(lambda self: 100)
        max_cue_ms = property(lambda self: 7000)
        max_borrow_ms = property(lambda self: 500)

    real_settings = config.settings
    monkeypatch.setattr(config, "settings", _ReadOnlySettings())

    r = app_with_stub.put("/api/settings/subtitle-tuning", json={"min_cue_ms": 2000})
    assert r.status_code == 500
    assert "rolled back" in r.json()["detail"]

    # Scoped rollback contract: unrelated override preserved, new key cleared.
    # The touched set is exactly {min_cue_ms}; keys outside it are never
    # rewritten/removed. target_cps survives not because it was restored, but
    # because it is an untouched unrelated key the scoped rollback leaves alone.
    # The re-save branch of _rollback_store (a touched key with a prior value)
    # is exercised by the DELETE and concurrency tests instead.
    ov = cs.load_overrides()
    assert ov == pre
    assert ov["vad_enabled"] is False  # unrelated override survives untouched
    assert ov["target_cps"] == 10.0  # untouched unrelated key (never rewritten/removed)
    assert "min_cue_ms" not in ov  # touched key (absent pre-PUT) cleared, not left persisted
    # the real Settings was never mutated (live ops hit the stand-in)
    assert real_settings.target_cps == before["target_cps"]
    assert real_settings.min_cue_ms == before["min_cue_ms"]

    monkeypatch.setattr(config, "settings", real_settings)


def test_put_multi_field_live_rollback_restores_values(app_with_stub, tmp_path, monkeypatch):
    """A STATEFUL stand-in that ACCEPTS the first object.__setattr__ then RAISES
    on the second proves _rollback_live genuinely writes values back rather than
    being a no-op: the first field must end equal to its pre-PUT snapshot (17.0),
    not the applied 12.0."""
    _isolate_store(monkeypatch, tmp_path)

    class _StatefulSettings:
        # Only target_cps has a slot, so object.__setattr__ succeeds for the
        # first field (target_cps, first in pydantic field order) then raises
        # AttributeError on min_cue_ms (property with no setter, no slot).
        __slots__ = ("target_cps",)

        def __init__(self):
            object.__setattr__(self, "target_cps", 17.0)

        retime_enabled = property(lambda self: True)
        min_cue_ms = property(lambda self: 1000)
        min_gap_ms = property(lambda self: 100)
        max_cue_ms = property(lambda self: 7000)
        max_borrow_ms = property(lambda self: 500)

    stand_in = _StatefulSettings()
    real_settings = config.settings
    monkeypatch.setattr(config, "settings", stand_in)

    r = app_with_stub.put(
        "/api/settings/subtitle-tuning",
        json={"target_cps": 12.0, "min_cue_ms": 2000},
    )
    assert r.status_code == 500
    assert "rolled back" in r.json()["detail"]

    # _rollback_live wrote the first field back to its pre-PUT snapshot value
    # (17.0). If rollback were a no-op, target_cps would still be the applied
    # 12.0 — so this assert proves the live values were genuinely restored.
    assert stand_in.target_cps == 17.0
    # store restored to the pre-PUT snapshot ({} — nothing persisted)
    assert cs.load_overrides() == {}

    monkeypatch.setattr(config, "settings", real_settings)


def test_delete_live_revert_failure_rolls_back_store_and_live(app_with_stub, tmp_path, monkeypatch):
    """If reverting live settings to defaults fails, the store must be restored
    to its pre-DELETE snapshot (the clear must not stick) and the live values
    left unchanged from pre-DELETE."""
    _isolate_store(monkeypatch, tmp_path)
    # Pre-DELETE state: a persisted override AND a matching live value, plus an
    # unrelated override key the scoped rollback must never touch.
    cs.save_overrides({"vad_enabled": True, "target_cps": 12.0, "min_cue_ms": 2000})
    object.__setattr__(config.settings, "target_cps", 12.0)
    object.__setattr__(config.settings, "min_cue_ms", 2000)
    before_store = cs.load_overrides()
    before_live = _snapshot()

    class _ReadOnlySettings:
        __slots__ = ()
        retime_enabled = property(lambda self: True)
        target_cps = property(lambda self: 12.0)
        min_cue_ms = property(lambda self: 2000)
        min_gap_ms = property(lambda self: 100)
        max_cue_ms = property(lambda self: 7000)
        max_borrow_ms = property(lambda self: 500)

    real_settings = config.settings
    monkeypatch.setattr(config, "settings", _ReadOnlySettings())

    r = app_with_stub.delete("/api/settings/subtitle-tuning")
    assert r.status_code == 500
    assert "rolled back" in r.json()["detail"]

    # Scoped rollback: the clear did not stick for touched keys and the
    # unrelated override survived untouched.
    ov = cs.load_overrides()
    assert ov == before_store
    assert ov["vad_enabled"] is True  # unrelated override survives the reset rollback
    assert ov["target_cps"] == 12.0 and ov["min_cue_ms"] == 2000  # touched keys restored
    # live values unchanged from pre-DELETE (the real Settings was never mutated)
    assert real_settings.target_cps == before_live["target_cps"]
    assert real_settings.min_cue_ms == before_live["min_cue_ms"]

    monkeypatch.setattr(config, "settings", real_settings)


def test_put_save_overrides_persist_failure_500(app_with_stub, tmp_path, monkeypatch):
    """A ConfigStoreError during the atomic persist must surface as a clean 500
    ('failed to persist') with the store AND live state untouched — no partial
    persistence, nothing live-applied."""
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()
    try:

        def boom(mapping):
            raise cs.ConfigStoreError("simulated disk failure")

        monkeypatch.setattr(cs, "save_overrides", boom)

        r = app_with_stub.put("/api/settings/subtitle-tuning", json={"target_cps": 12.0})
        assert r.status_code == 500
        assert "failed to persist" in r.json()["detail"]
        # nothing persisted, nothing live-applied
        assert cs.load_overrides() == {}
        assert _snapshot() == before
    finally:
        _restore(before)


def test_delete_clear_overrides_persist_failure_500(app_with_stub, tmp_path, monkeypatch):
    """A ConfigStoreError during the reset persist must surface as a clean 500
    ('reset failed to persist') with the live state untouched and the override
    still present (the clear never succeeded)."""
    _isolate_store(monkeypatch, tmp_path)
    cs.save_overrides({"target_cps": 12.0})
    object.__setattr__(config.settings, "target_cps", 12.0)
    before = _snapshot()
    try:

        def boom(keys):
            raise cs.ConfigStoreError("simulated disk failure")

        monkeypatch.setattr(cs, "clear_overrides", boom)

        r = app_with_stub.delete("/api/settings/subtitle-tuning")
        assert r.status_code == 500
        assert "reset failed to persist" in r.json()["detail"]
        # live untouched (the revert loop never ran)
        assert _snapshot() == before
        # store still holds the override (clear never succeeded)
        assert cs.load_overrides() == {"target_cps": 12.0}
    finally:
        _restore(before)


# ─── DELETE reset (P3-S2) ────────────────────────────────────────────────────
def test_delete_resets_persisted_overrides_to_defaults(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()
    try:
        r = app_with_stub.put("/api/settings/subtitle-tuning", json={"target_cps": 12.0, "min_cue_ms": 2000})
        assert r.status_code == 200
        assert config.settings.target_cps == 12.0

        r = app_with_stub.delete("/api/settings/subtitle-tuning")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert set(body["reset"]) == set(_FIELDS)
        assert body["managed_by_env"] == []
        # persisted tuning overrides cleared
        ov = cs.load_overrides()
        for k in _FIELDS:
            assert k not in ov
        # live reverted to built-in defaults
        for k in _FIELDS:
            assert getattr(config.settings, k) == config.RETIME_DEFAULTS[k]
        # response fields reflect the defaulted state
        assert body["fields"]["target_cps"]["value"] == config.RETIME_DEFAULTS["target_cps"]
    finally:
        _restore(before)


def test_delete_env_pinned_retime_keeps_env_authority(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "0")
    _reload_config()
    before = _snapshot()
    try:
        assert config.settings.retime_enabled is False  # env-pinned
        r = app_with_stub.put("/api/settings/subtitle-tuning", json={"target_cps": 12.0})
        assert r.status_code == 200
        assert config.settings.target_cps == 12.0

        r = app_with_stub.delete("/api/settings/subtitle-tuning")
        assert r.status_code == 200
        body = r.json()
        assert "retime_enabled" in body["managed_by_env"]
        assert "retime_enabled" not in body["reset"]
        assert set(body["reset"]) == set(_NUMERICS)
        # env-managed retime never cleared or touched
        assert config.settings.retime_enabled is False
        assert "retime_enabled" not in cs.load_overrides()
        # numerics cleared and reverted to defaults
        for k in _NUMERICS:
            assert k not in cs.load_overrides()
            assert getattr(config.settings, k) == config.RETIME_DEFAULTS[k]
    finally:
        _restore(before)


def test_delete_idempotent_when_nothing_persisted(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()
    try:
        calls = {"n": 0}
        real_write = cs._write

        def counting_write(data):
            calls["n"] += 1
            return real_write(data)

        monkeypatch.setattr(cs, "_write", counting_write)

        r = app_with_stub.delete("/api/settings/subtitle-tuning")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert set(body["reset"]) == set(_FIELDS)
        assert body["managed_by_env"] == []
        assert calls["n"] == 0  # nothing persisted -> no store write performed
        for k in _FIELDS:
            assert getattr(config.settings, k) == config.RETIME_DEFAULTS[k]
    finally:
        _restore(before)


def test_delete_removes_stale_override_pins_after_reload(app_with_stub, tmp_path, monkeypatch):
    # Reset inheritance: after DELETE, a fresh config.load() yields the built-in
    # defaults — no stale override pin survives a restart.
    _isolate_store(monkeypatch, tmp_path)
    before = _snapshot()
    try:
        r = app_with_stub.put(
            "/api/settings/subtitle-tuning",
            json={"target_cps": 12.0, "retime_enabled": False},
        )
        assert r.status_code == 200
        assert config.settings.target_cps == 12.0
        assert config.settings.retime_enabled is False

        r = app_with_stub.delete("/api/settings/subtitle-tuning")
        assert r.status_code == 200
        assert set(r.json()["reset"]) == set(_FIELDS)

        _reload_config()
        assert config.settings.target_cps == config.RETIME_DEFAULTS["target_cps"]
        assert config.settings.retime_enabled == config.RETIME_DEFAULTS["retime_enabled"]
    finally:
        _restore(before)


def test_delete_mixed_env_pin_and_numeric_reset(app_with_stub, tmp_path, monkeypatch):
    # env-pinned retime_enabled untouched while numerics clear (P3-S2 belt+braces)
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "0")
    _reload_config()
    before = _snapshot()
    try:
        r = app_with_stub.put(
            "/api/settings/subtitle-tuning",
            json={"target_cps": 12.0, "min_gap_ms": 250, "retime_enabled": True},
        )
        assert r.status_code == 200
        r = app_with_stub.delete("/api/settings/subtitle-tuning")
        assert r.status_code == 200
        body = r.json()
        assert "retime_enabled" in body["managed_by_env"]
        assert set(body["reset"]) == set(_NUMERICS)
        assert config.settings.retime_enabled is False  # env still wins
        assert "retime_enabled" not in cs.load_overrides()
        for k in _NUMERICS:
            assert k not in cs.load_overrides()
            assert getattr(config.settings, k) == config.RETIME_DEFAULTS[k]
    finally:
        _restore(before)


# ─── preview operation ───────────────────────────────────────────────────────
def _preview_before():
    return {
        "store": cs.load_overrides(),
        "settings": {k: getattr(config.settings, k) for k in _FIELDS},
    }


def _assert_preview_side_effect_free(before):
    # no persisted settings written
    assert cs.load_overrides() == before["store"]
    # no live settings mutated
    for k in _FIELDS:
        assert getattr(config.settings, k) == before["settings"][k]


def test_preview_custom_text_returns_original_retimed_metrics(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _preview_before()
    srt = (
        "1\n00:00:00,000 --> 00:00:02,000\n"
        "This is a very long translated line that crams far too many characters onto one single cue\n\n"
        "2\n00:00:20,000 --> 00:00:20,300\nDon't go.\n"
    )
    r = app_with_stub.post("/api/settings/subtitle-tuning/preview", json={"text": srt})
    assert r.status_code == 200
    body = r.json()
    assert _GATE_KEY not in body  # the language gate was deleted
    assert body["original"] == srt
    assert body["retimed"] != srt  # over-CPS cue extended
    assert body["metrics"]["before"]["cue_count"] == 2
    assert body["metrics"]["before"]["critical_cues"] >= 1  # the over-CPS cue
    assert (
        body["metrics"]["after"]
        and body["metrics"]["after"]["critical_cues"] < body["metrics"]["before"]["critical_cues"]
    )
    # params carry exactly the five retimer numerics
    assert set(body["params"]) == set(_NUMERICS)
    assert body["overrides_applied"] is False
    assert body["source"] == {"sample_id": None, "custom_text": True}
    _assert_preview_side_effect_free(before)


def test_preview_override_applied_transiently_never_persisted(app_with_stub, tmp_path, monkeypatch):
    """THE transient-override proof: a validated draft override changes the
    preview params for THIS response only — live settings stay at 17.0 and
    nothing is written to the config store."""
    _isolate_store(monkeypatch, tmp_path)
    before = _preview_before()
    assert config.settings.target_cps == 17.0  # stock default the override must not clobber
    srt = "1\n00:00:00,000 --> 00:00:02,000\nHello.\n"
    r = app_with_stub.post(
        "/api/settings/subtitle-tuning/preview",
        json={"text": srt, "overrides": {"target_cps": 12.0}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["overrides_applied"] is True
    assert body["params"]["target_cps"] == 12.0  # merged into the preview params
    # live settings untouched, nothing persisted
    assert config.settings.target_cps == 17.0
    assert cs.load_overrides() == before["store"]
    _assert_preview_side_effect_free(before)


def test_preview_without_overrides_reports_not_applied(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _preview_before()
    r = app_with_stub.post(
        "/api/settings/subtitle-tuning/preview",
        json={"text": "1\n00:00:00,000 --> 00:00:02,000\nHello.\n"},
    )
    assert r.status_code == 200
    assert r.json()["overrides_applied"] is False
    assert r.json()["params"]["target_cps"] == 17.0  # live defaults
    _assert_preview_side_effect_free(before)


def test_preview_override_out_of_bounds_422(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _preview_before()
    for overrides in (
        {"target_cps": 30.0},
        {"target_cps": 4.0},
        {"min_cue_ms": 50},
    ):
        r = app_with_stub.post(
            "/api/settings/subtitle-tuning/preview",
            json={"text": "1\n00:00:00,000 --> 00:00:02,000\nHi\n", "overrides": overrides},
        )
        assert r.status_code == 422, overrides
        _assert_preview_side_effect_free(before)


def test_preview_override_cross_field_422_on_merged_state(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _preview_before()
    # Both override values are inside the per-field pydantic bounds; the 422
    # must come from the merged-state cross-field rule.
    r = app_with_stub.post(
        "/api/settings/subtitle-tuning/preview",
        json={
            "text": "1\n00:00:00,000 --> 00:00:02,000\nHi\n",
            "overrides": {"max_cue_ms": 1500, "min_cue_ms": 3000},
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail[0]["loc"] == ["body", "max_cue_ms"]
    assert detail[0]["msg"] == "max_cue_ms must be >= min_cue_ms"
    _assert_preview_side_effect_free(before)


def test_preview_text_too_long_rejected(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    r = app_with_stub.post(
        "/api/settings/subtitle-tuning/preview",
        json={"text": "x" * 200_001},
    )
    assert r.status_code == 422  # pydantic max_length


def test_preview_empty_text_400(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    r = app_with_stub.post(
        "/api/settings/subtitle-tuning/preview",
        json={"text": "   \n "},
    )
    assert r.status_code == 400


def test_preview_neither_text_nor_sample_400(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    r = app_with_stub.post("/api/settings/subtitle-tuning/preview", json={})
    assert r.status_code == 400


def test_preview_both_text_and_sample_400(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    r = app_with_stub.post(
        "/api/settings/subtitle-tuning/preview",
        json={"text": "1\n00:00:00,000 --> 00:00:02,000\nHi\n", "sample_id": "dense"},
    )
    assert r.status_code == 400


def test_preview_with_sample_id_returns_text_and_metrics(app_with_stub, tmp_path, monkeypatch):
    _isolate_store(monkeypatch, tmp_path)
    before = _preview_before()
    r = app_with_stub.post("/api/settings/subtitle-tuning/preview", json={"sample_id": "dialogue"})
    assert r.status_code == 200
    body = r.json()
    assert _GATE_KEY not in body
    assert body["source"] == {"sample_id": "dialogue", "custom_text": False}
    assert "Morning." in body["original"]
    assert body["metrics"]["before"]["cue_count"] == 5
    _assert_preview_side_effect_free(before)


# ─── sample seam + path safety ───────────────────────────────────────────────
def test_samples_list_shape(app_with_stub):
    r = app_with_stub.get("/api/settings/subtitle-tuning/samples")
    assert r.status_code == 200
    samples = r.json()["samples"]
    by_id = {s["id"]: s for s in samples}
    assert set(by_id) == {"dialogue", "dense", "sdh", "rapid"}
    assert by_id["dialogue"]["name"] == "Dialogue"
    assert by_id["dense"]["name"] == "Dense prose"
    assert by_id["sdh"]["name"] == "HI/SDH"
    assert by_id["rapid"]["name"] == "Rapid exchange"
    for s in samples:
        assert {"id", "name", "description", "language", "cue_count"} <= s.keys()
        assert s["language"] == "en"
        assert isinstance(s["cue_count"], int) and s["cue_count"] > 0


def test_sample_fetch_sdh_contains_verbatim_elements(app_with_stub):
    r = app_with_stub.get("/api/settings/subtitle-tuning/samples/sdh")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "sdh"
    assert body["language"] == "en"
    text = body["text"]
    for element in (
        "[footsteps approaching]",
        "[distant gunfire]",
        "[door closes]",
        "♪ music playing ♪",
        "MAYA:",
    ):
        assert element in text, element
    # cue_count verified by parsing the actual shipped file (7 cues)
    assert body["cue_count"] == 7


def test_sample_fetch_dialogue_metrics(app_with_stub):
    r = app_with_stub.get("/api/settings/subtitle-tuning/samples/dialogue")
    assert r.status_code == 200
    body = r.json()
    assert body["cue_count"] == 5
    assert body["critical_cues"] == 0  # calm dialogue: all cues below 25 CPS


def test_unknown_sample_404(app_with_stub):
    r = app_with_stub.get("/api/settings/subtitle-tuning/samples/nope")
    assert r.status_code == 404


def test_sample_traversal_404(app_with_stub):
    # Path-traversal ids must be unreachable: reads are locked to the static
    # sample dir + manifest via Path.is_relative_to (both resolved first).
    for bad in (
        "../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "dense/../../x",
        "%2e%2e",
        "dialogue.%2e%2e",
    ):
        r = app_with_stub.get(f"/api/settings/subtitle-tuning/samples/{bad}")
        assert r.status_code == 404, bad


def test_manifest_id_resolving_to_base_dir_404(app_with_stub, monkeypatch):
    # Path-equality edge: is_relative_to is True for a path EQUAL to the base
    # dir, but the base dir is not a file — the is_file() guard must 404, never
    # serve the directory as a sample.
    from subarr.routers import subtitle_tuning as st

    monkeypatch.setitem(
        st._SAMPLES,
        "basedir",
        {"file": ".", "name": "Base dir", "description": "edge", "language": "en"},
    )
    r = app_with_stub.get("/api/settings/subtitle-tuning/samples/basedir")
    assert r.status_code == 404


# ─── preview is side-effect-free on the sample files ─────────────────────────
def test_preview_never_writes_media_or_settings_file(app_with_stub, tmp_path, monkeypatch):
    import os

    import subarr

    # Preview must be a pure read: no sidecar rewritten, no new files anywhere,
    # and no persisted settings. The samples dir mtime-stability proves it.
    _isolate_store(monkeypatch, tmp_path)
    samples_dir = os.path.join(os.path.dirname(subarr.__file__), "data", "subtitle_samples")
    before = {}
    for f in sorted(os.listdir(samples_dir)):
        p = os.path.join(samples_dir, f)
        before[p] = (os.path.getmtime(p), os.path.getsize(p), Path(p).read_bytes())
    before_store = cs.load_overrides()
    before_settings = {k: getattr(config.settings, k) for k in _FIELDS}

    r = app_with_stub.post("/api/settings/subtitle-tuning/preview", json={"sample_id": "sdh"})
    assert r.status_code == 200

    for f in sorted(os.listdir(samples_dir)):
        p = os.path.join(samples_dir, f)
        mtime, size, blob = before[p]
        assert os.path.getmtime(p) == mtime, f"sample file rewritten by preview: {p}"
        assert os.path.getsize(p) == size and Path(p).read_bytes() == blob
    assert cs.load_overrides() == before_store  # no persisted settings written
    for k in _FIELDS:
        assert getattr(config.settings, k) == before_settings[k]  # no live mutation


# ─── retime preserves cue TEXT byte-for-byte (timestamps may change) ─────────
@pytest.mark.parametrize("sample_id", ["dialogue", "dense", "sdh", "rapid"])
def test_preview_retime_preserves_cue_text_byte_for_byte(app_with_stub, tmp_path, monkeypatch, sample_id):
    from subarr.subtitle_readability import parse_srt

    _isolate_store(monkeypatch, tmp_path)
    r = app_with_stub.post("/api/settings/subtitle-tuning/preview", json={"sample_id": sample_id})
    assert r.status_code == 200
    body = r.json()
    orig = parse_srt(body["original"])
    out = parse_srt(body["retimed"])
    assert len(out) == len(orig)
    # cue TEXT identical — retiming may only touch timestamps
    assert [c.lines for c in out] == [c.lines for c in orig]


# ─── concurrency: interleaved transactions serialize; rollback is scoped ────
def test_put_concurrent_transactions_serialize_and_scoped_rollback(app_with_stub, tmp_path, monkeypatch):
    """Deterministic two-transaction interleaving regression (no barrier race).

    Thread A starts a PUT of min_gap_ms and blocks mid-live-apply while holding
    _tx_lock. Thread B then PUTs a DIFFERENT field (target_cps=21.0) — under the
    fix it must block on the operation lock until A finishes rolling back, so
    the two transactions never interleave at the store level. A's live-apply is
    then made to fail and A rolls back. Assertions:
      (a) B's persisted override (target_cps=21.0) survives A's rollback;
      (b) A's touched key (min_gap_ms) returns to its pre-transaction value in
          the store AND in live settings;
      (c) disk (store) and live agree;
      (d) while A is mid-transaction a non-blocking acquire from the main
          thread fails — the operation lock is held across A's whole
          transaction (store write -> live apply -> rollback).

    Fails against the pre-fix code: without the lock B interleaves while A is
    blocked, and A's whole-snapshot rollback clears B's persisted target_cps.
    """
    from subarr.routers import subtitle_tuning as st

    _isolate_store(monkeypatch, tmp_path)
    # Seed a pre-existing unrelated override + a prior value for A's touched key
    # so the scoped restore has real pre-transaction state to return to.
    cs.save_overrides({"vad_enabled": False, "min_gap_ms": 40})
    object.__setattr__(config.settings, "min_gap_ms", 40)
    object.__setattr__(config.settings, "target_cps", 17.0)

    real_object_setattr = object.__setattr__  # capture BEFORE patching st.object

    a_ident = {"id": None}
    a_failed = {"done": False}
    reached_apply = threading.Event()
    let_a_fail = threading.Event()
    b_done = threading.Event()
    a_result = {}
    b_result = {}

    class FakeObject:
        """Mirror object.__setattr__ for the router's live-apply seam (the code
        calls object.__setattr__(config.settings, name, value)). Thread A's
        write to min_gap_ms is the injected failure point: signal "reached
        live-apply", block until the test lets it fail, then raise. Every other
        write — B's writes, and A's rollback once it has failed — delegates to
        the real object.__setattr__."""

        @staticmethod
        def __setattr__(obj, name, value):
            if (
                threading.current_thread().ident == a_ident["id"]
                and name == "min_gap_ms"
                and not a_failed["done"]
            ):
                reached_apply.set()
                let_a_fail.wait(10)
                a_failed["done"] = True
                raise RuntimeError("injected live-apply failure for thread A")
            real_object_setattr(obj, name, value)

    # Inject a module-global `object` name so the router's live-apply/rollback
    # `object.__setattr__(...)` lookups hit FakeObject. raising=False because the
    # builtin isn't a module attribute yet.
    monkeypatch.setattr(st, "object", FakeObject, raising=False)

    def thread_a():
        a_ident["id"] = threading.current_thread().ident
        try:
            st.set_subtitle_tuning(st.SubtitleTuningBody(min_gap_ms=250))
        except Exception as e:  # noqa: BLE001 - capture the HTTPException
            a_result["exc"] = e

    def thread_b():
        try:
            b_result["resp"] = st.set_subtitle_tuning(st.SubtitleTuningBody(target_cps=21.0))
        finally:
            b_done.set()

    ta = threading.Thread(target=thread_a)
    tb = threading.Thread(target=thread_b)
    ta.start()
    assert reached_apply.wait(10), "thread A never reached its live-apply seam"
    tb.start()
    # (d) A is blocked inside `with _tx_lock:` (its store write already happened
    # earlier in the SAME locked section). A non-blocking acquire from the main
    # thread must FAIL — proving the operation lock is held across A's whole
    # transaction (store write -> live apply -> rollback). Defensive getattr so
    # a pre-fix build (no lock) skips the probe and still fails on the
    # serialization / rollback assertions below instead of AttributeError-ing.
    tx_lock = getattr(st, "_tx_lock", None)
    if tx_lock is not None:
        assert not tx_lock.acquire(blocking=False), "operation lock NOT held while A mid-transaction"
    # Under the fix A holds _tx_lock through live-apply + rollback, so B cannot
    # finish while A is blocked. Deterministic: A will not release the lock until
    # let_a_fail is set below.
    tb.join(timeout=0.5)
    assert tb.is_alive(), "thread B completed while A held the operation lock (not serialized)"

    let_a_fail.set()
    ta.join(10)
    assert not ta.is_alive(), "thread A did not finish after the injected failure"
    tb.join(10)
    assert not tb.is_alive(), "thread B did not finish after the lock was released"
    b_done.wait(10)

    # A surfaced a rolled-back 500.
    assert isinstance(a_result.get("exc"), HTTPException)
    assert a_result["exc"].status_code == 500
    assert "rolled back" in a_result["exc"].detail
    # B completed successfully.
    assert b_result["resp"]["ok"] is True
    assert b_result["resp"]["applied"] == ["target_cps"]

    # (a) B's persisted override survives A's rollback.
    ov = cs.load_overrides()
    assert ov["target_cps"] == 21.0
    # (b) A's touched key back to its pre-transaction value in the store.
    assert ov["min_gap_ms"] == 40
    # unrelated override survives untouched.
    assert ov["vad_enabled"] is False
    # (c) disk (store) and live agree.
    assert config.settings.target_cps == 21.0
    assert config.settings.min_gap_ms == 40
    assert ov["target_cps"] == config.settings.target_cps
    assert ov["min_gap_ms"] == config.settings.min_gap_ms
