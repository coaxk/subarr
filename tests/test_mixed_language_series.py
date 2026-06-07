"""#140: detect mis-grouped series — one Sonarr folder that actually holds two
different shows, revealed by ≥2 distinct NON-English high-trust spoken
languages across its episodes (e.g. S01E04 Korean, S01E16 Russian).

Precision guardrails under test:
  - only user/whisper languages count (ffprobe tags ignored)
  - English never counts (foreign + English dub is legitimate)
  - a single foreign language never flags
  - per-series dismiss (by series dir) suppresses
"""
from __future__ import annotations

from pathlib import Path

import pytest

from subarr.coverage_engine import CoverageItem, _flag_mixed_language_series
from subarr.audio_lang_store import AudioLangStore
from subarr.migrate import run_migrations


def _ep(title, lang, *, source="whisper", sid=1, path="TV/Trigger", epnum="1x01"):
    return CoverageItem(
        media_type="episode", title=title, canonical_path=path,
        bazarr_sonarr_id=sid, episode_number=epnum,
        audio_langs=[lang] if lang else [], audio_source=source,
    )


def test_flags_two_foreign_languages():
    items = [_ep("Trigger", "kor", epnum="1x04"), _ep("Trigger", "rus", epnum="1x16")]
    n = _flag_mixed_language_series(items)
    assert n == 1
    assert all(it.series_mixed_languages for it in items)
    assert items[0].series_mixed_langs == ["ko", "ru"]  # normalized to ISO-639-1


def test_single_foreign_language_not_flagged():
    items = [_ep("Show", "kor", epnum="1x01"), _ep("Show", "kor", epnum="1x02")]
    assert _flag_mixed_language_series(items) == 0
    assert not any(it.series_mixed_languages for it in items)


def test_foreign_plus_english_dub_not_flagged():
    # Korean show with an English-dubbed episode — legitimate, must not flag.
    items = [_ep("Show", "kor", epnum="1x01"), _ep("Show", "en", epnum="1x02")]
    assert _flag_mixed_language_series(items) == 0


def test_ffprobe_tags_ignored():
    # Two foreign langs but only from the unreliable ffprobe tag → ignored.
    items = [_ep("Show", "kor", source="ffprobe", epnum="1x01"),
             _ep("Show", "rus", source="ffprobe", epnum="1x02")]
    assert _flag_mixed_language_series(items) == 0


def test_user_source_counts():
    items = [_ep("Show", "kor", source="user", epnum="1x01"),
             _ep("Show", "jpn", source="whisper", epnum="1x02")]
    assert _flag_mixed_language_series(items) == 1


def test_normalizes_language_variants():
    # 'eng' must normalize to English (excluded); 'kor'/'ko' collapse to one.
    items = [_ep("Show", "ko", epnum="1x01"), _ep("Show", "kor", epnum="1x02"),
             _ep("Show", "eng", epnum="1x03")]
    # only one distinct foreign (Korean) + English → not mixed
    assert _flag_mixed_language_series(items) == 0


def test_separate_series_not_conflated():
    # Two single-language series must not be merged into one mixed flag.
    items = [_ep("A", "kor", sid=1, path="TV/A"), _ep("B", "rus", sid=2, path="TV/B")]
    assert _flag_mixed_language_series(items) == 0


def test_dismissed_series_suppressed():
    items = [_ep("Trigger", "kor", epnum="1x04"), _ep("Trigger", "rus", epnum="1x16")]
    n = _flag_mixed_language_series(items, dismissed_series={"TV/Trigger"})
    assert n == 0
    assert not any(it.series_mixed_languages for it in items)


def test_groups_by_sonarr_id_not_title():
    # Same title, different series ids → not grouped together.
    items = [_ep("Dup", "kor", sid=1, path="TV/Dup (2001)"),
             _ep("Dup", "rus", sid=2, path="TV/Dup (2020)")]
    assert _flag_mixed_language_series(items) == 0


def test_movies_ignored():
    movies = [
        CoverageItem(media_type="movie", title="M1", canonical_path="Movies/M1",
                     audio_langs=["kor"], audio_source="whisper"),
        CoverageItem(media_type="movie", title="M2", canonical_path="Movies/M2",
                     audio_langs=["rus"], audio_source="whisper"),
    ]
    assert _flag_mixed_language_series(movies) == 0


# ─── dismiss persistence (migration 013 + store) ────────────────────


@pytest.fixture
def store(tmp_path: Path) -> AudioLangStore:
    db = tmp_path / "subarr.db"
    run_migrations(db)  # includes 013_mixed_language_dismissed
    return AudioLangStore(db)


def test_dismiss_roundtrip(store: AudioLangStore):
    assert store.get_mixed_dismissed_set() == set()
    store.dismiss_mixed("TV/Trigger", note="genuinely bilingual")
    assert store.get_mixed_dismissed_set() == {"TV/Trigger"}
    # idempotent upsert
    store.dismiss_mixed("TV/Trigger")
    assert store.get_mixed_dismissed_set() == {"TV/Trigger"}


def test_undismiss(store: AudioLangStore):
    store.dismiss_mixed("TV/Trigger")
    assert store.undismiss_mixed("TV/Trigger") is True
    assert store.get_mixed_dismissed_set() == set()
    assert store.undismiss_mixed("TV/Trigger") is False  # already gone


def test_dismiss_integrates_with_detector(store: AudioLangStore):
    items = [_ep("Trigger", "kor", epnum="1x04"), _ep("Trigger", "rus", epnum="1x16")]
    store.dismiss_mixed("TV/Trigger")
    n = _flag_mixed_language_series(items, dismissed_series=store.get_mixed_dismissed_set())
    assert n == 0
