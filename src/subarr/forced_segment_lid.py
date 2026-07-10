"""#364 slice 2 -- the local windowed LID backend.

Groups VAD utterances into ~lid_window_s windows, classifies each window ONCE
with silero-lang95 (local, no subgen round-trip), applies the confidence gate,
and returns the per-utterance classified list slice-1's merge/bail already
consume. Selected by ForcedSegmentGenerator when the model is available; the
subgen per-utterance path is the fallback.
"""

from __future__ import annotations

import asyncio
import logging
import os
import wave
from typing import Callable

from . import lid as lid_module
from .forced_segment import (
    ForcedSegmentParams,
    Utterance,
    assemble_windows,
    expand_window_verdicts,
    window_is_foreign,
)

log = logging.getLogger(__name__)


def _read_wav_f32(path: str):
    """Read a 16 kHz mono int16 wav -> float32 [-1, 1] numpy array."""
    import numpy as np

    with wave.open(path) as w:
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, np.int16).astype(np.float32) / 32768.0


class LocalLidBackend:
    """classify(fs_path, utterances, tmp) -> list[(utterance, foreign_bool)].
    clip_fn/read_fn/classify_fn are injected so the logic is testable without
    ffmpeg or the real model. A window whose clip or inference fails is
    over-flagged (completeness bias), matching slice-1's clip-failure handling."""

    def __init__(
        self,
        *,
        params: ForcedSegmentParams,
        classify_fn: Callable[..., "tuple[str, float, float] | None"] | None = None,
        clip_fn: Callable[..., None] | None = None,
        read_fn: Callable[[str], object] | None = None,
    ):
        self._params = params
        self._classify = classify_fn or lid_module.classify_samples
        if clip_fn is None:
            from .forced_segment import clip_audio

            clip_fn = clip_audio
        self._clip = clip_fn
        self._read = read_fn or _read_wav_f32

    async def classify(self, fs_path: str, utterances: list[Utterance], tmp) -> list:
        windows = assemble_windows(utterances, self._params.lid_window_s)
        flags: list[bool] = []
        for i, (w_start, w_end, _idxs) in enumerate(windows):
            clip = os.path.join(tmp, f"lidwin-{i}.wav")
            try:
                # Off-loop (#364): ffmpeg + onnxruntime are blocking.
                await asyncio.to_thread(self._clip, fs_path, w_start, w_end, clip)
                samples = await asyncio.to_thread(self._read, clip)
                verdict = await asyncio.to_thread(self._classify, samples)
            except Exception as exc:  # noqa: BLE001 - a bad window is suspect, never fatal
                log.warning("forced-segment: local LID failed for window %.1f-%.1fs: %s", w_start, w_end, exc)
                flags.append(True)  # over-flag on hard failure
                continue
            if verdict is None:
                flags.append(True)  # model unavailable mid-run -> over-flag
                continue
            top_lang, top_prob, en_prob = verdict
            flags.append(window_is_foreign(top_lang, top_prob, en_prob, self._params))
        return expand_window_verdicts(utterances, windows, flags)
