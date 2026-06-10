"""Path translation regression tests.

The canonical_to_subgen_batch prefix was wrong since Phase 1 ('/media/library/'
instead of '/media/') and survived because every scan test mocked subgen with
a transport that ignored the directory= query value. First live end-to-end
scan from the GUI caught it. These tests pin the contract.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


def test_split_canonical_default_and_qualified(subarr_env):
    from subarr.paths import _split_canonical

    assert _split_canonical("TV/Show/ep.mkv") == ("", "TV/Show/ep.mkv")
    assert _split_canonical("/TV/Show/") == ("", "TV/Show")
    assert _split_canonical("@disk2/Movies/x.mkv") == ("disk2", "Movies/x.mkv")
    assert _split_canonical("@disk2/") == ("disk2", "")
    assert _split_canonical("@disk2") == ("disk2", "")
    assert _split_canonical("") == ("", "")


def test_canonical_to_subgen_batch_strips_and_prefixes(subarr_env):
    from subarr.paths import canonical_to_subgen_batch

    assert canonical_to_subgen_batch("TV/Foo/Season 1") == "/media/TV/Foo/Season 1"
    assert canonical_to_subgen_batch("/TV/Foo/") == "/media/TV/Foo"
    # File leaf (Phase 2 batch 3): a .mkv path round-trips intact.
    assert canonical_to_subgen_batch("TV/Foo/Season 1/ep.mkv") == "/media/TV/Foo/Season 1/ep.mkv"


def test_canonical_to_subgen_batch_root(subarr_env):
    from subarr.paths import canonical_to_subgen_batch

    assert canonical_to_subgen_batch("") == "/media/"


def test_canonical_to_subgen_batch_respects_env_override(monkeypatch, subarr_env):
    """If a future deployment changes subgen's mount, SUBGEN_MEDIA_PREFIX
    is the single knob that flips the prefix."""
    monkeypatch.setenv("SUBGEN_MEDIA_PREFIX", "/srv/media-root")
    import importlib
    from subarr import config, paths

    importlib.reload(config)
    importlib.reload(paths)
    assert paths.canonical_to_subgen_batch("TV/X") == "/srv/media-root/TV/X"


def test_canonical_to_subgen_batch_handles_unicode(subarr_env):
    """Real-world: non-ASCII path components (French, Japanese, etc) must
    survive the formatting step intact — httpx URL-encodes at request time,
    not here."""
    from subarr.paths import canonical_to_subgen_batch

    assert (
        canonical_to_subgen_batch("TV/Cette nuit-là/Season 1/ep.mkv")
        == "/media/TV/Cette nuit-là/Season 1/ep.mkv"
    )
