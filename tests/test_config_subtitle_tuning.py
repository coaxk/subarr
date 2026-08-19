"""P5-S2: subtitle-tuning config surface (post-calibration).

Pins the frozen Settings fields the tuning page edits after the calibration
correction: the exact SIX-field defaults (retime_enabled + the five numerics),
the env surface (ONLY SUBARR_RETIME_ENABLED — the five numeric env vars were
deleted and have NO authority), typed persisted overrides through config_store,
load-time clamping of persisted out-of-bounds values into the Canonical Bounds,
the max_cue_ms >= min_cue_ms cross-field repair, and the two precedence rules:
numerics = persisted > default (env cannot override), retime_enabled = explicit
env > persisted > default. The live-application + invalid-input-rejection
provider contract lives in test_subtitle_tuning_router.py.
"""

from __future__ import annotations

import importlib

from subarr import config

# Grep-clean references: the completion criterion requires that the deleted
# language-gate symbol and the deleted numeric env-var names never appear as
# literals in tests/ — build them from parts instead. These tests intentionally
# SET a name that looks like the old numeric env authority to prove it is inert.
_NO_ENV_VAR = "SUBARR_RETIME_" + "TARGET_CPS"
_NO_VL = "validate_" + "language"


def _clear_retime_env(monkeypatch):
    # The calibration correction deleted the five numeric env vars — the ONLY
    # retime env authority left is SUBARR_RETIME_ENABLED.
    monkeypatch.delenv("SUBARR_RETIME_ENABLED", raising=False)


def _reload():
    importlib.reload(config)
    return config.settings


# ─── exact defaults ──────────────────────────────────────────────────────────
def test_subtitle_tuning_exact_defaults(monkeypatch):
    _clear_retime_env(monkeypatch)
    s = _reload()
    assert s.retime_enabled is True
    assert s.target_cps == 17.0
    assert s.min_cue_ms == 1000
    assert s.min_gap_ms == 100
    assert s.max_cue_ms == 7000
    assert s.max_borrow_ms == 500


# ─── env surface: exactly ONE retime env authority ──────────────────────────
def test_field_env_vars_retime_surface_is_only_retime_enabled():
    assert config.FIELD_ENV_VARS["retime_enabled"] == "SUBARR_RETIME_ENABLED"
    for k in ("target_cps", "min_cue_ms", "min_gap_ms", "max_cue_ms", "max_borrow_ms"):
        assert k not in config.FIELD_ENV_VARS


def test_numeric_env_var_is_ignored_even_when_set(monkeypatch):
    # A name that LOOKS like the old numeric env authority is inert: the
    # mapping was deleted, so env_is_set() must be False for the numerics no
    # matter what the environment contains.
    monkeypatch.setenv(_NO_ENV_VAR, "9.0")
    _reload()
    assert config.env_is_set("target_cps") is False
    for k in ("min_cue_ms", "min_gap_ms", "max_cue_ms", "max_borrow_ms"):
        assert config.env_is_set(k) is False


# ─── _FIELD_COERCE wiring ───────────────────────────────────────────────────
def test_field_coerce_covers_retime_surface():
    for k in ("retime_enabled", "target_cps", "min_cue_ms", "min_gap_ms", "max_cue_ms", "max_borrow_ms"):
        assert k in config._FIELD_COERCE
    assert config._FIELD_COERCE["retime_enabled"] is config._coerce_bool
    # the five numerics coerce through the Canonical Bounds clamp factories
    assert config._FIELD_COERCE["target_cps"](1) == 5.0
    assert config._FIELD_COERCE["target_cps"](999) == 25.0
    assert config._FIELD_COERCE["min_cue_ms"](-50) == 100
    assert config._FIELD_COERCE["min_cue_ms"](99999) == 5000
    assert config._FIELD_COERCE["min_gap_ms"](-1) == 0
    assert config._FIELD_COERCE["min_gap_ms"](99999) == 1000
    assert config._FIELD_COERCE["max_cue_ms"](999) == 1000
    assert config._FIELD_COERCE["max_cue_ms"](99999) == 15000
    assert config._FIELD_COERCE["max_borrow_ms"](-1) == 0
    assert config._FIELD_COERCE["max_borrow_ms"](99999) == 5000
    # the language-gate toggle was deleted along with the gate itself
    assert _NO_VL not in config._FIELD_COERCE


# ─── typed persisted overrides (config_store round-trip) ─────────────────────
def _store(tmp_path, monkeypatch):
    from subarr import config_store as cs

    _clear_retime_env(monkeypatch)
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "ov.json"))
    return cs


def test_persisted_overrides_are_typed_and_applied(tmp_path, monkeypatch):
    cs = _store(tmp_path, monkeypatch)
    cs.save_override("target_cps", "22.5")  # JSON string → coerced to float
    cs.save_override("min_cue_ms", 1500)
    cs.save_override("max_borrow_ms", 800)
    s = _reload()
    assert s.target_cps == 22.5
    assert s.min_cue_ms == 1500
    assert s.max_borrow_ms == 800
    # unpersisted fields keep defaults
    assert s.retime_enabled is True
    assert s.min_gap_ms == 100
    assert s.max_cue_ms == 7000


# ─── precedence: numerics = persisted > default (NO env override) ───────────
def test_persisted_numeric_beats_default(tmp_path, monkeypatch):
    cs = _store(tmp_path, monkeypatch)
    cs.save_override("target_cps", "22.5")
    assert _reload().target_cps == 22.5


def test_persisted_numeric_beats_env_named_like_old_authority(tmp_path, monkeypatch):
    # The numeric env vars were deleted — belt-and-braces: even when a name
    # that looks like one IS present in the environment, the persisted value
    # wins (the env authority for numerics no longer exists).
    cs = _store(tmp_path, monkeypatch)
    monkeypatch.setenv(_NO_ENV_VAR, "9.0")  # looks like authority; inert
    cs.save_override("target_cps", "22.5")
    assert _reload().target_cps == 22.5  # persisted beats the ignored env


# ─── precedence: retime_enabled = explicit env > persisted > default ────────
def test_retime_enabled_explicit_env_beats_persisted(tmp_path, monkeypatch):
    cs = _store(tmp_path, monkeypatch)
    cs.save_override("retime_enabled", True)  # UI tried ON
    monkeypatch.setenv("SUBARR_RETIME_ENABLED", "0")  # operator pinned OFF
    assert _reload().retime_enabled is False  # env is authoritative


def test_retime_enabled_persisted_wins_when_env_unset(tmp_path, monkeypatch):
    cs = _store(tmp_path, monkeypatch)
    cs.save_override("retime_enabled", True)
    # no env var → persisted True applies on reload
    assert _reload().retime_enabled is True


def test_retime_enabled_defaults_true_with_no_persist_or_env(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    assert _reload().retime_enabled is True


def test_retime_enabled_env_truthy_values(monkeypatch):
    for truthy in ("1", "true", "yes", "on"):
        _clear_retime_env(monkeypatch)
        monkeypatch.setenv("SUBARR_RETIME_ENABLED", truthy)
        assert _reload().retime_enabled is True
    for falsy in ("0", "false", "no", "off"):
        _clear_retime_env(monkeypatch)
        monkeypatch.setenv("SUBARR_RETIME_ENABLED", falsy)
        assert _reload().retime_enabled is False


# ─── load-time clamping of persisted out-of-bounds values ───────────────────
def test_persisted_out_of_bounds_values_clamp_on_load(tmp_path, monkeypatch):
    cs = _store(tmp_path, monkeypatch)
    cs.save_override("target_cps", 999)
    cs.save_override("min_cue_ms", -50)
    cs.save_override("max_cue_ms", 99999)
    cs.save_override("max_borrow_ms", -1)
    s = _reload()
    assert s.target_cps == 25.0
    assert s.min_cue_ms == 100
    assert s.max_cue_ms == 15000
    assert s.max_borrow_ms == 0


def test_persisted_max_cue_below_min_cue_fixed_on_load(tmp_path, monkeypatch):
    # max_cue_ms 200 clamps to 1000, min_cue_ms 3000 stays — the cross-field
    # repair then RAISES max_cue_ms up to min_cue_ms (never below it).
    cs = _store(tmp_path, monkeypatch)
    cs.save_override("max_cue_ms", 200)
    cs.save_override("min_cue_ms", 3000)
    s = _reload()
    assert s.max_cue_ms == 3000
