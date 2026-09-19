"""#569: a file subgen skipped for its audio language says so in Queue Issues.

subgen's /batch reply only counts skips, so every such row read "reason not
in /batch response". subarr knows the file's tagged audio language (probe),
the connected subgen's skip list and the user's verdict, so it can name the
audio-language skip and the fix. A skip of a language the user verified and
this subgen is set to skip is correct, and is marked `audio_lang_expected`,
which the Queue page files under Recently done.
"""

from __future__ import annotations

import dataclasses
import time
from types import SimpleNamespace

import pytest

EP = "TV/Flics/Season 2/Flics - S02E03 - x.mkv"


@pytest.fixture
def store(subarr_env, tmp_path):
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "al.db"
    run_migrations(db)
    return AudioLangStore(db)


def _probe_store(*tags):
    from subarr.media_probe import AudioStream, ProbeResult

    return SimpleNamespace(
        get=lambda c: ProbeResult(
            canonical_path=c,
            audio=[
                AudioStream(index=i, language=t, codec=None, title=None, default=i == 0)
                for i, t in enumerate(tags)
            ],
        )
    )


def _caps(**kw):
    base = dict(skip_audio_languages=("en",), bypass_skip=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _explain(store, caps=None, probe=None):
    from subarr.submission_language import explain_audio_language_skip

    return explain_audio_language_skip(
        EP, store=store, caps=caps if caps is not None else _caps(), probe_store=probe or _probe_store("eng")
    )


# ---- the explainer ------------------------------------------------------------


def test_unverified_file_tagged_with_a_skipped_language(store):
    out = _explain(store)
    assert out["skip_reason"] == "audio_lang"
    assert "tagged English" in out["detail"] and "skips English" in out["detail"]
    assert "verify it in Review" in out["detail"]


def test_verified_foreign_language_says_a_requeue_sends_it(store):
    store.upsert(canonical_path=EP, lang_code="fr")
    out = _explain(store)
    assert out["skip_reason"] == "audio_lang"
    assert "French" in out["detail"] and "requeue" in out["detail"]


def test_a_series_rule_counts_as_the_verdict(store):
    store.set_series_intent(series_prefix="TV/Flics/", lang_code="it")
    assert "Italian" in _explain(store)["detail"]


@pytest.mark.parametrize("code", ["en", "eng"])
def test_verified_english_on_a_skip_english_subgen_is_expected(store, code):
    store.upsert(canonical_path=EP, lang_code=code)
    out = _explain(store)
    assert out["skip_reason"] == "audio_lang_expected"
    assert "expected" in out["detail"]


def test_multilingual_with_bypass_support_says_requeue(store):
    store.upsert(canonical_path=EP, lang_code="de", lang_class="multi", lang_codes=["de", "fr"])
    out = _explain(store)
    assert out["skip_reason"] == "audio_lang" and "bypass" in out["detail"]


def test_multilingual_without_bypass_support_names_what_is_needed(store):
    store.upsert(canonical_path=EP, lang_code="de", lang_class="multi", lang_codes=["de", "fr"])
    out = _explain(store, caps=_caps(bypass_skip=False))
    assert "v4.23" in out["detail"]


def test_nothing_to_say_when_subgen_skips_no_language(store):
    assert _explain(store, caps=SimpleNamespace(skip_audio_languages=None)) is None
    assert _explain(store, caps=SimpleNamespace(skip_audio_languages=())) is None


def test_nothing_to_say_when_the_tag_is_not_on_the_skip_list(store):
    assert _explain(store, probe=_probe_store("ger")) is None


def test_nothing_to_say_when_the_file_has_no_audio_tags(store):
    assert _explain(store, probe=_probe_store()) is None


def test_unprobed_file_is_not_explained(store):
    from subarr.submission_language import explain_audio_language_skip

    assert explain_audio_language_skip(EP, store=store, caps=_caps(), probe_store=None) is None
    no_probe = SimpleNamespace(get=lambda c: None)
    assert explain_audio_language_skip(EP, store=store, caps=_caps(), probe_store=no_probe) is None


def test_a_broken_store_still_explains_the_tag(store):
    broken = SimpleNamespace(get=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    from subarr.submission_language import explain_audio_language_skip

    out = explain_audio_language_skip(EP, store=broken, caps=_caps(), probe_store=_probe_store("eng"))
    assert out["skip_reason"] == "audio_lang"


# ---- the Queue outcome chip ---------------------------------------------------


def test_chip_uses_the_explanation_for_an_unexplained_skip(subarr_env):
    import subarr.routers.queue as q
    from subarr.scan_store import PATH_STATUS_SKIPPED

    chip = q._path_outcome_chip(
        PATH_STATUS_SKIPPED,
        {"skipped": 1},
        "subgen skipped 1 - reason not in /batch response",
        canonical_path=EP,
        explain_skip=lambda c: {
            "skip_reason": "audio_lang",
            "label": "skipped: audio language",
            "detail": "why",
        },
    )
    assert chip == {
        "category": "skipped",
        "skip_reason": "audio_lang",
        "label": "skipped: audio language",
        "detail": "why",
    }


def test_chip_keeps_the_generic_text_without_an_explanation(subarr_env):
    import subarr.routers.queue as q
    from subarr.scan_store import PATH_STATUS_SKIPPED

    for explain in (None, lambda c: None, lambda c: 1 / 0):
        chip = q._path_outcome_chip(
            PATH_STATUS_SKIPPED, {"skipped": 1}, "generic", canonical_path=EP, explain_skip=explain
        )
        assert chip["label"] == "skipped" and chip["detail"] == "generic"


def test_an_existing_subtitle_still_wins(subarr_env, media_root):
    import subarr.routers.queue as q
    from subarr.scan_store import PATH_STATUS_SKIPPED

    (media_root / "TV" / "Show").mkdir(parents=True, exist_ok=True)
    called = []
    chip = q._path_outcome_chip(
        PATH_STATUS_SKIPPED,
        {"skipped": 1},
        "generic",
        canonical_path="TV/Show/ep.mkv",  # conftest ships ep.en.srt next to it
        explain_skip=lambda c: called.append(c) or {"skip_reason": "audio_lang", "label": "x", "detail": "y"},
    )
    assert chip["skip_reason"] == "sub_exists" and called == []


# ---- end to end: GET /api/queue ------------------------------------------------


def test_queue_api_explains_a_live_skip(app_with_stub, media_root):
    from subarr.app import app
    from subarr.media_probe import AudioStream, ProbeResult
    from subarr.scan_store import PATH_STATUS_SKIPPED

    f = media_root / EP
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    st = f.stat()
    app.state.probe_store.upsert(
        canonical_path=EP,
        mtime=st.st_mtime,
        size=st.st_size,
        result=ProbeResult(
            canonical_path=EP,
            audio=[AudioStream(index=0, language="eng", codec=None, title=None, default=True)],
        ),
    )
    app.state.subgen_caps = dataclasses.replace(app.state.subgen_caps, skip_audio_languages=("en",))
    app.state.audio_lang.upsert(canonical_path=EP, lang_code="fr")
    scan = app.state.scans.create([EP], reverse=False)
    scan.created_at = time.time() - 30
    scan.status = "done"
    scan.results[0].status = PATH_STATUS_SKIPPED
    scan.results[0].error = "subgen skipped 1 - reason not in /batch response"
    app.state.scans.save(scan)

    rows = [h for h in app_with_stub.get("/api/queue").json()["history"] if h["path"] == EP]
    assert rows and rows[0]["outcome"]["skip_reason"] == "audio_lang"
    assert "French" in rows[0]["outcome"]["detail"]
