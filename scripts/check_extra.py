#!/usr/bin/env python3
"""Prove an optional extra is actually usable once installed.

WHY THIS EXISTS (2026-09-30). subarr declares four optional runtime extras --
vad, qe, qe-onnx, lid -- and CI installed NONE of them. `ci.yml` runs
`pip install -e .[dev]`, and the dev extra pulls in no self-extras and never
mentions their dependencies, so `huggingface_hub` was never imported in CI at
all and `src/subarr/qe_onnx.py`'s import path was never exercised.

That became concrete on #586, which widened `huggingface_hub` from `<2.0` to
`<3.0` while hf-hub 2.0.0 was already published. It passed 15 checks, none of
which could see the change. (It was in fact safe, because `tokenizers` caps
hf-hub below 2.0 from inside the same extra -- but safe by accident is not the
same as tested, and that cap will lift.)

⚠️ THE ASSERTION IS `is True`, NOT "it imported". Every one of these probes is
written to RETURN FALSE rather than raise, because at runtime the feature is
meant to degrade quietly -- `qe_available()` False just falls back to the
structural judge. That is correct in production and useless as a test: a broken
dependency would leave the probe returning False, the import succeeding, and the
feature silently switched off. The failure mode here is silent disablement, so
the check has to demand the positive.

Usage:
    python scripts/check_extra.py --audit      # mapping covers every declared extra
    python scripts/check_extra.py qe-onnx      # that extra's probe must say True
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# extra -> (module, probe attribute, env the probe needs to pin a backend)
#
# The probe is the project's OWN availability function in each case, not a
# bespoke import list. That is deliberate: if the real code decides the feature
# is unavailable, this must fail, and a parallel import list here would drift
# from the thing it is meant to guard.
PROBES: dict[str, tuple[str, str, dict[str, str]]] = {
    "vad": ("subarr.vad", "runtime_present", {}),
    "lid": ("subarr.lid", "runtime_present", {}),
    # qe_available() is "auto" by default and returns True if EITHER backend
    # resolves. Pinning the backend makes this test the torch path specifically,
    # rather than passing because some other extra happened to be installed.
    "qe": ("subarr.qe", "qe_available", {"SUBARR_QE_BACKEND": "torch"}),
    "qe-onnx": ("subarr.qe_onnx", "onnx_qe_available", {}),
}

# Extras that are tooling, not a shipped runtime feature.
NOT_A_RUNTIME_FEATURE = {"dev"}


def declared_extras() -> set[str]:
    """The extras pyproject actually declares."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    return set(data["project"]["optional-dependencies"]) - NOT_A_RUNTIME_FEATURE


def audit() -> int:
    """Every declared runtime extra must have a probe, and vice versa.

    This is the half that makes the gap self-closing: adding an extra to
    pyproject without adding it here fails CI, so the next optional feature
    cannot arrive untested the way these four did.
    """
    declared = declared_extras()
    mapped = set(PROBES)

    unmapped = sorted(declared - mapped)
    stale = sorted(mapped - declared)

    for name in sorted(declared & mapped):
        print(f"  ok       {name}")
    for name in unmapped:
        print(f"  MISSING  {name}: declared in pyproject, no probe here")
    for name in stale:
        print(f"  STALE    {name}: probe here, no longer declared in pyproject")

    if unmapped or stale:
        print()
        print("extras audit FAILED - the mapping and pyproject disagree.", file=sys.stderr)
        return 1
    print(f"\nextras audit ok - {len(declared)} runtime extra(s) covered.")
    return 0


def check(extra: str) -> int:
    if extra not in PROBES:
        print(f"unknown extra {extra!r}; known: {', '.join(sorted(PROBES))}", file=sys.stderr)
        return 2

    module_name, probe_name, env = PROBES[extra]
    for key, value in env.items():
        os.environ[key] = value

    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - the point is to report any failure
        print(f"FAILED: import {module_name} raised {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    probe = getattr(module, probe_name, None)
    if probe is None:
        print(f"FAILED: {module_name} has no attribute {probe_name!r}", file=sys.stderr)
        return 1

    result = probe()
    env_note = f" (with {env})" if env else ""
    print(f"  {module_name}.{probe_name}(){env_note} -> {result!r}")

    if result is not True:
        print(
            f"FAILED: extra '{extra}' installed but {probe_name}() did not report True.\n"
            "        The dependency set resolved, and the feature is still off. That is the\n"
            "        silent-disablement case this check exists for - look at what the probe\n"
            "        imports and which of those is now broken or missing.",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extra", nargs="?", help="extra to verify (e.g. qe-onnx)")
    parser.add_argument(
        "--audit",
        action="store_true",
        help="check the probe mapping matches pyproject's declared extras",
    )
    args = parser.parse_args()

    if args.audit:
        return audit()
    if not args.extra:
        parser.error("give an extra name, or --audit")
    return check(args.extra)


if __name__ == "__main__":
    sys.exit(main())
