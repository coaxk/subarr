"""#545 follow-up: the test suite can never write into a real media library.

subarr's default media root is `/media/library`, which on Windows resolves to
`C:\\media\\library` (and on CI to `/media/library` on the runner). A test that
planted files through `settings.media_root` without the `subarr_env` fixture
wrote there: the #545 completion tests did. conftest now points
SUBARR_MEDIA_ROOT at a per-session sandbox in the temp folder, overriding any
value from the developer's shell, so a forgotten fixture lands in the sandbox.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def test_the_session_media_root_is_a_temp_sandbox():
    root = Path(os.environ["SUBARR_MEDIA_ROOT"])
    assert root.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert root.name.startswith("subarr-test-media-")
    assert root.is_dir()


def test_a_fresh_config_load_without_a_fixture_uses_the_sandbox():
    from subarr import config

    loaded = config.load().media_root
    assert loaded == Path(os.environ["SUBARR_MEDIA_ROOT"])
    assert loaded != Path("/media/library")
