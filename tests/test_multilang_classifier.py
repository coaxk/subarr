"""#357 — classify_high_conf_langs: languages with >=1 chunk prob >= T.

>=2 distinct high-conf langs => confident multilingual (returns them, ordered).
==1 => single (noise chunks in other langs ignored).
==0 => confused (returns []) -> caller falls back to today's tag path.
"""

from __future__ import annotations

from subarr.multilang import classify_high_conf_langs


def test_the_beasts_three_high_conf_distinct_is_multilingual():
    chunks = [("gl", 0.91), ("es", 0.88), ("fr", 0.76)]
    assert classify_high_conf_langs(chunks, 0.5) == ["gl", "es", "fr"]


def test_three_low_conf_distinct_is_confused_empty():
    chunks = [("gl", 0.20), ("es", 0.18), ("fr", 0.11)]
    assert classify_high_conf_langs(chunks, 0.5) == []


def test_one_high_conf_plus_two_low_noise_is_single():
    chunks = [("de", 0.95), ("en", 0.12), ("nn", 0.09)]
    assert classify_high_conf_langs(chunks, 0.5) == ["de"]


def test_two_high_conf_same_lang_is_single():
    chunks = [("ja", 0.9), ("ja", 0.8), ("ja", 0.7)]
    assert classify_high_conf_langs(chunks, 0.5) == ["ja"]


def test_none_probabilities_treated_as_below_threshold():
    # absent per-chunk probability (pre-#396 subgen) -> no high-conf langs.
    chunks = [("gl", None), ("es", None), ("fr", None)]
    assert classify_high_conf_langs(chunks, 0.5) == []


def test_ordering_is_first_high_conf_appearance():
    chunks = [("es", 0.6), ("gl", 0.9), ("es", 0.95), ("fr", 0.55)]
    assert classify_high_conf_langs(chunks, 0.5) == ["es", "gl", "fr"]


def test_ignores_und_language():
    chunks = [("und", 0.99), ("gl", 0.9), ("es", 0.8)]
    assert classify_high_conf_langs(chunks, 0.5) == ["gl", "es"]
