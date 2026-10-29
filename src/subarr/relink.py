"""#638: propose path-rewrite rules that move stale rows onto the same files.

The orphan prune (#453) refuses when most known paths are missing. When the
cause is a layout change (a mount fixed, a library moved) the files are usually
the same files at a new path. This module finds those files and groups the
matches into rules `old_head -> new_head` that the user applies in one click.

`build_rules` is PURE: no I/O, no config. The inventory walk lives further down
and is the only part that touches the filesystem.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

RULE_MIN_VERIFIED = 3
MIN_TAIL = 3


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


def _unique_best(cands: list[tuple[int, str]]) -> tuple[int, str] | None:
    """Longest shared tail wins; a tie for the longest is None."""
    if not cands:
        return None
    top = max(n for n, _ in cands)
    best = [c for c in cands if c[0] == top]
    return best[0] if len(best) == 1 else None


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
    index: dict[tuple[str, ...], list[tuple[str, list[str]]]] = defaultdict(list)
    for c in inventory:
        if _slug_of(c) in failed_slugs:
            continue
        _prefix, segs = split_head(c)
        if len(segs) >= MIN_TAIL:
            index[tuple(segs[-MIN_TAIL:])].append((c, segs))

    # stale path -> (new path, old_head, new_head, how)
    matches: dict[str, tuple[str, str, str, str]] = {}
    mismatches: dict[tuple[str, str], int] = defaultdict(int)

    for s in sorted(set(stale)):
        if _slug_of(s) in failed_slugs:
            continue
        s_prefix, s_segs = split_head(s)
        if len(s_segs) < MIN_TAIL:
            continue
        cands = [(c, segs) for c, segs in index.get(tuple(s_segs[-MIN_TAIL:]), []) if c != s]
        if not cands:
            continue
        ranked = [(_shared_tail(s_segs, segs), c, segs) for c, segs in cands]
        size = old_sizes.get(s)
        how = "path_only"
        pool = ranked
        if isinstance(size, int):
            filtered = [r for r in ranked if inventory[r[1]] == size]
            if filtered:
                pool, how = filtered, "size"
            else:
                best = _unique_best([(n, c) for n, c, _ in ranked])
                if best is not None:
                    n, c = best
                    segs = next(sg for cc, sg in cands if cc == c)
                    key = (_join_head(s_prefix, s_segs[:-n]), _join_head(split_head(c)[0], segs[:-n]))
                    mismatches[key] += 1
                continue
        best = _unique_best([(n, c) for n, c, _ in pool])
        if best is None:
            continue
        n, c = best
        segs = next(sg for cc, sg in cands if cc == c)
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
            continue
        key = (oh, nh)
        rule = grouped.setdefault(key, Rule(old_head=oh, new_head=nh))
        collided = (new in verified) if s in verified else (new in probed)
        if collided:
            rule.collisions += 1
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
            rule.path_only = []
        rule.by_size.sort(key=lambda p: p.old)
        rule.path_only.sort(key=lambda p: p.old)
        rule.verified = sum(1 for p in rule.included if p.old in verified)
        if rule.verified >= RULE_MIN_VERIFIED:
            out.append(rule)
    out.sort(key=lambda r: (-r.verified, r.old_head, r.new_head))
    return out
