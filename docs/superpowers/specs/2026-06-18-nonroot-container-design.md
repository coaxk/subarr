# Non-root container (#237)

**Goal:** run the subarr process as non-root, honour the (currently decorative) `PUID/PGID`, and do it **without breaking existing installs** whose `/data` is root-owned.

**Approach: Path A (PUID-honouring entrypoint).** No static `USER` directive — a root entrypoint fixes ownership and drops privileges before exec, so the *running* process is non-root. This is the arr-ecosystem standard and the only option that transparently handles root-owned `/data` under the project's hardened-compose posture.

**Lint:** the static `missing-user` check is **not gating** in our CI (Trivy here is an *image-CVE* scan, not `trivy config`; no semgrep/CodeQL dockerfile rule gates us) — every PR already passes with no `USER`. So no suppression is involved; the runtime drop is the real hardening.

---

## Flow-on effects (investigated) and how each is handled
- **Docker via socket-proxy (recommended):** TCP — unaffected.
- **Docker via raw `/var/run/docker.sock`:** non-root can't read a root:docker socket → the entrypoint adds the runtime user to the **gid of the mounted socket** (if present) so discovery / subgen-restart / log-tail keep working.
- **GPU:** no CUDA in subarr (only shells `nvidia-smi` for detection; the LaBSE QE judge is CPU-torch) → non-issue.
- **HuggingFace QE cache** at `/root/.cache/huggingface`: non-root can't write `/root` → relocate via `HF_HOME` to a writable, chowned path (`/data/.cache/huggingface`), and the compose volume mount moves with it.
- **Media sidecar writes:** subgen writes the `.srt`, not subarr → non-issue; non-root-matching-PUID actually improves arr file ownership.

## Components

### Dockerfile
- Install `gosu` **or** use `setpriv` (util-linux ships in `python:3.12-slim`) for privilege drop — prefer `setpriv` (no new package).
- Create a default `subarr` group+user at `1000:1000` (overridable at runtime by `PUID/PGID`).
- `ENV HF_HOME=/data/.cache/huggingface` (QE cache off `/root`).
- `ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]`; keep `CMD ["python","-m","subarr.app"]`.
- No `USER` directive (entrypoint must start root to chown + drop).

### `docker-entrypoint.sh` (runs as root, drops before exec)
1. Resolve `PUID`/`PGID` (default 1000/1000); reconcile the `subarr` user/group to those ids.
2. `chown` `/data` (recursive, idempotent — skip if already owned) so an existing root-owned DB becomes readable; create + chown `$HF_HOME`.
3. If `/var/run/docker.sock` exists, read its gid and add the runtime user to that group (raw-socket support).
4. Drop to `PUID:PGID` via `setpriv --reuid --regid --init-groups` and `exec` the CMD.
5. Best-effort + loud logging; if `chown` fails (e.g. caps stripped), log a clear actionable message rather than a silent crash-loop.

### README hardened-compose update
`cap_drop: [ALL]` blocks the entrypoint's `chown`. Update the example to `cap_drop: [ALL]` + `cap_add: [CHOWN, SETUID, SETGID, FOWNER, DAC_OVERRIDE]` (the minimal set the entrypoint needs to chown + drop). Net posture: non-root process with no caps — a genuine improvement over today's root-with-all-caps. Document `PUID/PGID` now being real.

## Error handling
- Idempotent chown (skip when ownership already correct) — fast on every boot.
- If running under `cap_drop: ALL` without the added caps, `chown` fails → log "cannot fix /data ownership; add CHOWN/SETUID/SETGID caps or run rootful" and continue attempting to start (so a correctly-owned volume still works).

## Testing
- **Unit:** factor any non-trivial id/gid resolution into a tiny testable helper if warranted; otherwise the entrypoint is integration-tested.
- **Upgrade integration test (the critical one):** `docker build` the image, then run it against a **root-owned `/data`** containing a `subarr.db` (simulating an existing install) → assert it boots, the process is **non-root** (`id -u` ≠ 0 inside), and the DB is readable. Also cover: `PUID/PGID` override changes the runtime uid; a mounted raw docker.sock grants group access.
- **Live verification:** the dev box (`subarr-next`) is the real upgrade case — raw socket + existing root-owned `/data` + existing `/root/.cache` QE cache. Build + run the new image there and confirm health, docker discovery, and the QE judge still work.

## Out of scope
The s6-overlay init system (overkill for one process); multi-process supervision.
