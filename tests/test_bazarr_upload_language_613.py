"""#613: the Bazarr direct upload labelled every subtitle English.

`_try_upload_to_bazarr` sent `language="en"` on both the episode and the movie
upload, whatever subgen wrote. A Norwegian transcription (`<stem>.nor.srt`)
reached Bazarr as English, and Bazarr saved the Norwegian text as `.en.srt`.
The language now comes from the name of the file being uploaded: that name is
what subgen wrote, so it is the record of what the file actually is.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

EPISODE = "ShowTV/Season 1/ShowTV.S01E01.mkv"


def _watcher(bundle):
    from subarr.completion_watcher import CompletionWatcher

    prov = SimpleNamespace(
        mark_bazarr_triggered=lambda _id: None,
        completed_without_bazarr=lambda max_age_s=0: [],
    )
    return CompletionWatcher(provenance=prov, bundle_provider=lambda: bundle)


def _seed(canonical: str, sidecar_suffix: str) -> None:
    from subarr.paths import canonical_to_fs

    video = Path(canonical_to_fs(canonical))
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"\x00")
    (video.parent / f"{video.stem}{sidecar_suffix}").write_text("1\n00:00:01,000 --> 00:00:02,000\nhei\n")


def _sent_languages(calls, path):
    sent = []
    for c in calls.get(("bazarr", ""), []):
        if c["path"] != path:
            continue
        m = re.search(r'name="language"\r\n\r\n([^\r\n]*)', c["body"] or "")
        sent.append(m.group(1) if m else None)
    return sent


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        (".nor.srt", "no"),  # the reported case: subgen's 3-letter code
        (".no.srt", "no"),
        (".subgen.large-v3.nor.srt", "no"),  # SHOW_IN_SUBNAME_* naming
        (".de.srt", "de"),
        (".en.srt", "en"),
        (".eng.srt", "en"),
        (".en.sdh.srt", "en"),  # caption marker after the language
        (".en.hi.srt", "en"),  # `hi` after a language is the HI marker
        (".hi.srt", "hi"),  # on its own it is Hindi
    ],
)
def test_episode_upload_sends_the_language_subgen_wrote(writeback_stack, suffix, expected):
    _seed(EPISODE, suffix)
    entry = SimpleNamespace(id=1, canonical_path=EPISODE, sonarr_episode_id=2201, series_id=22)

    assert asyncio.run(_watcher(writeback_stack.bundle)._try_upload_to_bazarr(entry)) is True
    assert _sent_languages(writeback_stack.calls, "/api/episodes/subtitles") == [expected]


def test_movie_upload_sends_the_language_subgen_wrote(writeback_stack):
    _seed(EPISODE, ".nor.srt")
    entry = SimpleNamespace(
        id=2, canonical_path=EPISODE, sonarr_episode_id=None, series_id=None, radarr_movie_id=909
    )

    assert asyncio.run(_watcher(writeback_stack.bundle)._try_upload_to_bazarr(entry)) is True
    assert _sent_languages(writeback_stack.calls, "/api/movies/subtitles") == ["no"]


def test_a_sidecar_with_no_language_in_its_name_keeps_the_english_default(writeback_stack):
    # Nothing in the name says what the file is. English is subgen's default
    # output language, and it is what every earlier release sent here.
    _seed(EPISODE, ".srt")
    entry = SimpleNamespace(id=3, canonical_path=EPISODE, sonarr_episode_id=2201, series_id=22)

    assert asyncio.run(_watcher(writeback_stack.bundle)._try_upload_to_bazarr(entry)) is True
    assert _sent_languages(writeback_stack.calls, "/api/episodes/subtitles") == ["en"]


def test_a_title_word_is_never_sent_as_the_language(writeback_stack):
    # Only the part after the video's stem is the sidecar's own suffix. `Who`
    # is a word of the title, and a whole-name scan would send it to Bazarr.
    canonical = "Doctor Who/Season 1/Doctor.Who.mkv"
    _seed(canonical, ".srt")
    entry = SimpleNamespace(id=4, canonical_path=canonical, sonarr_episode_id=2201, series_id=22)

    assert asyncio.run(_watcher(writeback_stack.bundle)._try_upload_to_bazarr(entry)) is True
    assert _sent_languages(writeback_stack.calls, "/api/episodes/subtitles") == ["en"]
