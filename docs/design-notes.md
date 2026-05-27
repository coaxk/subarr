# Design Notes

## Four path representations

The library tree exists at five vantage points. The GUI canonicalises to one and converts at boundaries.

| Layer | Example |
|---|---|
| UNC (Windows) | `\\192.168.1.119\share\Media\TV\Show\Season 1` |
| Windows drive | `Z:\Media\TV\Show\Season 1` |
| WSL2 / LianLi host | `/mnt/z/Media/TV/Show/Season 1` |
| Inside subgen container | `/media/library/TV/Show/Season 1` |
| Inside GUI container | `/media/library/TV/Show/Season 1` (volume-mounted from `/mnt/nas/Media`) |
| GUI canonical | `TV/Show/Season 1` |

Canonical = no leading slash, forward-slashes, relative to `SUBGENSCAN_MEDIA_ROOT` (default `/media/library`). All API paths use canonical. Conversion to subgen's `/batch?directory=` form happens in exactly one place: `paths.canonical_to_subgen_batch`.

## docker.sock security tradeoff

The GUI mounts `/var/run/docker.sock` to call `docker logs subgen` and `docker restart subgen`. This is root-equivalent on the host. Mitigated by:

- LAN-only access (Pi-hole DNS, no public ingress).
- No auth needed because no public exposure.
- A small fixed set of Docker SDK operations: `logs(container=subgen, follow=True)` and `restart(container=subgen)`. No user input flows into container names or command arguments.
- Future: switch to `socketproxy` (already in the stack at port 2375) if we want a defence-in-depth layer. The Docker SDK supports `DOCKER_HOST=tcp://socketproxy:2375` swap with one env var.

## Mode detection

`Subgenscan.ps1` V69 detects mode via regex against the whole compose file: `patience.: 1.5` → European, `patience.: 1.0` → Japanese, else unknown. The GUI does the same, scoped to the `SUBGEN_KWARGS:` line so the per-language `SUBGEN_KWARGS_LANG_JA` (patience=1.0) doesn't false-positive. Both tools agree because they read the same file.

Mode switch = copy `compose.european.yaml` or `compose.japanese.yaml` over `compose.yaml`, then `docker restart subgen`. No YAML editing; pure file copy. Templates are the source of truth.
