"""#159: mkvpropedit default-track swap."""
from __future__ import annotations

import pytest

from subarr import track_swap
from subarr.track_swap import (
    TrackSwapError,
    build_mkvpropedit_args,
    swap_default_audio_track,
)


def test_build_args_sets_target_default_clears_others():
    args = build_mkvpropedit_args("/media/x.mkv", target_ordinal=2, audio_ordinals=[1, 2])
    assert args == [
        "mkvpropedit", "/media/x.mkv",
        "--edit", "track:a1", "--set", "flag-default=0",
        "--edit", "track:a2", "--set", "flag-default=1",
    ]


def test_build_args_three_tracks_only_one_default():
    args = build_mkvpropedit_args("/m.mkv", target_ordinal=3, audio_ordinals=[1, 2, 3])
    flags = [args[i + 1] for i, a in enumerate(args) if a == "--set"]
    assert flags == ["flag-default=0", "flag-default=0", "flag-default=1"]


@pytest.mark.asyncio
async def test_swap_raises_when_target_not_in_ordinals(monkeypatch):
    monkeypatch.setattr(track_swap, "mkvpropedit_available", lambda: True)
    with pytest.raises(TrackSwapError, match="not in"):
        await swap_default_audio_track("/m.mkv", target_ordinal=5, audio_ordinals=[1, 2])


@pytest.mark.asyncio
async def test_swap_raises_when_tool_missing(monkeypatch):
    monkeypatch.setattr(track_swap, "mkvpropedit_available", lambda: False)
    with pytest.raises(TrackSwapError, match="mkvpropedit not found"):
        await swap_default_audio_track("/m.mkv", target_ordinal=1, audio_ordinals=[1, 2])


@pytest.mark.asyncio
async def test_swap_success(monkeypatch):
    captured = {}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return (b"", b"")

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _Proc()

    monkeypatch.setattr(track_swap, "mkvpropedit_available", lambda: True)
    monkeypatch.setattr(track_swap.asyncio, "create_subprocess_exec", _fake_exec)
    await swap_default_audio_track("/m.mkv", target_ordinal=2, audio_ordinals=[1, 2])
    assert captured["args"][0] == "mkvpropedit"
    assert "track:a2" in captured["args"]


@pytest.mark.asyncio
async def test_swap_raises_on_hard_error(monkeypatch):
    class _Proc:
        returncode = 2

        async def communicate(self):
            return (b"", b"boom")

    async def _fake_exec(*args, **kwargs):
        return _Proc()

    monkeypatch.setattr(track_swap, "mkvpropedit_available", lambda: True)
    monkeypatch.setattr(track_swap.asyncio, "create_subprocess_exec", _fake_exec)
    with pytest.raises(TrackSwapError, match="exit 2"):
        await swap_default_audio_track("/m.mkv", target_ordinal=1, audio_ordinals=[1, 2])


@pytest.mark.asyncio
async def test_swap_tolerates_warning_exit(monkeypatch):
    class _Proc:
        returncode = 1

        async def communicate(self):
            return (b"", b"warning: something minor")

    async def _fake_exec(*args, **kwargs):
        return _Proc()

    monkeypatch.setattr(track_swap, "mkvpropedit_available", lambda: True)
    monkeypatch.setattr(track_swap.asyncio, "create_subprocess_exec", _fake_exec)
    # exit 1 = warnings but applied → no raise
    await swap_default_audio_track("/m.mkv", target_ordinal=1, audio_ordinals=[1, 2])
