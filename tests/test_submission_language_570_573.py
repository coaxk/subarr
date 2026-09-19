"""#573 + #570: the audio-language decision is made when a job is SUBMITTED.

#573: only the manual queue paths looked the verified language up; the
scheduler's auto-queue, backfill and folder scans reached subgen with no
override, so a verified-French file tagged `eng` was skipped every time it was
queued automatically. #570: a multilingual verdict sent nothing, so a
bilingual file tagged `eng` (Maximilian) was always skipped; it now sends
bypass_skip, but only when that is safe.
"""

from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace

import pytest

EP = "TV/Maximilian/Season 1/Maximilian - S01E01 - Episode 1.mkv"


@pytest.fixture
def store(subarr_env, tmp_path):
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "al.db"
    run_migrations(db)
    return AudioLangStore(db)


@pytest.fixture
def media(media_root):
    def put(canonical: str):
        p = media_root / canonical
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return p

    return put


def _probe_store(*tags):
    from subarr.media_probe import AudioStream, ProbeResult

    def get(canonical):
        return ProbeResult(
            canonical_path=canonical,
            audio=[
                AudioStream(index=i, language=t, codec=None, title=None, default=i == 0)
                for i, t in enumerate(tags)
            ],
        )

    return SimpleNamespace(get=get)


NO_PROBE = SimpleNamespace(get=lambda c: None)


def _caps(**kw):
    base = dict(
        bypass_skip=True,
        skip_audio_languages=("en",),
        transcribe_or_translate="translate",
        subtitle_language_name=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _multi(store, path=EP, codes=("de", "fr")):
    store.upsert(canonical_path=path, lang_code=codes[0], lang_class="multi", lang_codes=list(codes))


def _resolve(store, path=EP, caps=None, probe=None):
    from subarr.submission_language import resolve_submission

    return resolve_submission(store, path, caps=caps if caps is not None else _caps(), probe_store=probe)


# ---- #573: single-language verdicts ------------------------------------------


def test_a_single_language_verdict_becomes_the_override(store, media):
    media(EP)
    store.upsert(canonical_path=EP, lang_code="fr")
    d = _resolve(store, probe=_probe_store("eng"))
    assert (d.override, d.bypass_skip) == ("fr", False)


def test_a_series_rule_becomes_the_override(store, media):
    media(EP)
    store.set_series_intent(series_prefix="TV/Maximilian/", lang_code="de")
    assert _resolve(store, probe=_probe_store("eng")).override == "de"


def test_english_is_still_withheld_when_subgen_skips_english(store, media):
    """#498 unchanged: forwarding `en` to a skip-English subgen would make it skip."""
    media(EP)
    store.upsert(canonical_path=EP, lang_code="en")
    d = _resolve(store, probe=_probe_store("eng"))
    assert (d.override, d.bypass_skip) == (None, False)


def test_no_verdict_means_nothing_is_sent(store, media):
    media(EP)
    d = _resolve(store, probe=_probe_store("eng"))
    assert (d.override, d.bypass_skip) == (None, False)


def test_a_replaced_file_follows_its_verdict_at_submit(store, media):
    """#563's carry-over applies on this path too."""
    old = "TV/Flics/Season 2/Flics - S02E03 - x.mp4"
    new = "TV/Flics/Season 2/Flics - S02E03 - x.mkv"
    media(new)
    store.upsert(canonical_path=old, lang_code="fr")
    assert _resolve(store, path=new, probe=_probe_store("eng")).override == "fr"


# ---- #570: multilingual verdicts ---------------------------------------------


def test_a_multilingual_file_tagged_with_a_skipped_language_is_bypassed(store, media):
    media(EP)
    _multi(store)
    d = _resolve(store, probe=_probe_store("eng"))
    assert (d.override, d.bypass_skip) == (None, True)


def test_three_letter_tags_and_skip_codes_are_normalized(store, media):
    media(EP)
    _multi(store)
    d = _resolve(store, caps=_caps(skip_audio_languages=("eng",)), probe=_probe_store("en"))
    assert d.bypass_skip is True


@pytest.mark.parametrize(
    "caps_kw,probe,why",
    [
        ({"bypass_skip": False}, ("eng",), "subgen cannot bypass"),
        ({"skip_audio_languages": ()}, ("eng",), "subgen skips nothing"),
        ({"skip_audio_languages": None}, ("eng",), "skip list not advertised"),
        ({}, ("ger",), "tag not on the skip list"),
        ({}, (), "no audio tags at all"),
    ],
)
def test_no_bypass_when_subgen_would_not_refuse_the_file_anyway(store, media, caps_kw, probe, why):
    media(EP)
    _multi(store)
    d = _resolve(store, caps=_caps(**caps_kw), probe=_probe_store(*probe))
    assert d.bypass_skip is False, why


def test_no_bypass_when_the_file_was_never_analyzed(store, media):
    media(EP)
    _multi(store)
    assert _resolve(store, probe=NO_PROBE).bypass_skip is False
    assert _resolve(store, probe=None).bypass_skip is False


def test_no_bypass_when_an_english_subtitle_already_exists_in_translate_mode(store, media):
    """bypass_skip also switches off subgen's "subtitle exists" rule."""
    media(EP)
    media(EP[: -len(".mkv")] + ".en.srt")
    _multi(store)
    assert _resolve(store, probe=_probe_store("eng")).bypass_skip is False


def test_a_forced_english_subtitle_does_not_count_as_existing(store, media):
    media(EP)
    media(EP[: -len(".mkv")] + ".en.forced.srt")
    _multi(store)
    assert _resolve(store, probe=_probe_store("eng")).bypass_skip is True


def test_transcribe_mode_checks_the_spoken_languages_not_english(store, media):
    media(EP)
    _multi(store)
    caps = _caps(transcribe_or_translate="transcribe")
    media(EP[: -len(".mkv")] + ".en.srt")  # English is not what transcribe writes
    assert _resolve(store, caps=caps, probe=_probe_store("eng")).bypass_skip is True
    media(EP[: -len(".mkv")] + ".de.srt")  # German is
    assert _resolve(store, caps=caps, probe=_probe_store("eng")).bypass_skip is False


def test_unknown_mode_is_conservative(store, media):
    media(EP)
    media(EP[: -len(".mkv")] + ".en.srt")
    _multi(store)
    caps = _caps(transcribe_or_translate=None)
    assert _resolve(store, caps=caps, probe=_probe_store("eng")).bypass_skip is False


def test_a_subtitle_for_a_different_video_does_not_block(store, media):
    media(EP)
    media("TV/Maximilian/Season 1/Maximilian - S01E02 - Episode 2.en.srt")
    _multi(store)
    assert _resolve(store, probe=_probe_store("eng")).bypass_skip is True


# ---- resolve_for_job ---------------------------------------------------------


def _job(**kw):
    base = dict(canonical_path=EP, audio_language_override=None, bypass_skip=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_job_decided_when_queued_keeps_its_decision(store, media):
    from subarr.submission_language import resolve_for_job

    media(EP)
    store.upsert(canonical_path=EP, lang_code="fr")
    kept = resolve_for_job(_job(audio_language_override="it"), store=store, caps=_caps(), probe_store=None)
    assert (kept.override, kept.bypass_skip) == ("it", False)
    kept = resolve_for_job(_job(bypass_skip=True), store=store, caps=_caps(), probe_store=None)
    assert (kept.override, kept.bypass_skip) == (None, True)


def test_an_undecided_job_is_resolved(store, media):
    from subarr.submission_language import resolve_for_job

    media(EP)
    store.upsert(canonical_path=EP, lang_code="fr")
    assert resolve_for_job(_job(), store=store, caps=_caps(), probe_store=None).override == "fr"


def test_a_failing_lookup_never_blocks_the_submit(store):
    from subarr.submission_language import resolve_for_job

    broken = SimpleNamespace(get=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    d = resolve_for_job(_job(), store=broken, caps=_caps(), probe_store=None)
    assert (d.override, d.bypass_skip) == (None, False)


# ---- the feeder: every producer ----------------------------------------------


def _submit_through_feeder(app, monkeypatch, *, source):
    started = []
    monkeypatch.setattr(app.state.runner, "start", lambda scan, **kw: started.append(kw))
    job = app.state.pending_queue.enqueue(EP, source=source)
    assert job.audio_language_override is None and job.bypass_skip is False
    asyncio.run(app.state.queue_feeder._submit_job(job))
    assert len(started) == 1
    return started[0]


@pytest.mark.parametrize("source", ["auto", "gaps", "backfill", "manual"])
def test_feeder_forwards_the_verified_language_for_every_producer(
    app_with_stub, media_root, monkeypatch, source
):
    from subarr.app import app

    (media_root / EP).parent.mkdir(parents=True, exist_ok=True)
    (media_root / EP).write_bytes(b"x")
    app.state.audio_lang.upsert(canonical_path=EP, lang_code="fr")
    kw = _submit_through_feeder(app, monkeypatch, source=source)
    assert kw["audio_language_override"] == "fr"
    assert kw["bypass_skip"] is False


def test_feeder_bypasses_a_multilingual_file_tagged_english(app_with_stub, media_root, monkeypatch):
    from subarr.app import app
    from subarr.media_probe import AudioStream, ProbeResult

    (media_root / EP).parent.mkdir(parents=True, exist_ok=True)
    (media_root / EP).write_bytes(b"x")
    _multi(app.state.audio_lang)
    app.state.subgen_caps = dataclasses.replace(
        app.state.subgen_caps,
        bypass_skip=True,
        skip_audio_languages=("en",),
        transcribe_or_translate="translate",
    )
    st = (media_root / EP).stat()
    app.state.probe_store.upsert(
        canonical_path=EP,
        mtime=st.st_mtime,
        size=st.st_size,
        result=ProbeResult(
            canonical_path=EP,
            audio=[AudioStream(index=0, language="eng", codec=None, title=None, default=True)],
        ),
    )
    kw = _submit_through_feeder(app, monkeypatch, source="auto")
    assert kw["audio_language_override"] is None
    assert kw["bypass_skip"] is True


def test_a_folder_scan_job_gets_the_language_end_to_end(app_with_stub, media_root, monkeypatch):
    """POST /api/scan (folder scans) enqueues with no override; the feeder now
    supplies it."""
    from subarr.app import app

    monkeypatch.setattr(app.state.queue_feeder, "kick", lambda *a, **k: None)
    (media_root / EP).parent.mkdir(parents=True, exist_ok=True)
    (media_root / EP).write_bytes(b"x")
    app.state.audio_lang.upsert(canonical_path=EP, lang_code="fr")
    r = app_with_stub.post("/api/scan", json={"paths": [EP]})
    assert r.status_code in (200, 201, 202), r.text
    job = next(j for j in app.state.pending_queue.list() if j.canonical_path == EP)
    started = []
    monkeypatch.setattr(app.state.runner, "start", lambda scan, **kw: started.append(kw))
    asyncio.run(app.state.queue_feeder._submit_job(job))
    assert started and started[0]["audio_language_override"] == "fr"


def test_an_empty_skip_list_says_why_in_the_reason(store, media):
    media(EP)
    _multi(store)
    d = _resolve(store, caps=_caps(skip_audio_languages=()), probe=_probe_store("eng"))
    assert d.bypass_skip is False
    assert "skips no audio language" in d.reason


def test_translate_with_a_renamed_output_still_respects_an_english_subtitle(store, media):
    """SUBTITLE_LANGUAGE_NAME=es renames the file but translate still writes
    English text, so an existing English subtitle means the job is done."""
    media(EP)
    media(EP[: -len(".mkv")] + ".en.srt")
    _multi(store)
    caps = _caps(subtitle_language_name="es")
    assert _resolve(store, caps=caps, probe=_probe_store("eng")).bypass_skip is False
