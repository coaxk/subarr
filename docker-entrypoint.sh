#!/bin/sh
# #237: run subarr as non-root while staying transparent for existing installs.
#
# This entrypoint starts as root, reconciles the bundled `subarr` user to the
# requested PUID/PGID, fixes ownership of the data dir (so an existing
# root-owned /data/subarr.db stays readable after `compose pull`), grants the
# runtime user access to a raw docker socket if one is mounted, then DROPS to
# the non-root user before exec'ing the app. The running process is non-root.
#
# Requires the entrypoint to start as root with CHOWN/SETUID/SETGID (+ FOWNER/
# DAC_OVERRIDE) — see the hardened-compose example in the README. If those caps
# are stripped, the chown is logged and skipped rather than crash-looping.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
APP_USER=subarr
# #369: subarr's OWN writable state all lives in the directory that holds the DB
# (subarr.db + -wal/-shm, subarr-overrides.json, subarr.lock, vad/, backups/).
# Default /data; relocate the whole lot by pointing SUBARR_DB_PATH at a dedicated
# volume (e.g. /config). The HF cache follows that dir so it stays on the app's
# own volume and is never an excuse to touch the media mount.
DB_DIR="$(dirname "${SUBARR_DB_PATH:-/data/subarr.db}")"
HF_DIR="${HF_HOME:-$DB_DIR/.cache/huggingface}"

log() { echo "[entrypoint] $*"; }

# If we're not root we can't reconcile ids / chown / drop — just run as-is.
if [ "$(id -u)" != "0" ]; then
  log "not running as root (uid $(id -u)); skipping privilege setup"
  exec "$@"
fi

# 1. Reconcile the subarr group + user to the requested PUID/PGID (-o allows a
#    non-unique id, e.g. matching an existing host uid).
groupmod -o -g "$PGID" "$APP_USER" 2>/dev/null || groupadd -o -g "$PGID" "$APP_USER"
usermod -o -u "$PUID" -g "$PGID" "$APP_USER" 2>/dev/null \
  || useradd -o -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin "$APP_USER"

# 2. Reconcile ownership of subarr's OWN state dir + HF cache — only when needed
#    (skip when the top dir already matches; a recursive chown over a multi-GB HF
#    cache is slow).
#
#    #369 — CRITICAL: ONLY subarr's own dir is touched. The media library is a
#    SEPARATE mount (default /media/library) that subarr treats as foreign,
#    read-mostly data — it is NEVER chowned. In multi-library setups EVERY media
#    mount must already be owned/writable by PUID (subarr writes sidecars there);
#    the entrypoint cannot enumerate them (it runs before the app reads its
#    config) and must never re-own them. The old code chowned a hardcoded /data
#    recursively, which destroyed foreign ownership whenever the media dataset
#    was mounted at /data. Do NOT point SUBARR_DB_PATH at a media mount — keep
#    subarr state on a dedicated volume (/data or /config).
mkdir -p "$DB_DIR" "$HF_DIR"
reconcile_owner() {
  d="$1"
  [ -e "$d" ] || return 0
  if [ "$(stat -c '%u:%g' "$d")" != "$PUID:$PGID" ]; then
    log "chown -R $PUID:$PGID $d"
    # Errors are surfaced (no 2>/dev/null): a failed reconcile means subarr may
    # be unable to write its DB/cache, and the operator needs to see why.
    if ! chown -R "$PUID:$PGID" "$d"; then
      log "WARN: could not chown $d — add CHOWN/FOWNER/DAC_OVERRIDE caps or run rootful; continuing (subarr may be unable to write here)"
    fi
  fi
}
reconcile_owner "$DB_DIR"   # subarr.db + -wal/-shm, overrides, lock, vad/, backups/
reconcile_owner "$HF_DIR"   # LaBSE/QE model cache (under DB_DIR by default)

# 3. Raw docker socket support: a non-root user can't read a root:docker socket
#    unless it's in that gid. (socket-proxy users hit a TCP endpoint — no socket,
#    nothing to do here.)
if [ -S /var/run/docker.sock ]; then
  SOCK_GID="$(stat -c '%g' /var/run/docker.sock)"
  if [ -n "$SOCK_GID" ] && [ "$SOCK_GID" != "0" ]; then
    getent group "$SOCK_GID" >/dev/null 2>&1 || groupadd -o -g "$SOCK_GID" dockersock 2>/dev/null || true
    GRP="$(getent group "$SOCK_GID" | cut -d: -f1)"
    [ -n "$GRP" ] && usermod -aG "$GRP" "$APP_USER" 2>/dev/null || true
    log "granted $APP_USER access to docker.sock (gid $SOCK_GID)"
  fi
fi

# 4. Drop to the non-root user (init-groups picks up the socket group from step 3)
#    and hand off to the CMD.
log "starting as $APP_USER ($PUID:$PGID)"
exec setpriv --reuid "$PUID" --regid "$PGID" --init-groups "$@"
