"""Security regression tests (post-launch hardening, 2026-06-08).

Covers the findings from the adversarial audit:
  - onboarding state must not leak arr/Plex secrets in cleartext, and an
    echoed-back mask must not clobber the stored real key
  - supervised-task tracebacks must redact credential query-string params
    (Tautulli ?apikey=, Plex ?X-Plex-Token=) before storage (#157 served them)
  - /api/health (the one pre-auth endpoint) must not leak config
"""
from __future__ import annotations

from pathlib import Path

from subarr.migrate import run_migrations


# ─── onboarding secret masking ──────────────────────────────────────

def _store(tmp_path):
    from subarr.onboarding import OnboardingStore
    db = tmp_path / "subarr.db"
    run_migrations(db)
    return OnboardingStore(db)


def test_onboarding_to_dict_masks_secrets(tmp_path):
    s = _store(tmp_path)
    s.update(progress_patch={
        "sonarr_api_key": "REALKEY12345",
        "plex_token": "PLEXTOKEN6789",
        "sonarr_url": "http://sonarr:8989",
    })
    state = s.get()
    masked = state.to_dict()["progress"]
    # secrets masked
    assert masked["sonarr_api_key"].startswith("••••")
    assert "REALKEY12345" not in masked["sonarr_api_key"]
    assert masked["plex_token"].startswith("••••")
    assert "PLEXTOKEN6789" not in masked["plex_token"]
    # non-secret untouched
    assert masked["sonarr_url"] == "http://sonarr:8989"
    # raw value retained for complete() → _apply_progress_to_settings
    assert state.progress["sonarr_api_key"] == "REALKEY12345"


def test_onboarding_merge_ignores_echoed_mask(tmp_path):
    s = _store(tmp_path)
    s.update(progress_patch={"sonarr_api_key": "REALKEY12345"})
    masked = s.get().to_dict()["progress"]["sonarr_api_key"]
    # resuming wizard echoes the masked value back on the next step
    s.update(progress_patch={"sonarr_api_key": masked})
    assert s.get().progress["sonarr_api_key"] == "REALKEY12345"  # NOT clobbered
    # a genuinely new value still overwrites
    s.update(progress_patch={"sonarr_api_key": "NEWKEY99999"})
    assert s.get().progress["sonarr_api_key"] == "NEWKEY99999"


def test_onboarding_state_route_masks(app_with_stub):
    c = app_with_stub
    c.put("/api/onboarding/state", json={"progress": {"bazarr_api_key": "LEAKYKEY12345"}})
    body = c.get("/api/onboarding/state").json()
    assert "LEAKYKEY12345" not in str(body)


# ─── task_health traceback redaction ────────────────────────────────

def test_task_health_redacts_credential_query_params(tmp_path):
    from subarr.task_health import TaskHealthStore
    db = tmp_path / "subarr.db"
    run_migrations(db)
    th = TaskHealthStore(db)
    try:
        raise RuntimeError(
            "GET http://tautulli:8181/api/v2?apikey=SUPERSECRET123&cmd=status — "
            "and http://plex:32400/library?X-Plex-Token=PLEXLEAK456"
        )
    except RuntimeError as e:
        th.record_failure("completion-watcher", e)
    detail = th.states()[0].last_error_detail
    assert "SUPERSECRET123" not in detail
    assert "PLEXLEAK456" not in detail
    assert "apikey=<redacted>" in detail
    assert "X-Plex-Token=<redacted>" in detail


# ─── /api/health no config leak ─────────────────────────────────────

def test_health_endpoint_no_config_leak(app_with_stub):
    d = app_with_stub.get("/api/health").json()
    assert d["status"] == "ok"
    assert "version" in d
    assert "media_root" not in d
    assert "subgen_url" not in d
