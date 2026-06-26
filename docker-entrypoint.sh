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
DB="${SUBARR_DB_PATH:-/data/subarr.db}"
DB_DIR="$(dirname "$DB")"
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

# 2. Reconcile ownership of subarr's OWN state — and ONLY that.
#
#    #369 — CRITICAL: NEVER chown a parent tree. The media library is a SEPARATE
#    mount (default /media/library) of foreign, read-mostly data; in multi-library
#    setups there are several such mounts. The entrypoint runs before the app
#    reads its config, so it cannot enumerate them — and must never re-own them.
#    The old code ran `chown -R /data`, which destroyed foreign ownership (and
#    co-located services) whenever a media dataset was mounted at /data. So we
#    chown ONLY subarr's own inodes: the state-dir node itself (non-recursive, so
#    new files can be created), the DB + sidecars, and subarr's own subdirs. A
#    media tree co-located inside $DB_DIR therefore survives untouched. EVERY
#    media mount must already be owned/writable by PUID (subarr writes sidecars
#    there) — keep SUBARR_DB_PATH on a dedicated volume (/data or /config), never
#    on the media tree.

# chown a single inode, non-recursive, only when its owner differs (set -e safe).
chown_node() {
  d="$1"
  [ -e "$d" ] || return 0
  if [ "$(stat -c '%u:%g' "$d" 2>/dev/null)" = "$PUID:$PGID" ]; then return 0; fi
  chown "$PUID:$PGID" "$d" \
    || log "WARN: could not chown $d — add CHOWN/FOWNER/DAC_OVERRIDE caps or run rootful; continuing (subarr may be unable to write here)"
}
# chown a subarr-EXCLUSIVE subtree (cache / backups / vad), recursive, gated on
# the top owner so a warm multi-GB HF cache isn't re-walked every boot.
chown_tree() {
  d="$1"
  [ -e "$d" ] || return 0
  if [ "$(stat -c '%u:%g' "$d" 2>/dev/null)" = "$PUID:$PGID" ]; then return 0; fi
  log "chown -R $PUID:$PGID $d"
  chown -R "$PUID:$PGID" "$d" \
    || log "WARN: could not chown $d; continuing (subarr may be unable to write here)"
}

case "$DB_DIR" in
  "" | "." | "/")
    # A bare/relative dir means SUBARR_DB_PATH is misconfigured (it must be an
    # absolute *file* path on a dedicated volume). Refuse to chown — a recursive
    # or root-level chown here would be catastrophic.
    log "WARN: SUBARR_DB_PATH dir resolves to '$DB_DIR' — refusing ownership reconcile; set SUBARR_DB_PATH to an absolute file path on a dedicated volume (e.g. /config/subarr.db)"
    ;;
  *)
    mkdir -p "$DB_DIR" "$HF_DIR" \
      || log "WARN: could not create $DB_DIR / $HF_DIR — is the mount writable? continuing"
    chown_node "$DB_DIR"                       # the state dir itself (so subarr can create files)
    for f in "$DB" "$DB-wal" "$DB-shm" "$DB_DIR/subarr-overrides.json" "$DB_DIR/subarr.lock"; do
      chown_node "$f"
    done
    chown_tree "$DB_DIR/vad"                   # silero-VAD cache (subarr-exclusive)
    chown_tree "$DB_DIR/backups"              # DB backups (subarr-exclusive)
    chown_tree "$HF_DIR"                       # LaBSE/QE model cache (under DB_DIR by default)
    ;;
esac

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
