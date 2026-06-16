"""#216: audit EXISTING external subtitles with the aftercare scorer.

Today the aftercare scorer (#156) only fires on subgen completions via the
CompletionWatcher. This runner points the SAME scorer at the external sidecar
SRTs a user already has (Bazarr/scene/provider subs), so subarr can surface
bad pre-existing subs — sync drift, scene-release ad boilerplate, suspected
machine translation — and offer regenerate-from-audio. It turns subarr from
"fill the gaps" into "audit everything you have".

The runner is deliberately dependency-injected (read/probe/evaluate/record are
passed in) so the orchestration is unit-testable without a filesystem or DB;
the router wires the real implementations.

Boundaries (issue #216): we never fix sync in place (that's subsyncarr) — the
answer is regenerate-from-audio — and we never touch Bazarr's provider logic.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .aftercare import AftercareEvaluation

log = logging.getLogger(__name__)

# Source tag stored on aftercare_results rows from this runner, so the review
# UI's latest-per-path + filtering work unchanged and these never mix with
# completion-watcher rows.
EXISTING_AUDIT_SOURCE = "existing_audit"

# subarr-subgen writes its sidecars with this infix (e.g. Show.subgen.en.srt).
# A pre-existing external sub never carries it — a cheap first-pass skip.
_SUBGEN_MARKER = ".subgen."


def is_subarr_generated(srt_path: str, generated_paths: set[str]) -> bool:
    """True if this SRT is subarr's own output rather than a pre-existing
    external sub. Two signals: the subgen filename marker, or an exact match
    in the provenance set (paths subarr has transcribed)."""
    if _SUBGEN_MARKER in srt_path:
        return True
    return srt_path in generated_paths


def discover_external_srts(roots: Iterable[Path], *, max_depth: int | None = None) -> list[str]:
    """Walk each library root for .srt sidecars. Skips symlinks (avoid escaping
    the root / loops) and unreadable subtrees. Returns sorted absolute paths.

    This is the only filesystem walk the audit does — the same rglob the
    sidecar scanner uses — so the runner downstream stays pure/injectable.
    """
    found: set[str] = set()
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        root_depth = len(root.parts)
        for srt in root.rglob("*.srt"):
            try:
                if srt.is_symlink():
                    continue
                if max_depth is not None and (len(srt.parts) - root_depth) > max_depth:
                    continue
                found.add(str(srt))
            except OSError as e:  # unreadable entry — skip, don't abort the walk
                log.debug("existing-audit discover: skipping %s (%s)", srt, e)
    return sorted(found)


@dataclass
class AuditSummary:
    total: int = 0  # paths considered
    scanned: int = 0  # external subs actually scored
    skipped: int = 0  # subarr-generated, skipped
    flagged: int = 0  # scored AND flagged by aftercare
    errors: int = 0  # unreadable / failed mid-file


async def run_existing_audit(
    srt_paths: Iterable[str],
    *,
    generated_paths: set[str],
    read_text: Callable[[str], str],
    probe_duration: Callable[[str], Awaitable[float | None]],
    evaluate: Callable[[str, float | None], AftercareEvaluation],
    record: Callable[..., None],
    now: float,
    on_progress: Callable[[int, int], None] | None = None,
) -> AuditSummary:
    """Score each external sidecar SRT and store the result.

    For each path: skip subarr's own output; otherwise read it, fetch the
    media duration (enables the sync-overrun check), score it, and persist
    under EXISTING_AUDIT_SOURCE. A single bad file is counted and skipped, not
    fatal — a library walk must finish. `on_progress(done, total)` fires once
    per path (skips included) so a long walk can report live.
    """
    paths = list(srt_paths)
    summary = AuditSummary(total=len(paths))

    for i, srt_path in enumerate(paths, start=1):
        try:
            if is_subarr_generated(srt_path, generated_paths):
                summary.skipped += 1
                continue
            text = read_text(srt_path)
            duration = await probe_duration(srt_path)
            evaluation = evaluate(text, duration)
            record(
                canonical_path=srt_path,
                completed_at=now,
                evaluation=evaluation,
                source=EXISTING_AUDIT_SOURCE,
            )
            summary.scanned += 1
            if evaluation.flagged:
                summary.flagged += 1
        except Exception as e:  # noqa: BLE001 - one bad sub must not abort the walk
            summary.errors += 1
            log.warning("existing-audit: skipping %s (%s)", srt_path, e)
        finally:
            if on_progress is not None:
                on_progress(i, len(paths))

    return summary
