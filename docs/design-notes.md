# Design Notes

## Four path representations

The library tree exists at multiple vantage points. Subarr canonicalises to one and converts at boundaries.

| Layer | Example |
|---|---|
| UNC (Windows) | `\\192.168.1.119\share\Media\TV\Show\Season 1` |
| Windows drive | `Z:\Media\TV\Show\Season 1` |
| WSL2 / LianLi host | `/mnt/z/Media/TV/Show/Season 1` |
| Inside subgen container | `/media/library/TV/Show/Season 1` |
| Inside Subarr container | `/media/library/TV/Show/Season 1` (volume-mounted from `/mnt/nas/Media`) |
| Subarr canonical | `TV/Show/Season 1` |

Canonical = no leading slash, forward slashes, relative to `SUBARR_MEDIA_ROOT` (default `/media/library`). All API paths use canonical. Conversion to subgen's `/batch?directory=` form happens in exactly one place: `paths.canonical_to_subgen_batch`.

## docker.sock security tradeoff

Subarr mounts `/var/run/docker.sock` to call `docker logs subgen` and `docker restart subgen`. This is root-equivalent on the host. Mitigated by:

- LAN-only access (Pi-hole DNS, no public ingress).
- No auth needed because no public exposure.
- A small fixed set of Docker SDK operations: `logs(container=subgen, follow=True)` and `restart(container=subgen)`. No user input flows into container names or command arguments.
- Future: switch to `socketproxy` (already in the stack at port 2375) if defence-in-depth is wanted. The Docker SDK swaps via `DOCKER_HOST=tcp://socketproxy:2375`.

## Mode-switching: deliberately not implemented

Earlier drafts of this spec included a European/Japanese mode toggle that swapped `compose.european.yaml` / `compose.japanese.yaml` templates and restarted the subgen container. **Subarr does not do this.**

Why: v4 of the patched subgen wires per-language kwargs (`SUBGEN_KWARGS_LANG_<CODE>`) which were dead code before v4. When subgen processes a file, it auto-applies the kwargs matching the detected/forced language. No container restart needed between scanning a French show and a Japanese drama. The live `compose.yaml` has 18 per-language blocks (JA, DE, NL, IT, FR, ES, PT, KO, ZH, RU, PL, SV, NO, DA, FI, AR, CS, EL).

Consequences:
- `compose.european.yaml` / `compose.japanese.yaml` template files don't exist on disk because they're not needed.
- `GET /api/mode` is a **transparency endpoint**: returns the per-language tuning map for the Settings tab to display read-only. No `POST /api/mode`.
- Subgenscan.ps1's Options 4/5 (mode switch) are technically obsolete. Worth fixing in a future PS update, not Subarr's job.

## Volume mounts

`/dockercontainers/subgen` is mounted **read-only**. Subarr only ever reads `compose.yaml` for the transparency view. There is no write path to subgen's config from Subarr — that's deliberate and a v2 inline-edit feature would change the mount mode, not bypass it.

## Subgen patches

Subarr relies on two custom subgen patches applied via `update_subgen_v4.py`:

- **v4.1** — `/batch` returns structured JSON `{walked, queued, skipped, already_in_queue, no_audio, pending_language_detect}` with HTTP 404 when `walked == 0`. Lets Subarr (and Subgenscan.ps1) drive multi-folder scans honestly.
- **v4.2** (planned) — `GET /queue` returns `{queued, processing, queued_count, processing_count, idle, version}`. Wraps existing `task_queue.get_queued_tasks/get_processing_tasks/is_idle` methods. Makes the Monitor tab and v1.2 GPU-idle gating possible.

If v4.2 isn't applied, `GET /api/queue` falls back to parsing `WORKER START` / `WORKER FINISH` lines from `docker logs subgen --tail 200`. Lossier; documented limitation.
