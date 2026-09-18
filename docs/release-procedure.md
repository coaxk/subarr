# Release procedure

How a tag goes from "merged to main" to "`:stable` on GHCR for end-users".

## Tiers

The image at `ghcr.io/coaxk/subarr` carries multiple tags by design:

| Tag      | Who it's for                                    | Update cadence    |
|----------|-------------------------------------------------|-------------------|
| `:main`  | Curious users / preview folks                   | Every main push   |
| `:latest`| Newest release (the README's default install)   | Every release tag |
| `:1`     | Auto-track major version                        | Every tag         |
| `:1.1`   | Auto-track minor version (recommended for most) | Every minor tag   |
| `:1.1.0` | Pinned exact version                            | Never re-points   |
| `:stable`| Released, soak-tested, public-trust             | Every promotion   |

Production deployments should follow `:1.1` (minor floats) or `:stable`
(announced releases only). `:main` follows main and ships with in-flight
work — no guarantees. `:latest` moves only when a release tag is pushed
(never on a prerelease such as `v1.0.0-rc.1`), so it is always byte-for-byte
the newest `:X.Y.Z`. Until 2026-09-18 both the main push and the tag push of
a release wrote `:latest`, and whichever build finished last won.

## How a release ships

```
              ┌─────────────────┐
   tag v1.2.0 │  release.yml    │
   pushed  →  │  builds + tests │ → ghcr.io/coaxk/subarr:1.2.0
              │  + GHCR publish │   + :1.2 + :1 + :latest
              └────────┬────────┘
                       │
                       ↓
              ┌─────────────────┐
   7-day      │   rc soak       │
   stabilise  │   on dev stack  │
              └────────┬────────┘
                       │ if no regressions
                       ↓
              ┌─────────────────┐
   manual     │  promote-stable │  → ghcr.io/coaxk/subarr:stable
              │     script      │      now points at v1.2.0
              └─────────────────┘
```

## Step-by-step

### 1. Cut the release

On `main`:

```bash
# pyproject.toml version is the source of truth — bump it first
$EDITOR pyproject.toml
# AND bump src/subarr/__init__.py __version__ to match (it backs the in-app
# version + telemetry). tests/test_version.py fails CI if the two drift —
# 2.3.0 once shipped reporting itself as "2.2.1" because this was missed.
$EDITOR src/subarr/__init__.py

# Commit the bump
git commit -am "release: bump version to 1.2.0"

# Tag + push
git tag -a v1.2.0 -m "v1.2.0 — <one-line summary>"
git push origin main v1.2.0
```

The `release.yml` workflow fires on the tag push and runs (in order):

1. **`test` job** — pytest + frontend drift check. **Hard blocker**:
   if either fails the GHCR push is skipped.
2. **`publish` job** — builds the image and tags it on GHCR with:
   - `:1.2.0` (exact)
   - `:1.2` (minor float)
   - `:1` (major float)
   - `:sha-<short>` (commit ref)
   - `:latest` (only when the same SHA is also on main, which it
     will be for any tag cut from main HEAD)

Before tagging, also:

- Add the `## [x.y.z] - YYYY-MM-DD` section to `CHANGELOG.md`. `release.yml`
  derives the GitHub Release title from its first bolded phrase via
  `scripts/changelog_section.py`. **An empty title blanks the #203 update
  nudge fleet-wide**, so dry-run it first:
  `python scripts/changelog_section.py vX.Y.Z --body-out /tmp/notes.md`
- Refresh the README badges (status version + the real passing count from a
  local full run — the *passing* count, not the collected one).

### 1b. Verify the published artifact

The workflow going green is not the same as the image being correct. After
`release.yml` completes, check the thing users actually pull:

```bash
# multi-arch present?
docker manifest inspect ghcr.io/coaxk/subarr:1.2.0 | grep architecture

# does it report its own version correctly? (the 2.3.1 trap)
docker run --rm --entrypoint python ghcr.io/coaxk/subarr:1.2.0 \
  -c "import subarr; print(subarr.__version__)"

# did the security patches actually land? (the APT_REFRESH trap, below)
docker run --rm --entrypoint dpkg ghcr.io/coaxk/subarr:1.2.0 -l | grep libssl
```

Announcements go out at this point, once the image is confirmed pullable —
not at `:stable` promotion, which is a separate and quieter step.

### 2. Soak

A new release lives at `:1.2.0` / `:latest` for at least **7 days**
before promotion. During soak:

- Watch GHCR pull stats for early adopters
- Watch the `coaxk/subarr` GitHub issues for regression reports
- Test the dev stack against the new image — verify:
  - Coverage walk completes
  - Subgen dispatch + completion watcher still wires correctly
  - Bazarr scan-disk + provenance ledger keep recording
  - Audio-lang review queue verify-and-propagate path works
  - Settings → Subgen still surfaces per-language kwargs

If a real regression surfaces, **do not promote**:

- Hotfix on a follow-up patch tag (`v1.2.1`), restart soak
- Or revert the offending PR on main, hotfix patch tag
- Add an entry to `CHANGELOG.md` under the patch version

### 3. Promote

After 7 clean days, retag the image to `:stable` with the workflow:

```bash
gh workflow run promote-stable.yml -R coaxk/subarr --ref main -f version=1.2.0
```

It uses `docker buildx imagetools create`, which copies the whole multi-arch
index. Do NOT `docker pull` + `docker tag` + `docker push`: that pushes only
the puller's architecture, so `:stable` silently loses arm64 (see the header of
`promote-stable.yml`). Afterwards, check the artefact rather than the run:
`:stable` and `:1.2.0` must have the same digest on GHCR.

Then:

- Write a one-sentence announcement in `CHANGELOG.md`'s `## [Unreleased]`
  section noting the promotion date
- Bump the README badges if needed (`release-v1.2.0-violet`)
- Post the cut to whatever channels we're using (Reddit, etc.) — this
  is the moment a release stops being "for early adopters"

### 4. Yank protocol

If something serious surfaces AFTER promotion:

1. Determine if a hotfix is available or pending — if yes, cut it
   and **promote the hotfix** to `:stable` immediately.
2. If no hotfix yet and the regression is severe, point `:stable`
   back at the previous known-good version using the same retag
   procedure (`docker pull ghcr.io/coaxk/subarr:1.1.x && docker tag
   ... :stable && push`). Users following `:stable` get the rollback
   on their next `compose pull`.
3. Add a yank entry to `CHANGELOG.md` describing what failed and
   what the user should do.

For subgen-side yanks (the patch quilt itself), see
[subarr-subgen RELEASES.md](https://github.com/coaxk/subarr-subgen/blob/main/RELEASES.md).

## Build invariants (do not "clean these up")

**`ARG APT_REFRESH` in the Dockerfile, and the `APT_REFRESH=<UTC date>`
build-arg in `security.yml` + `release.yml`, are load-bearing.** Both container
builds use `cache-from: type=gha`. Without a value that changes daily, the apt
layer is a cache hit for days, so neither `apt-get update` nor
`apt-get upgrade -y` re-runs and **Debian security patches silently never reach
the published image**. The `echo "apt-refresh=${APT_REFRESH}"` line looks like
dead weight; it is what makes the arg actually invalidate the layer.

Found 2026-07-21 via trivy on libtiff CVE-2026-12912: the cached layer held
`+deb13u2` while `+deb13u3` had been published. The earlier Mesa fix only
worked by accident, because reordering the `RUN` changed its text.

Symptom to recognise: trivy fails on a CVE that is *fixed upstream*, and a
local `docker build` of the same Dockerfile installs the patched version fine.
That gap between local and CI is the cache, not the CVE.

## Why this procedure

- **Tests gate**: the `test` job blocks publish, so a broken main HEAD
  can't accidentally push to GHCR.
- **Soak time**: 7 days between tag-cut and `:stable` lets in-the-wild
  regressions surface before they reach the audience that asked for
  stability over freshness.
- **Three-tier tag floats**: lets users self-select their risk
  tolerance (`:stable` vs `:1.1` vs `:latest`) without us having to
  branch.

The cost is one extra retag step per release. Worth it for the
"`:stable` actually means stable" contract.
