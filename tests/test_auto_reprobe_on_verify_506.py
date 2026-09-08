"""#506 follow-up: a manual verification that disagrees with the cached probe
triggers a forced re-probe of that one file.

Why this is worth doing at all: the manual Force Re-probe added in #506 only
helps someone who already SUSPECTS the cache is stale. AztecGuyGDL knew because
he had just run Tdarr. Jorman is about to reach the same state from the other
direction, after tagging silent films `zxx`, and nothing would tell him the
cache is now lying.

Why the trigger is "differs from the cache" and not something narrower: it is
tempting to only re-probe when the cache has NO language (und), on the reasoning
that a definite cached language means the user is merely overriding it. That is
wrong. A file tagged `eng` that the user has since retagged to `jpn` externally
still reads `eng` from the cache, and verifying `ja` is exactly the moment to
go and look again.

Equally, when the verified language is ALREADY what the cache reports there is
nothing to learn, so that case must not spend an ffprobe.
"""

from __future__ import annotations

import pytest


def _probe(langs):
    from subarr.media_probe import AudioStream, ProbeResult

    r = ProbeResult(canonical_path="TV/S/ep.mkv")
    r.audio = [
        AudioStream(index=i, language=lang, codec="aac", title=None, default=(i == 0))
        for i, lang in enumerate(langs)
    ]
    return r


def test_untagged_cache_plus_a_verified_language_wants_a_reprobe():
    """The #506 case: encoder never tagged it, user corrected the file
    externally, cache still reports und."""
    from subarr.routers.audio_lang import verification_contradicts_probe

    assert verification_contradicts_probe(_probe(["und"]), "en") is True
    assert verification_contradicts_probe(_probe([None]), "en") is True
    assert verification_contradicts_probe(_probe([]), "en") is True
    assert verification_contradicts_probe(None, "en") is True


def test_a_different_definite_language_also_wants_a_reprobe():
    """The retag case. Cache says English, user says Japanese: the file may
    have been corrected to jpn externally, and this is the moment to look."""
    from subarr.routers.audio_lang import verification_contradicts_probe

    assert verification_contradicts_probe(_probe(["eng"]), "ja") is True


def test_agreement_must_not_spend_a_probe():
    """Nothing to learn. Must be cheap, because most verifications on a
    correctly-tagged file agree with it."""
    from subarr.routers.audio_lang import verification_contradicts_probe

    assert verification_contradicts_probe(_probe(["eng"]), "en") is False
    assert verification_contradicts_probe(_probe(["en"]), "en") is False


def test_agreement_with_any_track_counts_as_agreement():
    """A multi-track file whose Japanese track is present should not re-probe
    when the user confirms Japanese."""
    from subarr.routers.audio_lang import verification_contradicts_probe

    assert verification_contradicts_probe(_probe(["eng", "jpn"]), "ja") is False


def test_three_letter_and_two_letter_forms_are_the_same_answer():
    """ffprobe emits ISO-639-2 ('jpn'), the store holds ISO-639-1 ('ja').
    Comparing them raw would report a contradiction on every single file."""
    from subarr.routers.audio_lang import verification_contradicts_probe

    assert verification_contradicts_probe(_probe(["jpn"]), "ja") is False
    assert verification_contradicts_probe(_probe(["fre"]), "fr") is False
    assert verification_contradicts_probe(_probe(["ger"]), "de") is False


def test_blank_verification_never_triggers_work():
    from subarr.routers.audio_lang import verification_contradicts_probe

    assert verification_contradicts_probe(_probe(["und"]), "") is False
    assert verification_contradicts_probe(_probe(["und"]), None) is False


# ── The consumption tests ────────────────────────────────────────────────────
# #505 shipped a flag that was computed, serialised, rendered and covered by a
# dozen assertions, and nothing consumed it. Every one of those tests asserted
# the FLAG. These assert that POST /api/verifications actually fires the probe,
# which is the part that can silently rot.


def _install_spy(app_with_stub, monkeypatch):
    """Record at CALL time, not at task-run time.

    `_reprobe_then_refresh` is fired with asyncio.create_task, so its body does
    not run until the loop next yields -- well after the response returns and
    the assertion has already read an empty spy. Patching it with a SYNC
    recorder that returns a throwaway coroutine records the moment the endpoint
    decides to fire, which is exactly the wiring under test.
    """
    calls = {}
    from subarr.routers import audio_lang as mod

    def _spy(request, canonical):
        calls["canonical"] = canonical

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(mod, "_reprobe_then_refresh", _spy)
    return calls


def _seed_probe(app_with_stub, canonical, langs):
    from subarr.media_probe import AudioStream, ProbeResult

    r = ProbeResult(canonical_path=canonical)
    r.audio = [
        AudioStream(index=i, language=lg, codec="aac", title=None, default=(i == 0))
        for i, lg in enumerate(langs)
    ]
    app_with_stub.app.state.probe_store.upsert(canonical_path=canonical, mtime=1.0, size=1, result=r)


def _verify(app_with_stub, canonical, lang="en", source="user"):
    return app_with_stub.post(
        "/api/audio-lang/verifications",
        json={
            "canonical_path": canonical,
            "lang_code": lang,
            "source": source,
            "confidence": 1.0,
        },
    )


def test_verifying_against_an_untagged_probe_fires_a_reprobe(app_with_stub, monkeypatch):
    calls = _install_spy(app_with_stub, monkeypatch)
    canonical = "TV/AutoReprobe/ep.mkv"
    _seed_probe(app_with_stub, canonical, ["und"])
    r = _verify(app_with_stub, canonical)
    assert r.status_code == 200, r.text
    assert calls.get("canonical") == canonical, "the endpoint never fired the re-probe"


def test_verifying_a_different_language_fires_a_reprobe(app_with_stub, monkeypatch):
    """The retag case: cache says English, user says Japanese."""
    calls = _install_spy(app_with_stub, monkeypatch)
    canonical = "TV/AutoReprobe/retag.mkv"
    _seed_probe(app_with_stub, canonical, ["eng"])
    r = _verify(app_with_stub, canonical, lang="ja")
    assert r.status_code == 200, r.text
    assert calls.get("canonical") == canonical


def test_agreeing_verification_does_not_fire_a_reprobe(app_with_stub, monkeypatch):
    calls = _install_spy(app_with_stub, monkeypatch)
    canonical = "TV/AutoReprobe/agree.mkv"
    _seed_probe(app_with_stub, canonical, ["eng"])
    r = _verify(app_with_stub, canonical, lang="en")
    assert r.status_code == 200, r.text
    assert calls == {}, "an agreeing verification must not spend an ffprobe"


def test_non_user_source_does_not_fire_a_reprobe(app_with_stub, monkeypatch):
    """An automatic or Whisper verdict must never trigger disk IO."""
    calls = _install_spy(app_with_stub, monkeypatch)
    canonical = "TV/AutoReprobe/auto.mkv"
    _seed_probe(app_with_stub, canonical, ["und"])
    r = _verify(app_with_stub, canonical, source="whisper-robust")
    assert r.status_code == 200, r.text
    assert calls == {}


@pytest.mark.asyncio
async def test_the_worker_forces_the_probe_and_awaits_it_before_refreshing():
    """Separately from the wiring: the worker itself must pass force=True and
    must await the probe BEFORE asking for a rebuild, or the rebuild re-reads
    the stale cache entry it was meant to replace."""
    from subarr.routers.audio_lang import _reprobe_then_refresh

    order = []

    class _Walker:
        _tasks: dict = {}

        async def probe_paths(self, paths, force=False):
            order.append(("probe", list(paths), force))

            class _S:
                id = "w1"

            return _S()

    class _Cov:
        def request_refresh(self, *a):
            order.append(("refresh",))

    class _State:
        probe_walker = _Walker()
        coverage_cache = _Cov()
        integrations = object()
        probe_store = object()
        audio_lang = object()

    class _App:
        state = _State()

    class _Req:
        app = _App()

    await _reprobe_then_refresh(_Req(), "TV/x/ep.mkv")
    assert order == [("probe", ["TV/x/ep.mkv"], True), ("refresh",)]
