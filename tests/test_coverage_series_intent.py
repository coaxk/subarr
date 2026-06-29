"""#69: series-level audio-language intent expands to NEW episodes during a
coverage build.

The store already inherits intent on a per-path get() (#226), but the coverage
build loads verifications in BULK via get_all_as_lookup(), which deliberately
does NOT flatten series intents (would be O(series x episodes)). So a declared
series whose new episode has no per-file verification row was NOT auto-applied
during classification. This wires the intent-aware lookup into build_coverage's
user_verifications so a matching new episode resolves as user-verified.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def store(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.audio_lang_store import AudioLangStore

    db = tmp_path / "audio_lang.db"
    run_migrations(db)
    yield AudioLangStore(db)


def _ep(canonical, file_canonical):
    from subarr.coverage_engine import CoverageItem

    return CoverageItem(
        media_type="episode",
        title="Cheers",
        canonical_path=canonical,
        episode_number="1x9",
        file_canonical_path=file_canonical,
        audio_langs=["und"],
        original_language="english",
    )


def test_intent_lookup_resolves_new_episode_under_declared_series(store):
    """A series intent declared on the parent prefix makes a NEW episode
    (no per-file verification) resolve via the bulk lookup used by the
    coverage build."""
    from subarr.coverage_engine import _build_verification_lookup

    store.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    user_verifications, verification_sources = _build_verification_lookup(store)

    new_ep = "TV/Cheers/Season 1/Cheers.S01E09.mkv"
    # The new episode has no per-file row, yet membership + value resolve via
    # longest-prefix series-intent inheritance.
    assert new_ep in user_verifications
    assert user_verifications[new_ep] == "en"  # #358: 2-letter canonical
    assert verification_sources.get(new_ep).startswith("series_intent")


def test_classify_marks_new_episode_verified_from_series_intent(store):
    """End-to-end through the classifier: a declared series turns an
    un-tagged (und) new episode into an audio-verified row."""
    from subarr.coverage_engine import (
        _build_verification_lookup,
        _classify_audio_label,
    )

    store.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    user_verifications, _ = _build_verification_lookup(store)

    item = _ep("TV/Cheers", "TV/Cheers/Season 1/Cheers.S01E09.mkv")
    assert item.audio_verified is False  # default before classify

    _classify_audio_label(item, user_verifications=user_verifications)

    assert item.audio_verified is True
    assert item.audio_langs == ["en"]  # #358: 2-letter canonical
    assert item.audio_label_unknown is False


def test_per_file_verification_still_wins_over_intent(store):
    """A real per-file verification must beat the inherited series intent."""
    from subarr.coverage_engine import _build_verification_lookup

    store.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    store.upsert(canonical_path="TV/Cheers/Season 1/Cheers.S01E05.mkv", lang_code="fre")
    user_verifications, _ = _build_verification_lookup(store)

    assert user_verifications["TV/Cheers/Season 1/Cheers.S01E05.mkv"] == "fr"  # #358: 2-letter


def test_unrelated_path_not_matched(store):
    """A path outside any declared series prefix is NOT a member."""
    from subarr.coverage_engine import _build_verification_lookup

    store.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    user_verifications, _ = _build_verification_lookup(store)

    assert "TV/Frasier/Season 1/Frasier.S01E01.mkv" not in user_verifications


def test_no_store_yields_empty_lookups():
    from subarr.coverage_engine import _build_verification_lookup

    uv, vs = _build_verification_lookup(None)
    assert uv == {}
    assert vs == {}
