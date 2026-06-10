"""Path translation between the GUI's canonical form and the filesystem.

Four representations exist (see docs/design-notes.md). The GUI holds the canonical
form — relative-to-media-root, forward-slash, no leading slash — and converts at
boundaries.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from .config import settings
from .libraries import Library


VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm", ".ts"}

# Disc-image / archive containers we can't per-episode probe or transcribe.
# A multi-episode .iso resolves (via Sonarr's episodeFile) to ONE disc path
# for every episode it contains — ffprobe can't yield per-episode audio and
# subgen can't transcribe it per-episode, so such rows can never become real
# gaps. They're disqualified from coverage ("unsupported") rather than left
# stuck in "Analyzing" forever (#96/#62).
UNSUPPORTED_EXTS = {".iso", ".img", ".nrg", ".mdf", ".bin"}


class PathOutsideRootError(ValueError):
    """The requested path escapes media_root via .. or symlinks."""


def _split_canonical(canonical: str) -> tuple[str, str]:
    """Split a canonical into (slug, relative). A leading '@<slug>/' head
    yields that slug; otherwise slug is '' (the default library 0). The
    relative part is stripped of surrounding slashes.

    '@disk2/Movies/x' -> ('disk2', 'Movies/x')
    'TV/Show'         -> ('', 'TV/Show')
    '@disk2'          -> ('disk2', '')
    """
    s = (canonical or "").strip()
    if s.startswith("@"):
        head, _, rest = s[1:].partition("/")
        return head, rest.strip("/")
    return "", s.strip("/")


def _library_by_slug(slug: str) -> Library:
    """Resolve a library by slug. Unknown slug is a path-resolution error."""
    for lib in settings.libraries:
        if lib.slug == slug:
            return lib
    raise PathOutsideRootError(f"unknown library @{slug}")


def canonical_to_fs(canonical: str) -> Path:
    """Resolve a canonical to an absolute filesystem path under its library's
    root, guarding against traversal. A leading '@<slug>/' selects the
    library; no head means library 0 (media_root). Empty relative part means
    the library root itself."""
    slug, rel_str = _split_canonical(canonical)
    rel = PurePosixPath(rel_str)
    if any(part == ".." for part in rel.parts):
        raise PathOutsideRootError(canonical)
    lib = _library_by_slug(slug)
    root = lib.fs_root.resolve()
    target = (root / Path(*rel.parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise PathOutsideRootError(canonical) from e
    return target


def fs_to_canonical(p: Path) -> str:
    """Inverse of canonical_to_fs: find the owning library (longest-matching
    fs_root) and emit its canonical, prefixing '@<slug>/' for non-default
    libraries. Raises PathOutsideRootError (a ValueError subclass, preserving
    the previous raise-type contract) if p is under no library root."""
    rp = p.resolve()
    best: tuple[Path, PurePosixPath, Library] | None = None
    for lib in settings.libraries:
        root = lib.fs_root.resolve()
        try:
            rel = PurePosixPath(rp.relative_to(root).as_posix())
        except ValueError:
            continue
        if best is None or len(root.parts) > len(best[0].parts):
            best = (root, rel, lib)
    if best is None:
        raise PathOutsideRootError(str(p))
    _, rel, lib = best
    rel_posix = rel.as_posix()
    if lib.slug:
        return f"@{lib.slug}/{rel_posix}" if rel_posix != "." else f"@{lib.slug}/"
    return rel_posix


def canonical_to_subgen_batch(canonical: str) -> str:
    """Form the `directory` query value subgen's /batch expects.

    Subgen's compose mounts `/mnt/nas/Media:/media`, so files are at
    `/media/TV/...`, `/media/Movies/...`, etc. NOT `/media/library/...`
    (that's subarr's own mount). The prefix is configurable via
    SUBGEN_MEDIA_PREFIX in case a future deployment differs.

    Verified against Subgenscan.ps1 V69 line 353 (the canonical PS driver):
    `$encodedPath = "/media/$encodedFolder"` — same prefix.

    Note: the original `/media/library/` prefix was wrong since Phase 1 but
    never surfaced because every scan test mocked subgen with a transport
    that ignored the directory= value. First live end-to-end scan caught it.
    """
    slug, rel = _split_canonical(canonical)
    lib = _library_by_slug(slug)
    prefix = lib.subgen_prefix.rstrip("/")
    if rel:
        return f"{prefix}/{rel}"
    return prefix + "/"


def subgen_to_canonical(subgen_path: str) -> str:
    """Inverse of canonical_to_subgen_batch: map a subgen-space absolute
    path (e.g. `/media/TV/Show/file.mkv`) back to subarr's canonical form
    (`TV/Show/file.mkv`).

    Used by the WEBHOOK_URL_COMPLETED receiver (#87): subgen's completion
    webhook reports the source file at its OWN mount path, which is the
    `subgen_media_prefix` prefix. We strip that prefix to get the canonical
    key the provenance ledger is indexed by.

    If the path doesn't start with any configured prefix, it's returned
    stripped of leading slashes as a best-effort canonical — the caller
    will simply find no matching ledger entry, which is benign.

    #134 Phase 1: picks the library whose subgen_prefix is the longest match
    and prefixes '@<slug>/' for non-default libraries. Library 0 output is
    byte-identical to the previous single-prefix behavior.
    """
    p = (subgen_path or "").strip()
    best: Library | None = None
    best_len = -1
    for lib in settings.libraries:
        prefix = lib.subgen_prefix.rstrip("/")
        if prefix and (p == prefix or p.startswith(prefix + "/")) and len(prefix) > best_len:
            best, best_len = lib, len(prefix)
    if best is None:
        return p.strip("/")
    prefix = best.subgen_prefix.rstrip("/")
    rel = "" if p == prefix else p[len(prefix) + 1 :].strip("/")
    if best.slug:
        return f"@{best.slug}/{rel}" if rel else f"@{best.slug}/"
    return rel


def strip_arr_prefix(arr_path: str | None, prefix: str | None = None) -> str | None:
    """Strip an *arr's container-view path prefix to canonical form.

    Sonarr/Radarr report paths in THEIR container's mount namespace (e.g.
    'path': '/data/Media/TV/Foo'); stripping the configured prefix yields
    subarr's canonical 'TV/Foo'. Falsy input passes through unchanged
    (None → None, '' → '').

    #134 Phase 0: the single consolidated copy of what previously lived
    duplicated in coverage_engine, scheduler, and coverage_actions. `prefix`
    defaults to settings.arr_path_prefix; Phase 1 threads per-library /
    per-arr prefixes through this parameter (the config-level
    sonarr_path_prefix / radarr_path_prefix split from #133 is currently
    unconsumed — wiring it correctly needs arr identity at each call site,
    which is exactly what the library model adds).
    """
    if not arr_path:
        return arr_path
    if prefix is None:
        prefix = settings.arr_path_prefix
    s = arr_path
    if prefix and s.startswith(prefix):
        s = s[len(prefix) :]
    return s.strip("/")
