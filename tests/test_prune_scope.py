"""#638: the (library, top folder) scope the cache-only prune judges by."""

from __future__ import annotations

import pytest

from subarr.paths import prune_scope


@pytest.mark.parametrize(
    "canonical,scope",
    [
        ("@anime/Movies/A/a.mkv", ("anime", "Movies")),
        ("TV/Show/e.mkv", ("", "TV")),
        ("@x/a.mkv", ("x", "")),
        ("@x", ("x", "")),
        ("", ("", "")),
    ],
)
def test_prune_scope(canonical, scope):
    assert prune_scope(canonical) == scope
