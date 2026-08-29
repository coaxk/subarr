"""#469: clear pending rows whose English coverage is already satisfied.

Companion to #468. That stops NEW rows getting wedged; this clears the ones
already there. The reporter on #453 had 110 of them, each holding a feeder
depth slot for 30s as it drained.

Deliberately conservative in one direction. The cost of keeping a row that did
not need keeping is one wasted submission that #468 now resolves in
milliseconds. The cost of dropping a row wrongly is silently deleting work the
user asked for, with nothing to show they ever asked. So every judgement call
here leans toward KEEPING.

That is also why there is no ratio guard like ``orphan_prune``'s. There the
danger is a dropped mount making everything look missing at once; here a
dropped mount makes sidecars look ABSENT, which reads as "not satisfied", which
keeps rows. The failure mode already falls the safe way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .pending_queue import STATUS_SUBMITTED

# Only text subtitles count. A bitmap track (#458) cannot be searched,
# restyled, retimed or read, so an `.idx`/`.sub`/`.pgs` sibling is not coverage
# and must never satisfy a pending row -- those files are precisely the ones
# bypass_skip exists to transcribe.
_TEXT_SUBTITLE_EXT = ".srt"

# A forced sidecar covers foreign dialogue inside an otherwise-English film, not
# the whole show. Treating it as coverage would resolve a pending row for a real
# gap: the #79 defect class, one layer up.
_FORCED_TOKENS = frozenset({"forced"})

_ENGLISH_TOKENS = frozenset({"en", "eng"})


def is_full_english_sidecar(filename: str, stem: str) -> bool:
    """True only for a FULL English text sidecar belonging to ``stem``.

    Full means not forced. Text means .srt: deliberately narrower than every
    text format that exists, because a miss here keeps a row (cheap) while a
    false match deletes one (not).
    """
    name = filename.rsplit("/", 1)[-1]
    if not name.lower().endswith(_TEXT_SUBTITLE_EXT):
        return False
    base = name[: -len(_TEXT_SUBTITLE_EXT)]
    if not base.startswith(stem):
        return False
    tail = base[len(stem) :]
    if not tail.startswith("."):
        return False
    parts = [p.lower() for p in tail.split(".") if p]
    if any(p in _FORCED_TOKENS for p in parts):
        return False
    return any(p in _ENGLISH_TOKENS for p in parts)


@dataclass
class ReconcileReport:
    satisfied: list[str] = field(default_factory=list)
    resolved: int = 0
    skipped_bypass: int = 0
    examined: int = 0


def _stem_of(canonical_path: str) -> str:
    name = canonical_path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def reconcile_pending(
    store,
    *,
    list_siblings: Callable[[str], list[str]],
    dry_run: bool = False,
) -> ReconcileReport:
    """Resolve SUBMITTED rows that already have a full English text sidecar.

    ``list_siblings`` is injected so this stays pure and testable; callers pass
    a real directory listing. Any error from it is treated as "cannot tell",
    which keeps the row.

    SUBMITTED only. A PENDING row has not reached subgen, so nothing has been
    learned about it, and dropping it would discard work still queued.

    A ``bypass_skip`` row is never resolved. It was submitted on purpose against
    subgen's judgement, for a file whose only English subtitle is an image
    track -- such a file can even carry an `.en.idx` sibling. Resolving it would
    delete exactly the work the user explicitly asked for.
    """
    rep = ReconcileReport()
    for job in store.list(status=STATUS_SUBMITTED):
        rep.examined += 1
        if getattr(job, "bypass_skip", False):
            rep.skipped_bypass += 1
            continue
        try:
            siblings = list_siblings(job.canonical_path)
        except Exception:  # noqa: BLE001 -- cannot tell => keep the row
            continue
        stem = _stem_of(job.canonical_path)
        if any(is_full_english_sidecar(n, stem) for n in siblings or []):
            rep.satisfied.append(job.canonical_path)
    if not dry_run:
        for path in rep.satisfied:
            rep.resolved += store.resolve_skipped(path)
    return rep
