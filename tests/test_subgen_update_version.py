"""#224 follow-up: a dev/soak subarr-subgen build must not nag an update.

A locally-built image bakes a `dev-<sha>` release tag (scripts/build.sh).
That can't be ranked against the repo's date-style release tags, so the
update check must treat it as unknown (None) rather than reporting a
difference — which would falsely point the user BACKWARD to the last
release.
"""

from __future__ import annotations


def test_real_release_tag_passes_through(subarr_env):
    from subarr.app import _subgen_update_version

    assert _subgen_update_version("v2026.05.3-r9") == "v2026.05.3-r9"
    assert _subgen_update_version("v2026.05.3-r10") == "v2026.05.3-r10"


def test_dev_build_tag_resolves_to_none(subarr_env):
    from subarr.app import _subgen_update_version

    # scripts/build.sh stamps RELEASE_TAG=dev-<short-sha>
    assert _subgen_update_version("dev-5db497b") is None
    assert _subgen_update_version("dev") is None


def test_missing_tag_resolves_to_none(subarr_env):
    from subarr.app import _subgen_update_version

    assert _subgen_update_version(None) is None
    assert _subgen_update_version("") is None
