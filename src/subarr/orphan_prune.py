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

Rows fall into two classes, judged independently. Protected rows live in any
store not named as regenerable -- in the app the audio-language verifications --
and keep the ratio test. Cache-only rows live only in regenerable stores -- the
probe cache -- and have no ratio: a missing one is prunable when its (library,
top folder) scope shows enough readable files to prove the share is mounted,
else it is held.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

# Above this share of missing paths we assume the storage is unavailable rather
# than believe the user renamed most of their library in one sitting.
MAX_MISSING_RATIO = 0.5

# Below this many rows the ratio is meaningless -- 1 of 2 missing is 50% and
# tells us nothing. Refuse rather than act on noise.
MIN_TOTAL_TO_JUDGE = 1

# Readable files a scope needs before the share is believed mounted. One cached
# dentry must not be enough, hence 3.
MIN_LIVE_WITNESSES = 3

# A path we cannot resolve is "unknown", never "present": it proves nothing was
# read. Only "present" is a witness; "unknown" is never missing and never held.
PathState = Literal["present", "missing", "unknown"]


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

    ``store`` needs ``all_paths()`` and ``delete(path)``. probe_store and
    audio_lang_store both satisfy that shape (audio_lang_store's
    ``all_paths`` was added for #453; it had only ``delete`` before).

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


@dataclass(frozen=True)
class HeldScope:
    library: str
    folder: str
    missing: int
    present: int


@dataclass(frozen=True)
class MultiPruneReport:
    decision: PruneDecision
    total_paths: int
    missing: list[str]
    deleted_by_store: dict[str, int]
    held: list[str] = field(default_factory=list)
    held_protected: int = 0
    held_scopes: list[HeldScope] = field(default_factory=list)


def _scope_name(scope: HeldScope) -> str:
    if scope.library and scope.folder:
        return f"@{scope.library}/{scope.folder}"
    if scope.library:
        return f"@{scope.library}"
    return scope.folder or "the default library"


def _multi_reason(
    *,
    prunable: list[str],
    total: int,
    held_protected: int,
    protected_decision: PruneDecision | None,
    held_scopes: list[HeldScope],
) -> str:
    parts: list[str] = []
    if prunable:
        n = len(prunable)
        parts.append(
            f"{n} of {total} paths are missing ({n / total:.0%}) -- consistent "
            f"with renames or deletions; safe to prune."
        )
    if held_protected > 0 and protected_decision is not None:
        parts.append(protected_decision.reason)
    held_cache = sum(scope.missing for scope in held_scopes)
    if held_cache > 0:
        names = [_scope_name(scope) for scope in held_scopes]
        places = ", ".join(names[:5])
        if len(names) > 5:
            places += f" and {len(names) - 5} more"
        parts.append(
            f"{held_cache} cached probe row{'s' if held_cache != 1 else ''} "
            f"{'are' if held_cache != 1 else 'is'} on hold: too few files could be read under "
            f"{places} to show the share is mounted. They are only a cache and will be "
            f"cleared once those folders are readable again."
        )
    return " ".join(part for part in parts if part)


def prune_missing_multi(
    stores: dict,
    *,
    exists: Callable[[str], bool] | None = None,
    state: Callable[[str], PathState] | None = None,
    dry_run: bool = False,
    max_missing_ratio: float = MAX_MISSING_RATIO,
    regenerable: frozenset = frozenset(),
    scope_of: Callable[[str], tuple[str, str]] | None = None,
    folder_gone: Callable[[str, str], bool] | None = None,
) -> MultiPruneReport:
    """Sweep several stores, judging protected and cache-only rows independently.

    Protected rows are paths in any store not named in ``regenerable`` -- in the
    app the hand-built audio-language verifications. They keep the ratio test:
    if more than ``max_missing_ratio`` of the distinct protected paths is
    missing, every missing protected path is held.

    Cache-only rows exist only in ``regenerable`` stores -- the probe cache,
    which a rescan can rebuild. They have no ratio: a missing cache-only path is
    prunable when its scope is live, else held. A scope is live when it shows at
    least ``MIN_LIVE_WITNESSES`` readable paths, or when its folder is gone and
    its library still shows that many.

    Each class has its own storage-failure test, so a held protected set does
    not hold cache rows, and vice versa. ``state`` is called once per distinct
    path, and ``folder_gone`` at most once per scope.

    Paths are de-duplicated across stores before either test: both stores key on
    canonical_path and overlap heavily.
    """
    if exists is None and state is None:
        raise TypeError("prune_missing_multi needs exists= or state=")
    if exists is not None and state is not None:
        raise ValueError("pass exists= or state=, not both")
    if state is None:
        exists_fn = exists

        def _state(path: str) -> PathState:
            return "present" if exists_fn(path) else "missing"

        state_fn: Callable[[str], PathState] = _state
    else:
        state_fn = state

    scope_of = scope_of or (lambda path: ("", ""))
    folder_gone = folder_gone or (lambda library, folder: False)

    seen: list[str] = []
    seen_set: set[str] = set()
    protected: set[str] = set()
    for name, store in stores.items():
        for path in store.all_paths():
            if path not in seen_set:
                seen_set.add(path)
                seen.append(path)
            if name not in regenerable:
                protected.add(path)

    states: dict[str, PathState] = {path: state_fn(path) for path in seen}
    scopes = {path: scope_of(path) for path in seen}

    prot_missing = [path for path in seen if path in protected and states[path] == "missing"]
    protected_decision: PruneDecision | None = None
    held_protected_paths: set[str] = set()
    held_protected = 0
    if protected:
        protected_decision = prune_decision(
            total=len(protected), missing=len(prot_missing), max_missing_ratio=max_missing_ratio
        )
        if not protected_decision.safe:
            held_protected_paths = set(prot_missing)
            held_protected = len(prot_missing)

    present_by_scope: dict[tuple[str, str], int] = {}
    present_by_library: dict[str, int] = {}
    for path in seen:
        if states[path] != "present":
            continue
        library, folder = scopes[path]
        present_by_scope[(library, folder)] = present_by_scope.get((library, folder), 0) + 1
        present_by_library[library] = present_by_library.get(library, 0) + 1

    held_cache: dict[tuple[str, str], list[str]] = {}
    held_cache_paths: set[str] = set()
    folder_gone_cache: dict[tuple[str, str], bool] = {}
    for path in seen:
        if path in protected or states[path] != "missing":
            continue
        scope = scopes[path]
        if present_by_scope.get(scope, 0) >= MIN_LIVE_WITNESSES:
            continue
        library, folder = scope
        live = False
        if folder != "" and present_by_library.get(library, 0) >= MIN_LIVE_WITNESSES:
            if scope not in folder_gone_cache:
                folder_gone_cache[scope] = folder_gone(library, folder)
            live = folder_gone_cache[scope]
        if not live:
            held_cache.setdefault(scope, []).append(path)
            held_cache_paths.add(path)

    held_scopes = [
        HeldScope(
            library=library,
            folder=folder,
            missing=len(paths),
            present=present_by_scope.get((library, folder), 0),
        )
        for (library, folder), paths in held_cache.items()
    ]
    held_scopes.sort(key=lambda scope: (-scope.missing, scope.library, scope.folder))

    prunable = [
        path
        for path in seen
        if states[path] == "missing" and path not in held_protected_paths and path not in held_cache_paths
    ]
    held = [path for path in seen if path in held_protected_paths or path in held_cache_paths]

    if not seen:
        decision = prune_decision(total=0, missing=0, max_missing_ratio=max_missing_ratio)
        missing: list[str] = []
    elif not prunable and not held:
        decision = prune_decision(total=len(seen), missing=0, max_missing_ratio=max_missing_ratio)
        missing = []
    elif not prunable:
        decision = PruneDecision(
            False,
            len(held),
            _multi_reason(
                prunable=prunable,
                total=len(seen),
                held_protected=held_protected,
                protected_decision=protected_decision,
                held_scopes=held_scopes,
            ),
        )
        missing = held
    else:
        decision = PruneDecision(
            True,
            len(prunable),
            _multi_reason(
                prunable=prunable,
                total=len(seen),
                held_protected=held_protected,
                protected_decision=protected_decision,
                held_scopes=held_scopes,
            ),
        )
        missing = prunable

    deleted_by_store: dict[str, int] = {}
    if decision.safe and not dry_run:
        for name, store in stores.items():
            deleted_by_store[name] = sum(1 for path in prunable if store.delete(path))
    return MultiPruneReport(decision, len(seen), missing, deleted_by_store, held, held_protected, held_scopes)
