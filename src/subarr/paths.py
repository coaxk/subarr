"""Path translation between the GUI's canonical form and the filesystem.

Four representations exist (see docs/design-notes.md). The GUI holds the canonical
form — relative-to-media-root, forward-slash, no leading slash — and converts at
boundaries.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

from .config import settings
from .libraries import Library

log = logging.getLogger(__name__)


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


def library_for_canonical(canonical: str) -> Library:
    """Public, fail-soft resolver: a canonical's '@<slug>/' head -> its Library.
    Unknown/empty slug returns library 0 (the default) rather than raising —
    callers (clients_for, #161) must degrade to instance 0, never crash a row.
    """
    slug, _rel = _split_canonical(canonical)
    try:
        return _library_by_slug(slug)
    except PathOutsideRootError:
        return _library_by_slug("")


def library_label(canonical: str) -> dict[str, str]:
    """#378: the provenance label {slug, name} for a row's library, resolved from
    its canonical path. Fail-soft to library 0 (library_for_canonical never
    raises). A default-library row gets slug "" — the UI renders no chip for it,
    so single-library installs stay visually identical. Shared by the coverage
    rows and the Queue/Review/Aftercare/Activity surfaces."""
    lib = library_for_canonical(canonical or "")
    return {"slug": lib.slug, "name": lib.name}


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
    # Fix B (#285): when the path IS the default library root, rel_posix is "."
    # which is not a valid canonical. Return "" (empty = library root).
    return "" if rel_posix == "." else rel_posix


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
        if p:
            # #285 NIT: nothing matched a configured subgen_prefix. We still
            # return a best-effort canonical, but a completion webhook landing
            # here silently misses its provenance-ledger entry — surface it so a
            # misconfigured subgen_media_prefix (esp. multi-library #161) is
            # diagnosable rather than a silent no-op.
            log.warning(
                "subgen_to_canonical: %r matched no configured subgen_prefix; "
                "completion ledger lookup will miss",
                subgen_path,
            )
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

    Two modes:
    - Explicit ``prefix=`` (Phase 0 seam): strip exactly that prefix, emit a
      library-0-namespace canonical (no '@head'). Back-compat / tests.
    - Library-aware (``prefix is None``): pick the library whose ``arr_prefix``
      is the LONGEST match for ``arr_path``, strip it, and prefix '@<slug>/'
      for non-default libraries. With a single library this is byte-identical
      to the old single-prefix strip. A path matching no library passes
      through slash-stripped (unchanged from before).
    """
    if not arr_path:
        return arr_path

    if prefix is not None:
        # Fix A (#285): normalize backslashes so Windows arr paths match
        # forward-slash prefixes.
        s = arr_path.replace("\\", "/")
        p = (prefix or "").replace("\\", "/")
        if p:
            # Trailing-slash-aware: only match at a slash boundary so
            # /data/Media does not mis-strip /data/MediaExtra/...
            p_norm = p.rstrip("/")
            if s == p_norm or s.startswith(p_norm + "/"):
                s = s[len(p_norm) :]
        return s.strip("/")

    # Fix A (#285): normalize backslashes once, then compare against
    # forward-slash arr_prefix values.
    arr_path_norm = arr_path.replace("\\", "/")
    best: Library | None = None
    best_len = -1
    for lib in settings.libraries:
        ap = lib.arr_prefix.replace("\\", "/") if lib.arr_prefix else ""
        ap_norm = ap.rstrip("/")
        if (
            ap_norm
            and (arr_path_norm == ap_norm or arr_path_norm.startswith(ap_norm + "/"))
            and len(ap_norm) > best_len
        ):
            best, best_len = lib, len(ap_norm)
    if best is None:
        return arr_path_norm.strip("/")
    rel = arr_path_norm[best_len:].strip("/")
    if best.slug:
        return f"@{best.slug}/{rel}" if rel else f"@{best.slug}/"
    return rel
