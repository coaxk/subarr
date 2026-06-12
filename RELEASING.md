# Releasing

The full procedure (tag tiers, soak, `:stable` promotion, yank protocol)
lives in [docs/release-procedure.md](docs/release-procedure.md). Short form:

1. Bump `pyproject.toml` version on `main` (via PR like everything else).
2. Update `CHANGELOG.md` — move Unreleased entries under the new version.
3. Tag + push: `git tag -a v1.x.y -m "v1.x.y — summary" && git push origin v1.x.y`
4. `release.yml` (workflow name `build-and-publish`) runs tests (hard gate),
   then builds + pushes the **multi-arch** (amd64 + arm64) image to GHCR as
   `:1.x.y` / `:1.x` / `:1` / `:latest`. **It does NOT create a GitHub
   Release** — that is a manual step (5), and skipping it is silent: the
   tag still appears in `releases.atom` but with an empty title, so the
   #203 update nudge shows blank entries to the whole fleet. (This bit us
   on 1.5.2–1.5.4; see #229.)
5. **Create the GitHub Release yourself** from the changelog section:
   ```bash
   gh release create v1.x.y --title "v1.x.y - <descriptive summary>" \
     --notes-file <notes.md> --latest
   ```
   The release **title** doubles as the in-app update nudge's upgrade digest
   (#203), so make it descriptive ("v1.5.0 - multi-library + arm64"), not the
   bare tag. Verify it landed: `gh api repos/coaxk/subarr/releases --jq
   '.[0] | .tag_name + " " + .name'` should show your title, not an empty
   string.
6. After a 7-day clean soak, promote to `:stable` (see the full doc).

Telemetry/worker side has its own runbook:
[subarr-telemetry RUNBOOK.md](https://github.com/coaxk/subarr-telemetry/blob/main/RUNBOOK.md).
