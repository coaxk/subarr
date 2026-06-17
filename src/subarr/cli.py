"""#238: auth recovery CLI — `python -m subarr.cli <command>`.

  reset-auth                              clear the stored credential → first-run setup
  set-password --username U --password P  set/replace the admin credential

Docker lock-out recovery:
  docker exec subarr python -m subarr.cli reset-auth
  docker exec subarr python -m subarr.cli set-password --username admin --password '...'
"""

from __future__ import annotations

import argparse
import sys

from .auth import hash_password
from .auth_store import AuthStore
from .config import settings
from .migrate import run_migrations

_MIN_LEN = 8


def reset_auth(store: AuthStore) -> int:
    store.clear_credential()
    print("auth reset: stored credential cleared. subarr shows first-run setup on next load.")
    return 0


def set_password(store: AuthStore, username: str, password: str) -> int:
    if len(password) < _MIN_LEN:
        print(f"error: password must be at least {_MIN_LEN} characters", file=sys.stderr)
        return 2
    h, salt, iters = hash_password(password)
    store.set_credential(username, h, salt, iters)
    print(f"auth: password set for user '{username}'.")
    return 0


def _store() -> AuthStore:
    run_migrations(settings.db_path)  # ensure the auth tables exist
    return AuthStore(settings.db_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="subarr.cli", description="subarr auth recovery")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reset-auth", help="clear the stored credential (back to first-run setup)")
    sp = sub.add_parser("set-password", help="set/replace the admin credential")
    sp.add_argument("--username", required=True)
    sp.add_argument("--password", required=True)
    args = parser.parse_args(argv)

    store = _store()
    if args.cmd == "reset-auth":
        return reset_auth(store)
    if args.cmd == "set-password":
        return set_password(store, args.username, args.password)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
