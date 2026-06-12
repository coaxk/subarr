# Releasing

The full procedure (tag tiers, soak, `:stable` promotion, yank protocol)
lives in [docs/release-procedure.md](docs/release-procedure.md). Short form:

1. Bump `pyproject.toml` version on `main` (via PR like everything else).
2. Update `CHANGELOG.md` — move Unreleased entries under the new version.
3. Tag + push: `git tag -a v1.x.y -m "v1.x.y — summary" && git push origin v1.x.y`
4. `release.yml` runs tests (hard gate), then publishes the **multi-arch**
   (amd64 + arm64) image to GHCR as `:1.x.y` / `:1.x` / `:1` / `:latest`
   and drafts a GitHub Release.
5. Publish the draft release with the notes — the release titles double as
   the in-app update nudge's upgrade digest (#203), so make them descriptive
   ("v1.5.0 — multi-library + arm64"), not just the bare tag.
6. After a 7-day clean soak, promote to `:stable` (see the full doc).

Telemetry/worker side has its own runbook:
[subarr-telemetry RUNBOOK.md](https://github.com/coaxk/subarr-telemetry/blob/main/RUNBOOK.md).
