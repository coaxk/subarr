"""#111 — audio_sampler picks speech via silero VAD when available, and
falls back to silencedetect otherwise. Pins the WIRING (which path runs),
mocking the ffprobe / silence / VAD I/O so no media or model is needed.
"""

from __future__ import annotations

import asyncio


def _mods():
    from subarr import audio_sampler as a
    from subarr import vad

    return a, vad


def test_find_dialog_positions_uses_vad_when_available(monkeypatch):
    a, vad = _mods()

    async def fake_probe(path):
        return (100.0, 2)

    monkeypatch.setattr(a, "_ffprobe_duration_and_tracks", fake_probe)
    monkeypatch.setattr(vad, "vad_available", lambda: True)
    monkeypatch.setattr(vad, "detect_speech_ranges", lambda p, track=0: [(40.0, 70.0)])

    res = asyncio.run(a.find_dialog_positions("/x.mkv", n=3, use_cache=False))
    assert res.method == "vad"
    assert res.positions
    # every suggested position sits inside the detected speech region
    assert all(40.0 <= p <= 70.0 for p in res.positions)


def test_find_dialog_positions_falls_back_to_silencedetect(monkeypatch):
    a, vad = _mods()

    async def fake_probe(path):
        return (100.0, 1)

    async def fake_silence(path, **kw):
        return [(10.0, 12.0)]  # a single silence range → speech elsewhere

    monkeypatch.setattr(a, "_ffprobe_duration_and_tracks", fake_probe)
    monkeypatch.setattr(vad, "vad_available", lambda: False)
    monkeypatch.setattr(a, "_silencedetect", fake_silence)

    res = asyncio.run(a.find_dialog_positions("/x.mkv", n=2, use_cache=False))
    assert res.method == "silencedetect"
    assert res.positions


def test_vad_disabled_setting_forces_silencedetect(monkeypatch):
    a, vad = _mods()

    async def fake_probe(path):
        return (100.0, 1)

    async def fake_silence(path, **kw):
        return [(10.0, 12.0)]

    monkeypatch.setattr(a, "_ffprobe_duration_and_tracks", fake_probe)
    # VAD is technically available, but the user has it switched off
    monkeypatch.setattr(vad, "vad_available", lambda: True)
    monkeypatch.setattr(a, "_vad_enabled", lambda: False)
    monkeypatch.setattr(a, "_silencedetect", fake_silence)

    res = asyncio.run(a.find_dialog_positions("/x.mkv", n=2, use_cache=False))
    assert res.method == "silencedetect"
