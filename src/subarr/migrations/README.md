# subarr SQLite migrations

Numbered `.sql` files applied in order on app startup by
`src/subarr/migrations.py`.

## Naming convention

- `NNN_short_description.sql` — three-digit prefix, snake_case description
- Apply order = lexical sort of filenames
- Once applied, a migration is **never edited** — add a new file instead

## What each migration may do

- Add a new table
- Add a new column (`ALTER TABLE ... ADD COLUMN ...`)
- Add an index
- Backfill data (in plain SQL only — no Python in migrations)

## What each migration must NOT do

- Drop tables or columns (data loss — open a discussion first)
- Be idempotent-unsafe (every migration is wrapped in a transaction;
  running twice should be a no-op, even on a partial-apply state)
- Reference Python objects, env vars, or anything outside the SQL file
- Depend on a specific SQLite version beyond what we ship (3.40+)

## Authoring checklist

1. Pick the next number: `ls src/subarr/migrations/ | tail -1`
2. Create `NNN_<description>.sql` with the change
3. Use `IF NOT EXISTS` / `IF EXISTS` clauses where SQLite supports them
4. Test fresh-DB application: delete subarr.db, restart, observe logs
5. Test upgrade application: run with an existing db, restart, observe
6. Add a test in `tests/test_migrations.py` if the migration has
   non-trivial logic (backfills, multi-statement transactions)
7. Bump `subarr.__version__` if appropriate

## Running migrations manually

The runner runs automatically at app boot, but for diagnostic purposes:

```python
from pathlib import Path
from subarr.migrations import MigrationRunner

runner = MigrationRunner(
    db_path=Path("./subarr.db"),
    migrations_dir=Path("src/subarr/migrations"),
)
runner.run()
print(runner.applied_versions())
```

## Rollback

We don't ship down-migrations. If you need to roll back, restore from
backup (see Settings → Backup/Restore in the UI, when that lands).
Rolling forward via a fix-up migration is the canonical pattern.
