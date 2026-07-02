"""#357 — SUBARR_MULTILANG_CHUNK_MIN_PROB config knob (float default 0.5)."""

from __future__ import annotations

import importlib


def test_default_is_half(monkeypatch):
    monkeypatch.delenv("SUBARR_MULTILANG_CHUNK_MIN_PROB", raising=False)
    from subarr import config

    importlib.reload(config)
    assert config.settings.multilang_chunk_min_prob == 0.5


def test_env_override(monkeypatch):
    monkeypatch.setenv("SUBARR_MULTILANG_CHUNK_MIN_PROB", "0.7")
    from subarr import config

    importlib.reload(config)
    assert config.settings.multilang_chunk_min_prob == 0.7


def test_blank_falls_back_to_default(monkeypatch):
    # _env_or treats empty/whitespace as missing (a commented-out .env line).
    monkeypatch.setenv("SUBARR_MULTILANG_CHUNK_MIN_PROB", "  ")
    from subarr import config

    importlib.reload(config)
    assert config.settings.multilang_chunk_min_prob == 0.5
