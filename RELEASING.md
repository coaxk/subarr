# Releasing

The full procedure (tag tiers, soak, `:stable` promotion, yank protocol)
lives in [docs/release-procedure.md](docs/release-procedure.md). Short form:

1. Bump `pyproject.toml` version on `main` (via PR like everything else).
2. Update `CHANGELOG.md` — move Unreleased entries under the new version.
3. Tag + push: `git tag -a v1.x.y -m "v1.x.y — summary" && git push origin v1.x.y`
4. `release.yml` (workflow name `build-and-publish`) runs tests (hard gate),
   then builds + pushes the **multi-arch** (amd64 + arm64) image to GHCR as
   `:1.x.y` / `:1.x` / `:1` / `:latest`, and finally **auto-creates the GitHub
   Release** (#229). The `release` job derives a descriptive title from the
   `CHANGELOG.md` section for the tag (`vX.Y.Z — <first bold phrase>`) and uses
   that section as the body. This is what feeds the #203 update nudge, so it
   must be non-empty — the job guarantees a usable title even if the changelog
   section is missing. Idempotent: it skips if a release already exists.
   - **Therefore: get the `CHANGELOG.md` entry right (step 2).** The title +
     body come straight from it. The first **bold** phrase in the section
     becomes the title suffix, so lead the section's notable line with a bold
     phrase like `**Guided subgen setup.**`.
5. **Verify the release landed** (no manual create needed anymore):
   `gh api repos/coaxk/subarr/releases --jq '.[0] | .tag_name + " " + .name'`
   should show your derived title within a minute, not an empty string.
6. After a 7-day clean soak, promote to `:stable` (see the full doc).

Telemetry/worker side has its own runbook:
[subarr-telemetry RUNBOOK.md](https://github.com/coaxk/subarr-telemetry/blob/main/RUNBOOK.md).
