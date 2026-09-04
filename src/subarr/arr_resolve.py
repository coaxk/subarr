"""#485: resolve a Sonarr episode id against the library's OWN Sonarr instance.

Sonarr episode ids are unique only WITHIN an instance. Three call sites resolved
one against `bundle.sonarr`, which the bundle documents as the instance-0 alias:

    # Read/write aliases for instance 0. ... Multi-instance code uses
    # client_for()/clients_for() instead.

On a multi-instance install that means clicking a row in one library resolved
the id in a DIFFERENT library's Sonarr and targeted an unrelated file. Reported
by AztecGuyGDL: `@anime/The Legend of Vox Machina S03E02` resolved to
`@tv/The Chosen S02E04`.

⚠️ The manual path merely refused the job, because the already-subtitled guard
happened to catch it. The SCHEDULER path has no such guard and nobody watching,
so it would transcribe the wrong episode and write a .srt beside the wrong show.

⚠️ The correct answer was already available. coverage_engine builds its
episode-file maps PER INSTANCE and puts the right path on the row as
`file_canonical_path`. These sites re-derived it, wrongly. Prefer a canonical
you already hold over anything this function returns.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


async def resolve_episode_target(
    bundle,
    *,
    sonarr_episode_id: int | None,
    canonical_hint: str | None = None,
) -> tuple[str | None, int | None]:
    """Return `(canonical_path, series_id)` for a Sonarr episode.

    `canonical_hint` selects the instance: its `@slug` head identifies the
    library, and the library names the Sonarr that owns this id. Any canonical
    for the same row will do, file or directory, since only the slug is read.

    Without a hint this falls back to instance 0. That is the OLD behaviour and
    it is wrong on a multi-instance install, kept only so callers that genuinely
    have no canonical keep working. Supply a hint wherever one exists.

    Never raises: a control action must not 500 the page, and the scheduler must
    not lose a whole walk to one unresolvable row. Returns `(None, None)` when
    the episode cannot be resolved.
    """
    if sonarr_episode_id is None:
        return None, None

    # clients_for degrades an empty binding to instance 0, so a single-stack
    # install is byte-identical to the unscoped path.
    try:
        if canonical_hint:
            from .coverage_engine import clients_for

            client = clients_for(bundle, canonical_hint).sonarr
        else:
            client = bundle.sonarr
    except Exception:  # noqa: BLE001 - resolution must never take out the caller
        log.debug("episode resolve: client lookup failed for hint %r", canonical_hint)
        return None, None

    if client is None or not client.is_configured():
        return None, None

    try:
        ep = await client.episode(sonarr_episode_id)
    except Exception as e:  # noqa: BLE001
        log.debug("episode resolve: episode(%s) failed: %s", sonarr_episode_id, e)
        return None, None

    series_id = ep.get("seriesId")
    ep_file_id = ep.get("episodeFileId")
    if not ep_file_id:
        # Sonarr knows the episode but has no file for it. series_id is still
        # good provenance; there is simply nothing to transcribe.
        return None, series_id

    try:
        ep_file = await client.episode_file(ep_file_id)
    except Exception as e:  # noqa: BLE001
        log.debug("episode resolve: episode_file(%s) failed: %s", ep_file_id, e)
        return None, series_id

    arr_path = ep_file.get("path")
    if not arr_path:
        return None, series_id

    from .paths import strip_arr_prefix

    return strip_arr_prefix(arr_path), series_id
