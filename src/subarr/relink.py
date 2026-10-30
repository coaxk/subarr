"""#638: propose path-rewrite rules that move stale rows onto the same files.

The orphan prune (#453) refuses when most known paths are missing. When the
cause is a layout change (a mount fixed, a library moved) the files are usually
the same files at a new path. This module finds those files and groups the
matches into rules `old_head -> new_head` that the user applies in one click.

`build_rules` is PURE: no I/O, no config. The inventory walk lives further down
and is the only part that touches the filesystem.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .log_safe import scrub

RULE_MIN_VERIFIED = 3
MIN_TAIL = 3  # the shortest shared tail that may match on path alone
MIN_TAIL_SIZED = 2  # ...and when the recorded size equals the current file size


def split_head(canonical: str) -> tuple[str, list[str]]:
    """('@slug' or '', segments without the slug). Empty segments are dropped."""
    s = (canonical or "").strip()
    prefix = ""
    if s.startswith("@"):
        head, _, rest = s.partition("/")
        prefix, s = head, rest
    return prefix, [p for p in s.split("/") if p]


def _join_head(prefix: str, segs: list[str]) -> str:
    body = "/".join(segs)
    if prefix and body:
        return f"{prefix}/{body}"
    return prefix or body


@dataclass(frozen=True)
class Pair:
    old: str
    new: str
    how: Literal["size", "path_only"]


@dataclass
class Rule:
    old_head: str
    new_head: str
    by_size: list[Pair] = field(default_factory=list)
    path_only: list[Pair] = field(default_factory=list)
    path_only_withheld: int = 0
    mismatches: int = 0
    collisions: int = 0
    verified: int = 0

    @property
    def included(self) -> list[Pair]:
        return sorted([*self.by_size, *self.path_only], key=lambda p: p.old)


def _slug_of(canonical: str) -> str:
    return split_head(canonical)[0].lstrip("@")


def _shared_tail(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(reversed(a), reversed(b), strict=False):
        if x != y:
            break
        n += 1
    return n


def _unique_best(cands: list[tuple]) -> tuple | None:
    """Longest shared tail (first tuple element) wins; a tie for the longest is None."""
    if not cands:
        return None
    top = max(c[0] for c in cands)
    best = [c for c in cands if c[0] == top]
    return best[0] if len(best) == 1 else None


# Every stale path is counted under exactly one of these, the first that applies.
REASONS = (
    "failed_library",
    "too_short",
    "no_candidate",
    "needs_size",
    "size_changed",
    "ambiguous",
    "contested",
    "collision",
    "path_only_withheld",
    "below_threshold",
    "offered",
)
EXAMPLES_PER_REASON = 3


@dataclass
class Analysis:
    rules: list[Rule]
    reasons: dict[str, int]  # every key in REASONS is always present
    examples: dict[str, list[str]]  # up to 3 sorted stale paths per reason, non-zero reasons only


def build_rules(
    stale: Iterable[str],
    *,
    old_sizes: dict[str, int | None],
    verified: set[str],
    probed: set[str],
    inventory: dict[str, int],
    failed_slugs: set[str],
) -> list[Rule]:
    """Offered rules only, sorted by verified count (desc) then heads."""
    return analyse(
        stale,
        old_sizes=old_sizes,
        verified=verified,
        probed=probed,
        inventory=inventory,
        failed_slugs=failed_slugs,
    ).rules


def analyse(
    stale: Iterable[str],
    *,
    old_sizes: dict[str, int | None],
    verified: set[str],
    probed: set[str],
    inventory: dict[str, int],
    failed_slugs: set[str],
) -> Analysis:
    """Offered rules (sorted by verified count desc, then heads) plus the reason
    each distinct stale path is, or is not, in one of them.

    A tail of MIN_TAIL segments may match on path alone; a tail of only
    MIN_TAIL_SIZED may match when the stale row's recorded size equals the
    current file's. A different-size 2-segment file is not evidence of the same
    file, so it never counts as a mismatch against a rule."""
    index: dict[tuple[str, ...], list[tuple[str, list[str]]]] = defaultdict(list)
    for c in inventory:
        if _slug_of(c) in failed_slugs:
            continue
        _prefix, segs = split_head(c)
        if len(segs) >= MIN_TAIL_SIZED:
            index[tuple(segs[-MIN_TAIL_SIZED:])].append((c, segs))

    reason_of: dict[str, str] = {}
    # stale path -> (new path, old_head, new_head, how)
    matches: dict[str, tuple[str, str, str, str]] = {}
    mismatches: dict[tuple[str, str], int] = defaultdict(int)

    for s in sorted(set(stale)):
        if _slug_of(s) in failed_slugs:
            reason_of[s] = "failed_library"
            continue
        s_prefix, s_segs = split_head(s)
        if len(s_segs) < MIN_TAIL_SIZED:
            reason_of[s] = "too_short"
            continue
        cands = [(c, segs) for c, segs in index.get(tuple(s_segs[-MIN_TAIL_SIZED:]), []) if c != s]
        if not cands:
            reason_of[s] = "no_candidate"
            continue
        size = old_sizes.get(s)
        has_third = len(s_segs) >= MIN_TAIL
        third = s_segs[-MIN_TAIL] if has_third else None
        if isinstance(size, int):
            pool = [(_shared_tail(s_segs, segs), c, segs) for c, segs in cands if inventory[c] == size]
            how = "size"
            if not pool:
                reason_of[s] = "size_changed"
                # Only a tail of MIN_TAIL+ is evidence the wrong-size file is the same file.
                if has_third:
                    long = [
                        (_shared_tail(s_segs, segs), c, segs)
                        for c, segs in cands
                        if len(segs) >= MIN_TAIL and segs[-MIN_TAIL] == third
                    ]
                    best = _unique_best(long)
                    if best is not None:
                        n, c, segs = best
                        key = (_join_head(s_prefix, s_segs[:-n]), _join_head(split_head(c)[0], segs[:-n]))
                        mismatches[key] += 1
                continue
        else:
            if not has_third:
                reason_of[s] = "needs_size"
                continue
            pool = [
                (_shared_tail(s_segs, segs), c, segs)
                for c, segs in cands
                if len(segs) >= MIN_TAIL and segs[-MIN_TAIL] == third
            ]
            how = "path_only"
            if not pool:
                reason_of[s] = "needs_size"
                continue
        best = _unique_best(pool)
        if best is None:
            reason_of[s] = "ambiguous"
            continue
        n, c, segs = best
        matches[s] = (
            c,
            _join_head(s_prefix, s_segs[:-n]),
            _join_head(split_head(c)[0], segs[:-n]),
            how,
        )

    # A current file claimed by more than one stale path is claimed by none.
    claims: dict[str, int] = defaultdict(int)
    for new, *_ in matches.values():
        claims[new] += 1

    grouped: dict[tuple[str, str], Rule] = {}
    for s, (new, oh, nh, how) in matches.items():
        if claims[new] > 1:
            reason_of[s] = "contested"
            continue
        key = (oh, nh)
        rule = grouped.setdefault(key, Rule(old_head=oh, new_head=nh))
        collided = (new in verified) if s in verified else (new in probed)
        if collided:
            rule.collisions += 1
            reason_of[s] = "collision"
            continue
        pair = Pair(old=s, new=new, how=how)  # type: ignore[arg-type]
        if how == "size":
            rule.by_size.append(pair)
        else:
            rule.path_only.append(pair)

    out: list[Rule] = []
    for key, rule in grouped.items():
        rule.mismatches = mismatches.get(key, 0)
        if rule.path_only and (not rule.by_size or rule.mismatches):
            rule.path_only_withheld = len(rule.path_only)
            for p in rule.path_only:
                reason_of[p.old] = "path_only_withheld"
            rule.path_only = []
        rule.by_size.sort(key=lambda p: p.old)
        rule.path_only.sort(key=lambda p: p.old)
        rule.verified = sum(1 for p in rule.included if p.old in verified)
        is_offered = rule.verified >= RULE_MIN_VERIFIED
        for p in rule.included:
            reason_of[p.old] = "offered" if is_offered else "below_threshold"
        if is_offered:
            out.append(rule)
    out.sort(key=lambda r: (-r.verified, r.old_head, r.new_head))

    reasons = dict.fromkeys(REASONS, 0)
    by_reason: dict[str, list[str]] = defaultdict(list)
    for path, reason in reason_of.items():
        reasons[reason] += 1
        by_reason[reason].append(path)
    examples = {r: sorted(by_reason[r])[:EXAMPLES_PER_REASON] for r in REASONS if reasons[r]}
    return Analysis(rules=out, reasons=reasons, examples=examples)


# ─── Inventory of current files ─────────────────────────────────────

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Inventory:
    files: dict[str, int]
    failed_slugs: frozenset[str]
    walk_s: float = 0.0
    walked_at: float = 0.0  # _clock() when the walk finished; the #641 TTL runs from here


def walk_inventory() -> Inventory:
    """Walk every library root once and record canonical path -> size.

    A library whose root is not a readable directory, or whose walk reports ANY
    error part-way, is marked failed and contributes NO files: a partial
    listing that looks complete would let a rule map rows onto the wrong file.
    Cost: one directory listing per directory and one os.stat per video file.
    """
    from .config import settings
    from .paths import VIDEO_EXTS, fs_to_canonical

    started = time.monotonic()
    files: dict[str, int] = {}
    failed: set[str] = set()
    for lib in settings.libraries:
        root = lib.fs_root
        if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
            failed.add(lib.slug)
            continue
        errors: list[OSError] = []
        found: dict[str, int] = {}
        for dirpath, _dirs, names in os.walk(root, onerror=errors.append):
            for name in names:
                p = os.path.join(dirpath, name)
                if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
                    continue
                try:
                    size = os.stat(p).st_size
                    found[fs_to_canonical(Path(p))] = size
                except (OSError, ValueError):
                    continue
        if errors:
            failed.add(lib.slug)
            _log.warning("relink inventory: library %r walk failed (%s)", lib.slug, scrub(str(errors[0])))
            continue
        files.update(found)
    walk_s = time.monotonic() - started
    _log.info("relink inventory: %d files, %d failed libraries, %.1fs", len(files), len(failed), walk_s)
    return Inventory(files=files, failed_slugs=frozenset(failed), walk_s=walk_s, walked_at=_clock())


_clock = time.monotonic

_cache_lock = threading.Lock()
_cache: tuple[tuple, float, Inventory] | None = None


def _library_key() -> tuple:
    from .config import settings

    return tuple((lib.slug, str(lib.fs_root)) for lib in settings.libraries)


def get_inventory(*, max_age_s: float = 300.0) -> Inventory:
    """Cached walk. The lock is held ACROSS the walk so concurrent callers wait
    for one walk instead of each starting their own."""
    global _cache
    key = _library_key()
    with _cache_lock:
        if _cache is not None and _cache[0] == key and _clock() - _cache[1] < max_age_s:
            return _cache[2]
        inv = walk_inventory()
        _cache = (key, _clock(), inv)
        return inv


# ─── #641: remembered "no rules" outcome ────────────────────────────
#
# Stale rows that are ordinary renames (an upgrade in place) never form a rule,
# yet each one keeps the stale count above RULE_MIN_VERIFIED, so every Review
# load walked the library (29.8 s for 5,956 files, 0 rules, on the
# maintainer's install). A no-rules answer can only change when its inputs do:
# the fingerprint covers the stale set and each stale path's old size, plus the
# WHOLE verdict and probe sets (collisions read them at the new paths); the
# library key covers the roots. The TTL, counted from the walk, bounds the one
# input left: the inventory itself. Files appearing at a new path, or a
# duplicate copy disappearing (which breaks a tie in `_unique_best`), can each
# turn "no rules" into a rule with every key input unchanged. A head-exists
# pre-filter was rejected: a library split moves `TV/Show/S1/e.mkv` to
# `@anime/Show/S1/e.mkv` while `TV` still exists, which is exactly a relink.

NO_RULES_TTL_S = 6 * 3600.0

_no_rules_lock = threading.Lock()
_no_rules: tuple[tuple, str, float, Inventory, Analysis | None] | None = None


def analysis_fingerprint(
    stale: Iterable[str], *, verified: set[str], probed: set[str], old_sizes: dict[str, int | None]
) -> str:
    """Stable digest of every `build_rules` input except the inventory."""
    h = hashlib.sha256()
    for section in (
        (f"{p}\x1f{old_sizes.get(p)}" for p in sorted(set(stale))),
        sorted(verified),
        sorted(probed),
    ):
        for item in section:
            h.update(f"{item}\x1e".encode())
        h.update(b"\x1d")
    return h.hexdigest()


def _no_rules_hit(fingerprint: str, max_age_s: float):
    key = _library_key()
    with _no_rules_lock:
        hit = _no_rules
    if hit is None or hit[0] != key or hit[1] != fingerprint or _clock() - hit[2] >= max_age_s:
        return None
    return hit


def cached_no_rules(fingerprint: str, *, max_age_s: float = NO_RULES_TTL_S) -> Inventory | None:
    """The inventory of the walk that found no rules for these inputs, or None."""
    hit = _no_rules_hit(fingerprint, max_age_s)
    return None if hit is None else hit[3]


def cached_no_rules_analysis(
    fingerprint: str, *, max_age_s: float = NO_RULES_TTL_S
) -> tuple[Inventory, Analysis | None] | None:
    """The inventory AND breakdown from one remembered entry, so the two can
    never come from different remembers. The breakdown is None when the entry
    was stored without one."""
    hit = _no_rules_hit(fingerprint, max_age_s)
    return None if hit is None else (hit[3], hit[4])


def remember_no_rules(fingerprint: str, inv: Inventory, analysis: Analysis | None = None) -> None:
    """A walk with an unreadable library is never remembered: rows may map onto
    that library once it reads again, and that does not change the stale set."""
    global _no_rules
    if inv.failed_slugs:
        return
    with _no_rules_lock:
        _no_rules = (_library_key(), fingerprint, inv.walked_at, inv, analysis)


def invalidate_inventory() -> None:
    global _cache, _no_rules
    with _cache_lock:
        _cache = None
    with _no_rules_lock:
        _no_rules = None
