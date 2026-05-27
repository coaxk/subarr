# Subarr

A peer service in the *arr family. Coordinates the subtitle ecosphere: drives [subgen](https://github.com/McCloudS/subgen) for transcription, surfaces gaps from Bazarr/Sonarr/Radarr, prioritises with Tautulli watch history, and (later) automates the lot with LLM enrichment for edge cases.

Replaces the manual [`Subgenscan.ps1`](https://github.com/coaxk/dockerstacks/blob/main/scripts/subgenscan/Subgenscan.ps1) loop with a web UI: folder tree, multi-folder scan queue, live log tail, GPU/queue monitor, transparency view of subgen tuning.

**Runs on**: LianLi (LAN-only), Pi-hole DNS at `subarr.lab.home.arpa`
**Port**: 9922
**Stack**: Python 3.12 / FastAPI / vanilla JS / SQLite

## Status

Pre-alpha. Phase 1 complete (health, browse, mode transparency view). Phase 2 in progress.

## Roadmap

| Version | Theme | Status |
|---|---|---|
| v1.0 | Manual driver + monitoring (folder tree, scan queue, live logs, GPU widget, per-language kwargs transparency) | in progress |
| v1.1 | Coverage dashboard — Bazarr/Sonarr/Radarr/Tautulli reconciliation, prioritised gap list, manual-confirm queue | planned |
| v1.2 | Automation + LLM enrichment via ollama, scheduled coverage walks, optional srt-cleaner integration | planned |

## Architecture

Subarr is a coordinator, not a transcriber. It owns no GPU code; subgen does that. State lives in subgen + Docker + (v1.1+) Bazarr/Sonarr/Radarr/Tautulli; Subarr mirrors and acts on it.

| Layer | Tech |
|---|---|
| Backend | FastAPI + uvicorn + httpx + docker SDK + PyYAML |
| Frontend | Vanilla HTML/CSS/JS, no build step, SSE for live streams |
| Storage | SQLite at `/data/subarr.db` (scan queue, provenance ledger, API keys) |

See [`docs/design-notes.md`](docs/design-notes.md) for path-translation rules and the docker.sock security tradeoff.

## Development

```bash
pip install -e .[dev]
uvicorn subarr.app:app --reload --port 9922
```

Then open http://localhost:9922.

## Deploy (production)

Pulls `ghcr.io/coaxk/subarr:latest` into `C:\DockerContainers\subarr\`. See [`deploy/compose.yaml`](deploy/compose.yaml) for the DockerStacks-conformant compose file.

## Related

- [`Subgenscan.ps1`](https://github.com/coaxk/dockerstacks/tree/main/scripts/subgenscan) (V69) — original PowerShell driver, kept as a fallback. Subarr replicates its behaviour, not its existence.
- `subgen` patched v4.x — adds `/batch` with V4.1 structured response and (planned) `/queue` for queue introspection.
- `srt-cleaner` (separate tool) — post-hoc Whisper hallucination detector. May fold in as a v1.2 tab.

## Why "Subarr"?

The tool is genuinely a peer to Sonarr/Radarr/Bazarr/Lidarr in the *arr ecosystem: a service that monitors its domain (subtitles), coordinates other services, and exposes a dashboard. Naming follows the convention.
