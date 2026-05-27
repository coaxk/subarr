# subgenscan-gui

Web UI for [subgen](https://github.com/McCloudS/subgen) — a thin GUI layer that replaces the friction of `Subgenscan.ps1` (V69) with a folder tree, multi-folder scan queue, live log tail, GPU/queue monitor, and one-click mode swap.

**Runs on**: LianLi (LAN-only), Pi-hole DNS at `subgenscan.lab.home.arpa`
**Port**: 9922
**Stack**: Python 3.11 / FastAPI / vanilla JS / SQLite

## Status

Pre-alpha. Phase 1 scaffold.

## Architecture

The GUI is a remote control for subgen — it owns no transcription logic, only orchestration. State lives in subgen and Docker; the GUI mirrors it.

| Layer | Tech |
|---|---|
| Backend | FastAPI + uvicorn + httpx + docker SDK |
| Frontend | Vanilla HTML/CSS/JS, no build step, SSE for live streams |
| Storage | SQLite at `/data/subgenscan-gui.db` (scan queue persistence + recent paths) |

See `docs/design-notes.md` for the four-path-representation translation rules and the docker.sock security tradeoff.

## Development

```bash
pip install -e .[dev]
uvicorn subgenscan_gui.app:app --reload --port 9922
```

Then open http://localhost:9922.

## Deploy (production)

Pulls `ghcr.io/coaxk/subgenscan-gui:latest` into `C:\DockerContainers\subgenscan-gui\`. See `deploy/compose.yaml` for the DockerStacks-conformant compose file (safe-bridge network, json-file logging, resource limits, maintenance_log label).

## Related

- `Subgenscan.ps1` (V69) at `C:\DockerContainers\scripts\subgenscan\` — the original PowerShell tool. Kept as a fallback. Same subgen endpoints, same V4.1 structured response handling.
- `srt-cleaner` (separate tool) — post-hoc Whisper hallucination detector. Will be a v2 tab here if the pattern works.
