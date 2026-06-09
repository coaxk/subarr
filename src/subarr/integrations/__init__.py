"""Subarr integrations: read-only clients for the *arr ecosphere.

Each client is a thin async httpx wrapper around the one or two endpoints
Subarr actually needs. We deliberately don't model the upstream APIs
exhaustively — we extract just the fields the coverage engine consumes.
If an upstream's shape changes, only the client + the engine change; the
GUI stays stable.

Conventions:
- `is_configured()` returns False if URL or API key is missing; callers
  must check before issuing requests.
- Network errors raise IntegrationError; the engine catches and degrades
  per-source.
- All clients use a single shared httpx.AsyncClient created at app
  lifespan and closed on shutdown.
"""

from __future__ import annotations


class IntegrationError(RuntimeError):
    """Upstream returned a non-2xx, was unreachable, or returned malformed JSON."""
