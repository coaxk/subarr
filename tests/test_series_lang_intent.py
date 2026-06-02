"""#226: series-level audio-language intent memory tests."""
from __future__ import annotations

import pytest


@pytest.fixture
def store(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.audio_lang_store import AudioLangStore
    db = tmp_path / "audio_lang.db"
    run_migrations(db)  # schema is migration-owned now (no init_schema)
    yield AudioLangStore(db)


def test_get_returns_per_file_verification_when_present(store):
    store.upsert(canonical_path="TV/Cheers/Season 1/Cheers.S01E01.mkv",
                 lang_code="eng")
    v = store.get("TV/Cheers/Season 1/Cheers.S01E01.mkv")
    assert v is not None
    assert v.lang_code == "eng"
    assert v.source == "user"


def test_get_falls_through_to_series_intent(store):
    """No per-file verification, but series intent declared — return the
    intent's lang as a virtual verification so the rest of the app
    treats it as authoritative."""
    store.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    v = store.get("TV/Cheers/Season 1/Cheers.S01E01.mkv")
    assert v is not None
    assert v.lang_code == "eng"
    assert v.source.startswith("series_intent")
    assert v.evidence == {"inherited_from_series_prefix": "TV/Cheers/"}


def test_per_file_beats_series_intent(store):
    """If both exist, the per-file verification wins. Lets users correct
    individual episodes inside a declared-language series."""
    store.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    store.upsert(canonical_path="TV/Cheers/Season 1/Cheers.S01E05.mkv",
                 lang_code="fre")  # one French-dubbed episode
    v = store.get("TV/Cheers/Season 1/Cheers.S01E05.mkv")
    assert v.lang_code == "fre"
    assert v.source == "user"  # NOT series_intent


def test_longest_series_prefix_wins(store):
    """When two prefixes match (e.g. TV/Cheers/ and TV/Cheers/Season 2/
    were both declared), the longer prefix takes precedence."""
    store.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    store.set_series_intent(series_prefix="TV/Cheers/Season 2/",
                            lang_code="fre")
    v = store.get("TV/Cheers/Season 2/Cheers.S02E01.mkv")
    assert v.lang_code == "fre"


def test_intent_prefix_normalised_to_trailing_slash(store):
    """The caller may forget the trailing slash. We normalise it on
    insert so 'TV/Cheers' and 'TV/Cheers/' are the same intent."""
    store.set_series_intent(series_prefix="TV/Cheers", lang_code="eng")
    v = store.get("TV/Cheers/Season 1/Cheers.S01E01.mkv")
    assert v is not None
    intents = store.list_series_intents()
    assert intents[0]["series_prefix"] == "TV/Cheers/"


def test_get_all_as_lookup_excludes_series_intents(store):
    """Bulk path lookup intentionally doesn't expand series intents into
    every individual file — that would be O(declared series × episodes).
    Callers that need intent-aware lookup use get() per path."""
    store.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    store.upsert(canonical_path="TV/Other/S01E01.mkv", lang_code="kor")
    lookup = store.get_all_as_lookup()
    assert "TV/Other/S01E01.mkv" in lookup
    # No virtual rows for Cheers episodes:
    assert all("Cheers" not in k for k in lookup)


def test_delete_intent_removes_inheritance(store):
    store.set_series_intent(series_prefix="TV/Cheers/", lang_code="eng")
    assert store.get("TV/Cheers/Season 1/Cheers.S01E01.mkv") is not None
    assert store.delete_series_intent("TV/Cheers/") is True
    assert store.get("TV/Cheers/Season 1/Cheers.S01E01.mkv") is None
    # Deleting non-existent returns False.
    assert store.delete_series_intent("TV/Doesnt-Exist/") is False
