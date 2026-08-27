"""#453: decide whether it is SAFE to drop DB rows for files that vanished.

A rename leaves the old canonical path wedged in Review forever -- nothing in
the coverage path ever checks whether a file still exists, so re-walking does
not clear it. Users have resorted to hand-written SQL to unstick it.

The obvious fix is to delete every row whose path is missing. That is a
data-loss landmine on a network share: when the mount drops, the mountpoint
frequently still LISTS its directories while serving nothing, so every path
looks missing in the same instant. A naive prune then deletes every audio-lang
verification the user has ever confirmed, and re-confirming them is manual work
measured in hours.

So the rule is: a handful of missing files is renames, and safe to prune. A
large fraction missing is an infrastructure fault, and must be refused loudly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

# Above this share of missing paths we assume the storage is unavailable rather
# than believe the user renamed most of their library in one sitting.
MAX_MISSING_RATIO = 0.5

# Below this many rows the ratio is meaningless -- 1 of 2 missing is 50% and
# tells us nothing. Refuse rather than act on noise.
MIN_TOTAL_TO_JUDGE = 1


@dataclass(frozen=True)
class PruneDecision:
    safe: bool
    would_delete: int
    reason: str


def partition_missing(paths: Iterable[str], *, exists: Callable[[str], bool]) -> tuple[list[str], list[str]]:
    """Split paths into (present, missing), preserving order.

    ``exists`` is injected so this stays pure and testable -- callers pass
    ``os.path.exists`` or equivalent.
    """
    present: list[str] = []
    missing: list[str] = []
    for p in paths:
        (present if exists(p) else missing).append(p)
    return present, missing


def prune_decision(
    *, total: int, missing: int, max_missing_ratio: float = MAX_MISSING_RATIO
) -> PruneDecision:
    """Is it safe to delete rows for ``missing`` of ``total`` paths?"""
    if total < MIN_TOTAL_TO_JUDGE:
        return PruneDecision(False, 0, "nothing to judge: the store is empty")
    if missing == 0:
        return PruneDecision(True, 0, "no missing paths: nothing to prune")
    ratio = missing / total
    if ratio > max_missing_ratio:
        return PruneDecision(
            False,
            missing,
            f"refusing to prune: {missing} of {total} paths are missing "
            f"({ratio:.0%}). That is a storage mount failure far more often "
            f"than it is renames, and pruning here would delete verifications "
            f"that are expensive to rebuild by hand. Check the media mount, "
            f"then re-run.",
        )
    return PruneDecision(
        True,
        missing,
        f"{missing} of {total} paths are missing ({ratio:.0%}) -- consistent "
        f"with renames or deletions; safe to prune.",
    )


@dataclass(frozen=True)
class PruneReport:
    decision: PruneDecision
    deleted: int
    missing: list[str]


def prune_missing(
    store,
    *,
    exists: Callable[[str], bool],
    dry_run: bool = False,
    max_missing_ratio: float = MAX_MISSING_RATIO,
) -> PruneReport:
    """Drop rows for paths that no longer exist, if that looks safe.

    ``store`` needs ``all_paths()`` and ``delete(path)`` -- both probe_store and
    audio_lang_store already satisfy that shape.

    Refuses as a whole rather than partially: if the missing share says the
    storage is down, nothing is deleted at all. A partial prune under a mount
    failure would be the worst outcome, since it silently destroys some rows
    while looking like it worked.
    """
    paths = list(store.all_paths())
    _present, missing = partition_missing(paths, exists=exists)
    decision = prune_decision(total=len(paths), missing=len(missing), max_missing_ratio=max_missing_ratio)
    if not decision.safe or dry_run:
        return PruneReport(decision, 0, missing)
    deleted = sum(1 for p in missing if store.delete(p))
    return PruneReport(decision, deleted, missing)
