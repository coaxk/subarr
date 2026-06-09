"""GET /api/provenance/{canonical_path:path}

Combines three sources to answer 'who wrote this sub, when, with what?':
- Subarr's own ledger (transcribes initiated through subarr)
- Bazarr's history endpoint (downloads through Bazarr — providers, scores)
- Filename suffix parse (e.g. '.subgen.en.srt' indicates subgen provenance)

GET /api/provenance/recent → last 50 ledger entries (for a future
'Activity' panel in the UI).
"""

from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Request

from ..integrations import IntegrationError

router = APIRouter(prefix="/api", tags=["provenance"])
log = logging.getLogger(__name__)


# Match filename suffix conventions: <basename>.<lang>.<provider>.srt
# e.g. Foo.en.subgen.srt  (provider = subgen)
#      Foo.en.subliminal.srt (provider = subliminal, Bazarr-mediated)
_SUFFIX_RE = re.compile(r"\.([a-z]{2,3})(?:\.([a-zA-Z0-9_-]+))?\.srt$", re.IGNORECASE)


def _parse_suffix(name: str) -> dict[str, Any] | None:
    m = _SUFFIX_RE.search(name)
    if not m:
        return None
    return {"lang": m.group(1).lower(), "provider": (m.group(2) or "").lower() or None}


@router.get("/provenance/recent")
async def recent(request: Request) -> dict[str, Any]:
    store = request.app.state.provenance
    rows = store.recent(limit=50)
    return {"entries": [e.to_dict() for e in rows]}


@router.get("/provenance/{path:path}")
async def by_path(path: str, request: Request) -> dict[str, Any]:
    """Lookup by canonical_path. The :path converter preserves slashes."""
    canonical = path.strip().strip("/")
    store = request.app.state.provenance
    bundle = request.app.state.integrations

    ledger_rows = [r.to_dict() for r in store.query_by_path(canonical)]
    suffix = _parse_suffix(PurePosixPath(canonical).name)

    bazarr_history: list[dict[str, Any]] = []
    bazarr_error: str | None = None
    # Lookup Bazarr by sonarr_episode_id if any ledger row has one;
    # otherwise we don't have an id to query with.
    sonarr_ep_id = next(
        (r.get("sonarr_episode_id") for r in ledger_rows if r.get("sonarr_episode_id")),
        None,
    )
    if sonarr_ep_id and bundle.bazarr.is_configured():
        try:
            bazarr_history = await bundle.bazarr.episodes_history(sonarr_episode_id=sonarr_ep_id)
        except IntegrationError as e:
            bazarr_error = str(e)
    elif bundle.bazarr.is_configured():
        # No sonarr id known — surface a hint in the response shape but
        # don't error.
        bazarr_history = []

    return {
        "canonical_path": canonical,
        "ledger": ledger_rows,
        "filename_suffix": suffix,
        "bazarr": {
            "history": bazarr_history,
            "error": bazarr_error,
            "looked_up_via_sonarr_episode_id": sonarr_ep_id,
        },
    }
