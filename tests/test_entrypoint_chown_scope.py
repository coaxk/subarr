"""#369 regression: docker-entrypoint.sh must reconcile ownership of ONLY
subarr's own state dir (dirname of SUBARR_DB_PATH) + the HF cache — never a
hardcoded /data, never the shared media mount.

The old entrypoint ran `chown -R $PUID:$PGID /data`, destroying foreign ownership
of the shared media library (and a co-located Nextcloud) on a routine upgrade.

We run the real script under `sh` with the privileged commands (id, stat, chown,
setpriv, user/group tools, getent) shimmed to record their args, so no root /
real chown is needed. We then assert what `chown` was asked to touch.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parent.parent / "docker-entrypoint.sh"

# The harness shims privileged tools via PATH, which needs POSIX `:`-separated
# PATH + POSIX paths — both break when driven from Windows (a `C:\...` path
# splits on `:`). Runs in CI (Linux) and on macOS/Linux dev; skipped on Windows
# (verified manually there via Git Bash).
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None or not ENTRYPOINT.exists(),
    reason="needs a POSIX sh + POSIX PATH semantics (not Windows)",
)


def _run_entrypoint(tmp_path: Path, db_path: str) -> list[str]:
    """Run the entrypoint with shimmed privileged tools; return the list of
    paths `chown` was invoked on. `stat` always reports a mismatching owner so
    the reconcile branch fires."""
    shim = tmp_path / "shimbin"
    shim.mkdir()
    chown_log = tmp_path / "chown.log"

    shims = {
        "id": "#!/bin/sh\necho 0\n",  # pretend root so the chown branch runs
        "stat": '#!/bin/sh\necho "999:999"\n',  # always mismatch -> trigger chown
        # record every path chown is asked to touch, one per line
        "chown": f'#!/bin/sh\nfor a in "$@"; do case "$a" in -*|*:*) ;; *) echo "$a" >> "{chown_log}";; esac; done\nexit 0\n',
        "setpriv": "#!/bin/sh\nexit 0\n",  # final exec -> no-op
        "groupmod": "#!/bin/sh\nexit 0\n",
        "usermod": "#!/bin/sh\nexit 0\n",
        "groupadd": "#!/bin/sh\nexit 0\n",
        "useradd": "#!/bin/sh\nexit 0\n",
        "getent": "#!/bin/sh\nexit 1\n",
    }
    for name, body in shims.items():
        p = shim / name
        p.write_text(body)
        p.chmod(0o755)

    env = {
        "PATH": f"{shim}:/usr/bin:/bin",
        "PUID": "3001",
        "PGID": "3001",
        "SUBARR_DB_PATH": db_path,
    }
    subprocess.run(
        ["sh", str(ENTRYPOINT), "/bin/true"],
        env=env,
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if not chown_log.exists():
        return []
    return [ln for ln in chown_log.read_text().splitlines() if ln.strip()]


def test_chown_scoped_to_db_dir_not_data(tmp_path: Path):
    # subarr state on a dedicated /config-style volume; media lives elsewhere.
    cfg = tmp_path / "config"
    media = tmp_path / "media"  # the shared media mount — must never be chowned
    (media / "Movies").mkdir(parents=True)
    (media / "Movies" / "foreign.mkv").write_text("x")

    chowned = _run_entrypoint(tmp_path, db_path=str(cfg / "subarr.db"))

    # it reconciled subarr's own state dir...
    assert str(cfg) in chowned, f"expected {cfg} to be chowned; got {chowned}"
    # ...and the HF cache (which defaults under the state dir)...
    assert any("huggingface" in c for c in chowned), chowned
    # ...and NEVER the media mount or a hardcoded /data.
    assert str(media) not in chowned, f"media mount must not be chowned: {chowned}"
    assert not any(c == "/data" or c.startswith("/data/") for c in chowned), chowned
    assert not any(str(media) in c for c in chowned), chowned


def test_default_db_dir_is_data_only(tmp_path: Path):
    # With the default SUBARR_DB_PATH=/data/subarr.db, DB_DIR is /data — but the
    # chown target must be exactly /data (the app's own volume), resolved from
    # the DB path, NOT a value baked into the script. Point the default at a tmp
    # dir to prove it follows SUBARR_DB_PATH.
    data = tmp_path / "data"
    chowned = _run_entrypoint(tmp_path, db_path=str(data / "subarr.db"))
    assert str(data) in chowned, chowned
    # nothing outside the state dir tree was touched
    assert all(c == str(data) or str(data) in c for c in chowned), chowned
