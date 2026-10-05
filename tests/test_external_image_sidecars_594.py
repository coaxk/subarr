"""#594: external VobSub .idx/.sub sidecars were invisible to Coverage.

Reported by jkbhome against 2.7.16. A movie with a valid external VobSub pair
beside the MKV came back `has_sub_on_disk: false`, `sub_files_seen: []`,
`embedded_en: null` — indistinguishable from a film with no subtitles at all.
He audited the library and found 102 complete .idx/.sub pairs, 101 of them
containing English, so subarr would have queued Whisper for a hundred films
that already had usable English subs.

The cause is simple: disk discovery only ever looked for `.srt`
(`srt_sidecar_names`, `_scan_for_srt`, `_scan_for_srt_recursive`). #458 taught
the EMBEDDED path to classify bitmap tracks as `EN(image)`; nothing taught the
DISK path that a subtitle can be a picture.

Deliberately NOT modelled as `embedded_en = 'EN(image)'`, which is what the
report suggested: the file is external, and that field renders as
"embedded: EN(image)" in the UI, which sends the reader hunting for a track
inside the container that is not there. A separate field feeds the same
#458 image-only workflow, so behaviour matches and the label stays honest.

A pair must be COMPLETE. A `.idx` is an index into its `.sub` bitmap blob;
alone it is not subtitle coverage, and treating it as such would suppress a
real gap.
"""

from __future__ import annotations

from pathlib import Path

from subarr.paths import image_sidecar_names, srt_sidecar_names


def _touch(parent: Path, *names: str) -> None:
    for n in names:
        (parent / n).write_bytes(b"\x00")


def test_a_complete_idx_sub_pair_is_found(tmp_path: Path):
    _touch(tmp_path, "Whiplash.mkv", "Whiplash.idx", "Whiplash.sub")
    assert image_sidecar_names(tmp_path, "Whiplash") == ["Whiplash.idx"]


def test_a_lone_idx_is_not_coverage(tmp_path: Path):
    # The .idx is only an index; without its .sub there are no bitmaps to show.
    _touch(tmp_path, "Whiplash.mkv", "Whiplash.idx")
    assert image_sidecar_names(tmp_path, "Whiplash") == []


def test_a_lone_sub_is_not_coverage(tmp_path: Path):
    # A .sub with no .idx cannot be indexed into, and `.sub` is also a
    # MicroDVD text extension, so a bare .sub must never imply VobSub.
    _touch(tmp_path, "Whiplash.mkv", "Whiplash.sub")
    assert image_sidecar_names(tmp_path, "Whiplash") == []


def test_language_tagged_pairs_are_found(tmp_path: Path):
    _touch(tmp_path, "Whiplash.mkv", "Whiplash.en.idx", "Whiplash.en.sub")
    assert image_sidecar_names(tmp_path, "Whiplash") == ["Whiplash.en.idx"]


def test_uppercase_extensions_are_found(tmp_path: Path):
    # DVD rippers emit .IDX/.SUB; a case-sensitive check loses real libraries.
    _touch(tmp_path, "Movie.mkv", "Movie.IDX", "Movie.SUB")
    assert image_sidecar_names(tmp_path, "Movie") == ["Movie.IDX"]


def test_another_videos_pair_is_not_claimed(tmp_path: Path):
    # #558's lesson: compare LITERALLY, never as a glob. In a glob `[...]` is
    # a character class and release names are full of brackets, so a glob for
    # `Show [a]*` both misses this video's own pair and matches another's.
    _touch(tmp_path, "Show [a].mkv", "Show [a].idx", "Show [a].sub")
    _touch(tmp_path, "Show a.idx", "Show a.sub")
    assert image_sidecar_names(tmp_path, "Show [a]") == ["Show [a].idx"]


def test_srt_discovery_is_unchanged_and_ignores_image_pairs(tmp_path: Path):
    # The two must stay separate: an image pair is not a text sub, so it must
    # not raise has_sub_on_disk (which drives "stale: disk already has .srt"
    # and a Bazarr rescan suggestion).
    _touch(tmp_path, "Whiplash.mkv", "Whiplash.idx", "Whiplash.sub")
    assert srt_sidecar_names(tmp_path, "Whiplash") == []
    _touch(tmp_path, "Whiplash.en.srt")
    assert srt_sidecar_names(tmp_path, "Whiplash") == ["Whiplash.en.srt"]
    assert image_sidecar_names(tmp_path, "Whiplash") == ["Whiplash.idx"]


# ── fillability: whether subgen will actually skip the file ──────────────────
#
# This is the half most easily got wrong. `image_only_subgen_will_skip` means
# "the gap is real but nothing subarr can send will fill it". For an EMBEDDED
# bitmap track that is unconditional (#458). For an EXTERNAL pair it is NOT:
# subgen's has_external_subtitle_in_language() lists `.idx`/`.sub` among the
# extensions it recognises, but only counts one as coverage when a language
# token is parseable from the FILENAME. So a multilingual `Movie.idx` is
# transcribed, while `Movie.en.idx` is skipped. Marking every pair unfillable
# would have stranded 101 of the reporter's 102 films.

from subarr.coverage_engine import _is_en_sidecar_for  # noqa: E402


def test_an_untagged_idx_is_not_english_so_the_gap_stays_fillable():
    assert _is_en_sidecar_for("Whiplash.idx", "Whiplash", (".idx",)) is False


def test_an_en_tagged_idx_is_english_so_subgen_would_skip():
    assert _is_en_sidecar_for("Whiplash.en.idx", "Whiplash", (".idx",)) is True
    assert _is_en_sidecar_for("Whiplash.eng.idx", "Whiplash", (".idx",)) is True


def test_a_foreign_tagged_idx_is_not_english():
    assert _is_en_sidecar_for("Whiplash.fr.idx", "Whiplash", (".idx",)) is False
    assert _is_en_sidecar_for("Whiplash.es.idx", "Whiplash", (".idx",)) is False


def test_uppercase_idx_extension_still_parses_its_language():
    assert _is_en_sidecar_for("Movie.en.IDX", "Movie", (".idx",)) is True


def test_the_default_suffix_is_unchanged_for_every_existing_caller():
    # The signature gained a defaulted parameter; .srt behaviour must not move.
    assert _is_en_sidecar_for("Show.en.srt", "Show") is True
    assert _is_en_sidecar_for("Show.srt", "Show") is False
    assert _is_en_sidecar_for("Show.en.idx", "Show") is False  # .idx not matched by default


# ── end to end through /api/coverage ────────────────────────────────────────
#
# The unit tests above prove the primitives. This one proves the field actually
# POPULATES through a real coverage walk and reaches the JSON the reporter was
# reading — which is the step that unit tests cannot stand in for.

import pytest  # noqa: E402
from test_coverage import _ALL_STUB  # noqa: E402


@pytest.fixture
def media_root_with_vobsub(tmp_path, media_root):
    """Same shape as test_coverage's fixture, plus a complete VobSub pair on
    the episode that has no .srt — i.e. the reporter's situation."""
    (media_root / "TV" / "Foreign Drama").mkdir(parents=True)
    (media_root / "TV" / "Foreign Drama" / "S01E03.mkv").write_bytes(b"")
    (media_root / "TV" / "Foreign Drama" / "S01E03.idx").write_text("# VobSub index\n")
    (media_root / "TV" / "Foreign Drama" / "S01E03.sub").write_bytes(b"\x00" * 16)
    (media_root / "TV" / "Already Subbed").mkdir(parents=True)
    (media_root / "TV" / "Already Subbed" / "S01E01.mkv").write_bytes(b"")
    (media_root / "TV" / "Already Subbed" / "S01E01.en.srt").write_text("1\n")
    yield media_root


@pytest.mark.integrations_stub(**_ALL_STUB)
def test_coverage_reports_an_external_image_pair(app_with_stub, media_root_with_vobsub):
    r = app_with_stub.get("/api/coverage?fresh=true&hide_stale_disk=false&hide_english_audio=false")
    assert r.status_code == 200
    items = {i["title"]: i for i in r.json()["items"]}
    foreign = items["Foreign Drama"]

    # The whole bug: this used to be [] and the row read as "no subtitles".
    assert "S01E03.idx" in foreign["image_subs_on_disk"]

    # And the two must stay distinct. has_sub_on_disk means a TEXT sub; it
    # drives "stale: disk already has .srt" plus a Bazarr rescan suggestion,
    # and a bitmap pair must not trigger either.
    assert foreign["has_sub_on_disk"] is False
    assert foreign["sub_files_seen"] == []
    assert not any("stale" in reason for reason in foreign["score_reasons"])


@pytest.mark.integrations_stub(**_ALL_STUB)
def test_an_untagged_pair_is_not_marked_unfillable(app_with_stub, media_root_with_vobsub):
    # `S01E03.idx` carries no language token, so subgen will transcribe it
    # rather than skip it. Marking the row unfillable would strand a real,
    # fillable gap — which is what would have happened to 101 of the
    # reporter's 102 films.
    r = app_with_stub.get("/api/coverage?fresh=true&hide_stale_disk=false&hide_english_audio=false")
    foreign = {i["title"]: i for i in r.json()["items"]}["Foreign Drama"]
    assert foreign["image_only_subgen_will_skip"] is False
