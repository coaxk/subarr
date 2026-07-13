"""SQLite schema migration runner.

Reads `.sql` files from a migrations directory in lexical order, applies
each in a transaction if its version hasn't been recorded in the
`schema_versions` table, records the application.

Design notes:

- One shared `schema_versions` table tracks applied migrations across the
  entire `subarr.db`. We don't shard per-store because all the stores
  share one SQLite file anyway, and a single source of truth for "which
  schema is on disk" beats per-store version-tracking by a mile.

- Migrations are SQL-only. No Python hooks, no env-var references. The
  point is that anyone reading `migrations/NNN_*.sql` can see exactly
  what changes the file makes, and that the change is the same on every
  machine. Backfills go in SQL; if the backfill logic is too complex
  for SQL we re-think the schema, not extend the migration system.

- Each migration runs in a transaction. Failure mid-file rolls back; the
  version row is not inserted; the app's startup aborts with the SQL
  error visible. This is the right failure mode — silent partial-apply
  is the worst possible outcome.

- Idempotent. Running `.run()` on an already-up-to-date DB is a no-op
  (just a SELECT on schema_versions per migration to check).

- We use `executescript` per file. A single migration file MAY contain
  multiple statements separated by `;`. The transaction wraps the
  whole executescript.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .data_persistence import apply_journal_mode

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    """One numbered .sql file on disk."""

    version: int  # parsed from filename prefix (e.g. 001 → 1)
    name: str  # filename without .sql (e.g. "001_baseline")
    path: Path  # full path to the .sql file


class MigrationRunner:
    """Applies pending migrations to a SQLite database.

    Usage:
        runner = MigrationRunner(db_path, migrations_dir)
        runner.run()
    """

    def __init__(self, db_path: Path, migrations_dir: Path):
        self._db_path = db_path
        self._dir = migrations_dir

    # ─── Public API ──────────────────────────────────────────────────

    def run(self) -> list[Migration]:
        """Apply all pending migrations. Returns the list of migrations
        that were applied this call (empty if DB was already up-to-date).
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        try:
            conn.execute(
                "PRAGMA busy_timeout=5000"
            )  # #291: a concurrent migrator (dev --reload) WAITS for the lock instead of erroring "database is locked"
            apply_journal_mode(
                conn, self._db_path
            )  # #291 NIT: we rely on WAL's default synchronous=NORMAL — do NOT set synchronous=OFF (permits corruption on power loss)
            conn.execute("PRAGMA foreign_keys=ON")
            self._ensure_version_table(conn)
            applied_now: list[Migration] = []
            already = self._applied_versions(conn)
            for m in self._discover():
                if m.version in already:
                    log.debug("migration %s already applied", m.name)
                    continue
                self._apply_one(conn, m)
                applied_now.append(m)
                log.info("migration applied: %s", m.name)
            if not applied_now:
                log.info("migrations: already up to date (%d applied)", len(already))
            else:
                log.info(
                    "migrations: applied %d new (now at %d total)",
                    len(applied_now),
                    len(already) + len(applied_now),
                )
            return applied_now
        finally:
            conn.close()

    def applied_versions(self) -> list[int]:
        """Public read-only view of which versions are recorded as applied
        on disk. Useful for diagnostics / Settings → System info."""
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        try:
            self._ensure_version_table(conn)
            return self._applied_versions(conn)
        finally:
            conn.close()

    def discover(self) -> list[Migration]:
        """Public view of the migrations on disk in apply order."""
        return self._discover()

    # ─── Internal ────────────────────────────────────────────────────

    @staticmethod
    def _ensure_version_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_versions (
                version     INTEGER PRIMARY KEY,
                name        TEXT NOT NULL,
                applied_at  REAL NOT NULL
            )
            """
        )

    @staticmethod
    def _applied_versions(conn: sqlite3.Connection) -> list[int]:
        rows = conn.execute("SELECT version FROM schema_versions ORDER BY version").fetchall()
        return sorted([r[0] for r in rows])

    def _discover(self) -> list[Migration]:
        if not self._dir.is_dir():
            return []
        out: list[Migration] = []
        for p in sorted(self._dir.glob("*.sql")):
            stem = p.stem  # e.g. "001_baseline"
            prefix = stem.split("_", 1)[0]
            try:
                version = int(prefix)
            except ValueError:
                log.warning("ignoring migration with non-numeric prefix: %s", p.name)
                continue
            out.append(Migration(version=version, name=stem, path=p))
        return out

    @staticmethod
    def _apply_one(conn: sqlite3.Connection, m: Migration) -> None:
        import time

        sql = m.path.read_text(encoding="utf-8")
        # We can't use sqlite3.executescript() — it issues an implicit
        # COMMIT before running, which breaks our atomic-per-migration
        # contract. Split on ';' and execute one statement at a time
        # inside a manual transaction so failure mid-file rolls back
        # cleanly and the version row is NOT recorded.
        #
        # CRITICAL: strip comments BEFORE splitting on ';' — a ';' inside
        # a comment ('writes; the next') would otherwise split a comment
        # in two and leave the trailing fragment to be parsed as SQL.
        # We strip line-level `--` comments first (the only kind we use),
        # then split.
        stripped_lines = []
        for line in sql.splitlines():
            s = line.strip()
            if s.startswith("--"):
                continue
            # Also strip inline `-- foo` trailing comments. Naive but
            # safe given we don't put `--` literally inside string
            # literals in any migration (and never will — it's banned
            # in migrations/README.md).
            if "--" in line:
                line = line.split("--", 1)[0].rstrip()
            stripped_lines.append(line)
        sql_no_comments = "\n".join(stripped_lines)

        statements = []
        for raw in sql_no_comments.split(";"):
            clean = raw.strip()
            if clean:
                statements.append(clean)

        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in statements:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    # ADD COLUMN is not idempotent and SQLite has no
                    # "ADD COLUMN IF NOT EXISTS". On a TRANSITIONAL DB — one
                    # a prior init_schema() already extended — a parity
                    # migration's ADD COLUMN raises "duplicate column name".
                    # Treat ONLY that as a no-op for this statement so the
                    # migration still completes + records. A duplicate-column
                    # error does not abort the SQLite transaction, so the
                    # remaining statements + the version INSERT still commit.
                    # Any OTHER OperationalError re-raises → rollback + abort
                    # (the atomic contract is preserved).
                    if "duplicate column name" in str(e).lower():
                        log.info(
                            "migration %s: column already present, skipping: %s",
                            m.name,
                            stmt.split("\n", 1)[0][:80],
                        )
                        continue
                    raise
            try:
                conn.execute(
                    "INSERT INTO schema_versions (version, name, applied_at) VALUES (?, ?, ?)",
                    (m.version, m.name, time.time()),
                )
            except sqlite3.IntegrityError:
                # #291: a concurrent migrator (two processes booting together,
                # e.g. dev --reload) recorded this version between our
                # applied-versions read and now. The migration body is
                # idempotent (CREATE IF NOT EXISTS / ADD COLUMN duplicate-guard),
                # so roll back our duplicate work and treat it as applied — no
                # crash, no corruption.
                conn.execute("ROLLBACK")
                log.info("migration %s already recorded by a concurrent process; skipping", m.name)
                return
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass  # transaction already aborted by sqlite itself
            raise


def run_migrations(db_path: Path, migrations_dir: Path | None = None) -> list[Migration]:
    """Convenience: discover the bundled migrations dir + run.

    Used from app.py's lifespan setup before any store calls init_schema().
    """
    if migrations_dir is None:
        migrations_dir = Path(__file__).parent / "migrations"
    return MigrationRunner(db_path, migrations_dir).run()


def _migration_versions(db_path: Path) -> list[int]:
    """Public helper for /api/admin or Settings → System surface."""
    return MigrationRunner(db_path, Path(__file__).parent / "migrations").applied_versions()
