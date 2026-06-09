"""#90 slice 1 — detect-and-persist the spoken language (ground truth).

Robustly detect a file's language via subgen /detect_language_robust (multi-chunk
majority, skips intro/silence) and persist it as a whisper-sourced verification.
Pure logic over stubs — the real store + client are tested elsewhere.
"""

from __future__ import annotations

import pytest


class _StubStore:
    def __init__(self):
        self.upserts = []

    def upsert(self, **kw):
        self.upserts.append(kw)


class _StubSubgen:
    def __init__(self, resp=None, raises=False):
        self._resp = resp
        self._raises = raises
        self.calls = []

    async def detect_language_robust(self, path):
        self.calls.append(path)
        if self._raises:
            raise RuntimeError("subgen down")
        return self._resp


@pytest.mark.asyncio
async def test_verify_stores_whisper_language_on_majority():
    from subarr.audio_lang_verify import verify_audio_language

    resp = {"aggregate": {"language": "korean", "n_agreeing": 3, "n_total": 3, "min_probability": 0.9}}
    store = _StubStore()
    out = await verify_audio_language(_StubSubgen(resp), store, "TV/Show/ep.mkv", to_subgen=lambda p: p)
    assert out == ("ko", 1.0)  # normalized to ISO-639-1
    assert len(store.upserts) == 1
    u = store.upserts[0]
    assert u["source"] == "whisper" and u["lang_code"] == "ko" and u["confidence"] == 1.0
    assert u["canonical_path"] == "TV/Show/ep.mkv"


@pytest.mark.asyncio
async def test_verify_withholds_on_no_majority():
    from subarr.audio_lang_verify import verify_audio_language

    resp = {"aggregate": {"language": "en", "n_agreeing": 1, "n_total": 3}}
    store = _StubStore()
    out = await verify_audio_language(_StubSubgen(resp), store, "x.mkv", to_subgen=lambda p: p)
    assert out is None and store.upserts == []  # never persist a guess


@pytest.mark.asyncio
async def test_verify_graceful_when_subgen_errors():
    from subarr.audio_lang_verify import verify_audio_language

    store = _StubStore()
    out = await verify_audio_language(_StubSubgen(raises=True), store, "x.mkv", to_subgen=lambda p: p)
    assert out is None and store.upserts == []
