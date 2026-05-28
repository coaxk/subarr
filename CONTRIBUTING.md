# Contributing to subarr

Thanks for considering a contribution! Subarr is a homelab tool by
intent — small, opinionated, and honest about its trade-offs.
Contributions that align with that ethos are welcome.

## Before you start

**Open an issue first** for anything bigger than a one-file change.
The maintainer has strong opinions about scope; better to align
before you spend hours.

Out-of-scope (don't open PRs for these — they'll close):
- New top-level features that compete with Bazarr / Sonarr / etc.
  Subarr coordinates, it doesn't replace.
- UI rewrites in frameworks we don't already ship. Vanilla legacy
  + React-from-CDN for v1.0 is deliberate; build steps are friction.
- Adding dependencies "because it'd be cleaner with X". We pin
  what we have and review every new one.
- "Cleanup" PRs that touch many files without clear functional
  intent. Style PRs are noise.

In-scope:
- Bug fixes with a regression test
- New patches against subgen (raise in subarr-subgen first)
- Documentation, especially deploy-template variations for stacks
  we don't already cover
- Translations of UI copy (post-v1.0)
- Performance improvements with measurements

## Development setup

```bash
git clone https://github.com/coaxk/subarr
cd subarr
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
PYTHONPATH=src uvicorn subarr.app:app --reload --port 9922
```

Tests:

```bash
PYTHONPATH=src pytest -q
```

We expect tests to pass before requesting review. If your change has
no test, you're probably wrong about whether it's testable.

## Code conventions

- **Python**: type hints everywhere, `from __future__ import annotations`
  at the top, ~120 char lines (project uses ruff with line-length=110)
- **Async/IO**: `httpx.AsyncClient` for outbound HTTP, never `requests`
- **SQL**: parameterised queries only, no string concat. New tables
  go through a migration file (`src/subarr/migrations/NNN_*.sql`),
  never `init_schema()` for new code.
- **Commits**: one logical change per commit. Squash before opening
  a PR. Commit message: imperative subject line, body explains
  *why* if it's not obvious.

## Where to find things

```
src/subarr/
  app.py                — FastAPI app + lifespan wiring
  config.py             — env-var-driven Settings dataclass
  migrations/           — SQL migrations (numbered, applied in order)
  routers/              — one file per endpoint group
  integrations/         — clients for Bazarr / Sonarr / Radarr / Tautulli / Ollama
  static/v1/            — v1.0 React frontend (from Claude Design)
  static/               — legacy vanilla-JS UI (retiring)
tests/
  test_*.py             — pytest, ~200 tests
deploy/
  templates/            — 3 production compose templates per permission tier
  scripts/install.sh    — one-line install used by README quickstart
```

## Filing an issue

Use the templates in `.github/ISSUE_TEMPLATE/`. The bot's checklist
covers what we need to triage — please fill it out. "It doesn't work"
without logs + version + reproduction steps gets closed unanswered.

## Code of Conduct

See [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md). Tldr: be kind, assume
good faith, criticism focuses on the code not the person.
