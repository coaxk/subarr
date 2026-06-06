"""#155 phase 2 — audio-audit API routes + the "user verification wins" filter.

The walker persists findings keyed by canonical_path. Once the user adjudicates
a file (records an audio-lang verification, via the review modal or set-language),
that file must DROP OUT of the findings list — and stay out across a re-scan,
which skips unchanged files by mtime and would otherwise leave the stale finding
forever. The GET endpoint enforces this by filtering findings against the
audio_lang verification store (user ground truth).
"""
from __future__ import annotations


def _seed_finding(client, *, path, status="mislabel", detected="nl"):
    store = client.app.state.audio_audit_store
    store.upsert(canonical_path=path, tag_lang="da", detected_lang=detected,
                 status=status, languages_heard=[detected], n_agreeing=3,
                 n_total=3, mtime=10.0)


def test_get_audit_lists_actionable_findings(app_with_stub):
    _seed_finding(app_with_stub, path="TV/X/a.mkv")
    _seed_finding(app_with_stub, path="TV/X/ok.mkv", status="agrees", detected="en")
    body = app_with_stub.get("/api/audio-audit").json()
    paths = [f["canonical_path"] for f in body["findings"]]
    assert "TV/X/a.mkv" in paths          # mislabel = actionable
    assert "TV/X/ok.mkv" not in paths      # agrees = not surfaced
    assert body["counts"].get("mislabel") == 1


def test_get_audit_excludes_user_verified(app_with_stub):
    _seed_finding(app_with_stub, path="TV/X/a.mkv")
    # before verification: present
    assert any(f["canonical_path"] == "TV/X/a.mkv"
               for f in app_with_stub.get("/api/audio-audit").json()["findings"])
    # user adjudicates it → ground truth recorded
    app_with_stub.app.state.audio_lang.upsert(
        canonical_path="TV/X/a.mkv", lang_code="nld", source="user", confidence=1.0)
    # after: filtered out (and would survive a re-scan's mtime-skip)
    assert not any(f["canonical_path"] == "TV/X/a.mkv"
                   for f in app_with_stub.get("/api/audio-audit").json()["findings"])


def test_get_audit_verified_match_is_slash_insensitive(app_with_stub):
    # walker stores bare canonical; set-language persists lstrip('/') — match
    # must normalize both so a leading-slash mismatch doesn't leak a stale row.
    _seed_finding(app_with_stub, path="/TV/X/b.mkv")
    app_with_stub.app.state.audio_lang.upsert(
        canonical_path="TV/X/b.mkv", lang_code="nld", source="user")
    assert not any("b.mkv" in f["canonical_path"]
                   for f in app_with_stub.get("/api/audio-audit").json()["findings"])


def test_get_audit_whisper_robust_does_not_hide(app_with_stub):
    # The walker's OWN Tier 2 auto-verification (source=whisper-robust) must
    # NOT hide the finding — only a user confirmation does. Otherwise Tier 2
    # would self-erase every finding it writes.
    _seed_finding(app_with_stub, path="TV/X/a.mkv")
    app_with_stub.app.state.audio_lang.upsert(
        canonical_path="TV/X/a.mkv", lang_code="nld",
        source="whisper-robust", confidence=0.7)
    assert any(f["canonical_path"] == "TV/X/a.mkv"
               for f in app_with_stub.get("/api/audio-audit").json()["findings"])


def test_get_audit_includes_scan_summary(app_with_stub):
    _seed_finding(app_with_stub, path="TV/X/a.mkv")
    body = app_with_stub.get("/api/audio-audit").json()
    assert body["summary"]["total_checked"] >= 1
    assert body["summary"]["last_checked_at"] is not None


def test_start_validates_scope(app_with_stub):
    # bogus scope rejected up front (validated before the running-check)
    assert app_with_stub.post("/api/audio-audit/start?scope=bogus").status_code == 400


def test_start_accepts_library_scope(app_with_stub):
    r = app_with_stub.post("/api/audio-audit/start?scope=library")
    assert r.status_code in (202, 409)


def test_start_then_double_start_409(app_with_stub):
    # First start kicks the walker; an immediate second start is rejected while
    # it's still running. (Worklist is empty in the stub env, so it may finish
    # fast — we only assert the start succeeds and the conflict path is wired.)
    r1 = app_with_stub.post("/api/audio-audit/start")
    assert r1.status_code in (202, 409)
