"""#357 — AudioLangStore persists lang_class/lang_codes; singular lang_code
always populated; malformed lang_codes JSON degrades to single; the multi
lookup surfaces only multilingual rows."""

from __future__ import annotations

from pathlib import Path

from subarr.migrate import run_migrations


def _store(tmp_path: Path):
    # Apply migrations first so 027's columns exist, then open the store.
    db = tmp_path / "subarr.db"
    run_migrations(db)
    from subarr.audio_lang_store import AudioLangStore

    return AudioLangStore(db)


def test_single_roundtrip_defaults_class_single(tmp_path):
    store = _store(tmp_path)
    store.upsert(canonical_path="TV/S/ep.mkv", lang_code="ja", source="user")
    v = store.get("TV/S/ep.mkv")
    assert v.lang_code == "ja"
    assert v.lang_class == "single"
    assert v.lang_codes is None


def test_multi_roundtrip_populates_singular_and_set(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        canonical_path="Movies/TheBeasts.mkv",
        lang_code="gl",  # first-of-set
        source="auto-high-conf-multi",
        confidence=0.9,
        lang_class="multi",
        lang_codes=["gl", "es", "fr"],
    )
    v = store.get("Movies/TheBeasts.mkv")
    assert v.lang_code == "gl"  # singular ALWAYS populated (consumers keep working)
    assert v.lang_class == "multi"
    assert v.lang_codes == ["gl", "es", "fr"]
    assert v.source == "auto-high-conf-multi"
    # to_dict carries the new fields for the API/UI
    assert v.to_dict()["lang_class"] == "multi"
    assert v.to_dict()["lang_codes"] == ["gl", "es", "fr"]


def test_list_all_carries_multi_fields(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        canonical_path="Movies/TheBeasts.mkv",
        lang_code="gl",
        source="auto-high-conf-multi",
        lang_class="multi",
        lang_codes=["gl", "es", "fr"],
    )
    rows = store.list_all()
    assert rows[0].lang_class == "multi"
    assert rows[0].lang_codes == ["gl", "es", "fr"]


def test_malformed_lang_codes_json_degrades_to_single(tmp_path):
    store = _store(tmp_path)
    store.upsert(canonical_path="x.mkv", lang_code="gl", source="user")
    # corrupt the JSON directly
    store._conn.execute(
        "UPDATE audio_lang_verifications SET lang_class='multi', lang_codes='{not json' WHERE canonical_path='x.mkv'"
    )
    v = store.get("x.mkv")
    assert v.lang_codes is None  # malformed -> None, no crash
    assert v.lang_code == "gl"


def test_get_all_multi_as_lookup(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        canonical_path="a.mkv",
        lang_code="gl",
        source="auto-high-conf-multi",
        lang_class="multi",
        lang_codes=["gl", "es"],
    )
    store.upsert(canonical_path="b.mkv", lang_code="ja", source="user")  # single
    assert store.get_all_multi_as_lookup() == {"a.mkv": ["gl", "es"]}
