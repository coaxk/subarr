"""#358: the coverage payload emits audio_langs as 2-letter ISO-639-1 so the
frontend audio-language picker (2-letter options) pre-selects the detected audio
correctly. Raw ffprobe tags are commonly 3-letter ('eng'/'nor'), which would not
match the 2-letter <option> values otherwise (blank select)."""

from __future__ import annotations

from subarr.coverage_engine import CoverageItem


def test_to_dict_normalizes_audio_langs_to_2letter():
    item = CoverageItem(media_type="episode", title="Foreign Drama", audio_langs=["eng", "nor", "gl"])
    assert item.to_dict()["audio_langs"] == ["en", "no", "gl"]


def test_to_dict_audio_langs_passthrough_when_no_iso1_exists():
    # Hawaiian / Cantonese have no ISO-639-1 code; und stays und.
    item = CoverageItem(media_type="episode", title="X", audio_langs=["haw", "und"])
    assert item.to_dict()["audio_langs"] == ["haw", "und"]
