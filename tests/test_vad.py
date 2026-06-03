"""#110 / #65 — silero speech-window finder.

The VAD model integration (silero via onnxruntime) is I/O behind an
availability gate; the *selection* logic — given speech ranges, where to
place the listen/clip window — is pure and tested here. This is what fixes
the review player's "90% of clips have no voice" problem: silencedetect
finds non-silence (which includes MUSIC); silero finds actual SPEECH, and
this picks a window that sits inside it.

Rubric pinned here is behaviour, not implementation: a window lands inside
the largest speech region, clamps within the file, and prefers the cluster
with the most speech.
"""
from __future__ import annotations

import hashlib

import pytest


def _vad():
    from subarr import vad
    return vad


def test_returns_none_when_no_speech():
    vad = _vad()
    assert vad.select_speech_window([], 100.0, target_len=12.0) is None


def test_picks_window_inside_longest_speech_region():
    vad = _vad()
    # a 2s blip then a 40s speech region; the window must land in the big one
    speech = [(2.0, 4.0), (50.0, 90.0)]
    w = vad.select_speech_window(speech, 100.0, target_len=12.0)
    assert w is not None
    assert 50.0 <= w <= 78.0          # inside [50,90]
    assert w + 12.0 <= 90.0 + 0.01    # window fits inside the region


def test_window_clamped_within_duration():
    vad = _vad()
    # speech only near the very end, region shorter than the target window
    speech = [(95.0, 100.0)]
    w = vad.select_speech_window(speech, 100.0, target_len=12.0)
    assert w is not None
    assert w >= 0.0
    assert w + 12.0 <= 100.0 + 0.01


def test_prefers_cluster_with_most_speech():
    vad = _vad()
    speech = [(10.0, 13.0), (40.0, 70.0)]   # 3s vs 30s
    w = vad.select_speech_window(speech, 100.0, target_len=12.0)
    assert w is not None
    assert 40.0 <= w <= 58.0   # centred in the big cluster


# --- normalize_speech_ranges: clean silero's raw output -----------------

def test_normalize_merges_ranges_within_gap():
    vad = _vad()
    # 0.2s apart (< 0.3 gap) → merged into one contiguous range
    out = vad.normalize_speech_ranges([(1.0, 2.0), (2.2, 3.0)], gap_merge=0.3, min_speech=0.4)
    assert out == [(1.0, 3.0)]


def test_normalize_keeps_distant_ranges_separate():
    vad = _vad()
    out = vad.normalize_speech_ranges([(1.0, 2.0), (5.0, 6.0)], gap_merge=0.3, min_speech=0.4)
    assert out == [(1.0, 2.0), (5.0, 6.0)]


def test_normalize_drops_blips_below_min_speech():
    vad = _vad()
    # 0.2s blip, below the 0.4s floor → dropped
    out = vad.normalize_speech_ranges([(1.0, 1.2)], gap_merge=0.3, min_speech=0.4)
    assert out == []


def test_normalize_sorts_unordered_input():
    vad = _vad()
    out = vad.normalize_speech_ranges([(5.0, 6.0), (1.0, 2.0)], gap_merge=0.3, min_speech=0.4)
    assert out == [(1.0, 2.0), (5.0, 6.0)]


# --- availability gate: never hard-fails when silero is absent ----------

def test_vad_available_returns_bool_without_raising():
    vad = _vad()
    assert isinstance(vad.vad_available(), bool)


def test_detect_speech_ranges_returns_none_when_unavailable(monkeypatch):
    vad = _vad()
    monkeypatch.setattr(vad, "vad_available", lambda: False)
    assert vad.detect_speech_ranges("/nonexistent.mkv") is None


# --- _probs_to_ranges: per-window silero probs → speech ranges ----------
# (the pure half of the onnx inference; the ffmpeg decode + session.run is
#  live-verified I/O glue that calls into this.)

def test_probs_to_ranges_merges_contiguous_speech_windows():
    vad = _vad()
    # windows: no, yes, yes, no, yes  @ 32ms each
    ranges = vad._probs_to_ranges([0.1, 0.9, 0.9, 0.1, 0.8], window_s=0.032, threshold=0.5)
    assert len(ranges) == 2
    assert abs(ranges[0][0] - 0.032) < 1e-6 and abs(ranges[0][1] - 0.096) < 1e-6
    assert abs(ranges[1][0] - 0.128) < 1e-6 and abs(ranges[1][1] - 0.160) < 1e-6


def test_probs_to_ranges_all_silence_is_empty():
    vad = _vad()
    assert vad._probs_to_ranges([0.0, 0.1, 0.2], window_s=0.032, threshold=0.5) == []


def test_probs_to_ranges_all_speech_is_single_range():
    vad = _vad()
    ranges = vad._probs_to_ranges([0.9, 0.9, 0.9], window_s=0.032, threshold=0.5)
    assert len(ranges) == 1
    assert abs(ranges[0][0] - 0.0) < 1e-6 and abs(ranges[0][1] - 0.096) < 1e-6


# --- pull_model: pinned download + checksum verify + atomic write -------

def test_pull_model_downloads_and_verifies(tmp_path, monkeypatch):
    vad = _vad()
    target = tmp_path / "silero_vad.onnx"
    monkeypatch.setenv("SUBARR_VAD_MODEL_PATH", str(target))
    payload = b"FAKE_ONNX_BYTES"
    monkeypatch.setattr(vad, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    res = vad.pull_model(_fetch=lambda url: payload)
    assert res["status"] == "downloaded"
    assert target.is_file()
    assert target.read_bytes() == payload


def test_pull_model_idempotent_when_present(tmp_path, monkeypatch):
    vad = _vad()
    target = tmp_path / "silero_vad.onnx"
    monkeypatch.setenv("SUBARR_VAD_MODEL_PATH", str(target))
    payload = b"FAKE"
    monkeypatch.setattr(vad, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    vad.pull_model(_fetch=lambda url: payload)

    def _boom(url):
        raise AssertionError("must not refetch when the verified model exists")

    res = vad.pull_model(_fetch=_boom)
    assert res["status"] == "present"


def test_pull_model_rejects_checksum_mismatch(tmp_path, monkeypatch):
    vad = _vad()
    target = tmp_path / "silero_vad.onnx"
    monkeypatch.setenv("SUBARR_VAD_MODEL_PATH", str(target))
    monkeypatch.setattr(vad, "MODEL_SHA256", "deadbeef")
    with pytest.raises(ValueError):
        vad.pull_model(_fetch=lambda url: b"corrupted-or-tampered")
    assert not target.is_file()   # nothing persisted on a bad checksum
