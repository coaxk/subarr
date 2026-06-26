# subarr

The coordination layer for the *arr subtitle stack. Stands beside Bazarr.

Subarr decides what subtitles are actually missing across your library, which providers are worth your time, and when it is worth running Whisper. Bazarr finds and downloads. Subgen transcribes. Subarr coordinates.

[![status](https://img.shields.io/badge/status-v2.2-violet)](https://github.com/coaxk/subarr)
[![tests](https://img.shields.io/badge/tests-1159_passing-22d3ee)](https://github.com/coaxk/subarr/actions/workflows/ci.yml)
[![security](https://img.shields.io/badge/Bandit_%2B_Semgrep_%2B_Trivy_%2B_pip--audit-22c55e)](#security)
[![license](https://img.shields.io/badge/license-MIT-c8c8cc)](LICENSE)

> Built with AI assistance from Claude. Code is open, every PR is human-reviewed. Telemetry, security scans, and a published test count are how we stay honest about that.

![Subarr in action](docs/hero.gif)

---

## New in 2.2

Filling more gaps, finding more controls, and a deep reliability pass. Non-breaking — upgrades transparently.

- **Blacklist a bad sub without leaving subarr.** When a provider sub is broken, blacklist it from Aftercare or the Library tree and Bazarr stops re-fetching that release. A shared panel shows the file's Bazarr download history and blacklists the offending provider sub through Bazarr's own API.
- **Transcribe a full sub on forced-only files.** A file whose only English sub is *forced* (foreign-dialogue-only) used to sit in a "subgen will skip" bucket. Now each row has a **Transcribe full sub** button that generates a complete subtitle for just that file, without flipping subgen's global forced-subs setting. Needs the matching subgen image (`ghcr.io/coaxk/subarr-subgen:2026.05.3-r10`+); older subgen keeps the old guidance.
- **Per-title ignore.** Tell subarr "I don't want subs here" inline on a Library row or from Review — suppress a whole show or a single file.
- **A home for the scattered controls.** A new **Other subtitle controls** card on the Rules page signposts every force/ignore/language control to where it lives, so you can find them as a set.
- **Back up your database on demand.** A Health-page card writes a clean, defragmented, timestamped copy and runs a deep integrity check — with a callout that nudges you to back up before changes if integrity ever looks off.
- **The UI no longer freezes under load.** A dogfood-reported stall traced to per-poll filesystem work on the event loop; that work moved off-loop and a stall monitor stays in. Plus durability, Plex-client, queue-reconciliation, telemetry, and auth hardening from heavy real-world use. Full list in the [changelog](CHANGELOG.md).

## New in 2.0

A security-hardening + activation release. The two headline changes are breaking for some deployments — see [Upgrading](#upgrading-to-20) below.

- **Authentication is on by default.** subarr's API can drive Sonarr, Bazarr, and subgen, so a default install no longer ships wide open. First launch creates an admin account, then it's a normal login + signed session cookie. Recovery is built in (env override, `SUBARR_AUTH_RESET=1`, or `docker exec subarr python -m subarr.cli reset-auth`), failed logins are rate-limited, and you can mint named **managed API keys** for scripts. Already authenticating at a reverse proxy? `SUBARR_AUTH_DISABLED=1` hands auth back to it. Full detail under [Security → Authentication](#authentication).
- **Runs as a non-root user.** The container drops to `PUID`/`PGID` (default `1000:1000`); its entrypoint starts as root only long enough to fix ownership of **its own data dir** (never your media library) and grant docker-socket access, then drops privileges before the app runs. `PUID`/`PGID` are now real, not decorative. Hardened-compose users need a small `cap_add` set — see [Hardened deployment](#hardened-deployment-optional).
- **Pause/resume the schedule from the dashboard.** The "Next scheduled run" card gains a Pause/Resume button next to Run now — halt or restart automation without opening Rules.
- **You land on a populated dashboard.** Finishing onboarding now auto-runs the first coverage walk (when an arr is configured), with a clear empty-state as the safety net for anyone who still lands walk-less.

*Previously: guided subgen setup, aftercare score-explainers, and documented Swagger/OpenAPI at `/docs` in 1.6; multi-library, arm64 images, and fleet crash telemetry in 1.5; Job Aftercare and queue authority in 1.4; the Tuning Lab and audio-language verification in 1.2; speech-aware audio (silero VAD) in 1.1. See the [changelog](CHANGELOG.md) for the full history.*

### Upgrading to 2.0

- **You'll see a one-time login screen.** Create an admin account on first launch after upgrading. Installs that already set `SUBARR_USER`/`SUBARR_PASS` or `SUBARR_API_KEY` are **not** forced into setup — those keep working. Locked out? `SUBARR_AUTH_RESET=1`, the env pair, or `docker exec subarr python -m subarr.cli reset-auth`. Behind a reverse proxy that authenticates? `SUBARR_AUTH_DISABLED=1`.
- **Hardened-compose users:** the container now runs non-root and reconciles ownership of **its own data dir** at boot — the volume holding `SUBARR_DB_PATH` (default `/data`), **never the media library** (a separate mount subarr treats as foreign data you own). Add `cap_add: [CHOWN, SETUID, SETGID, FOWNER, DAC_OVERRIDE]` alongside your `cap_drop: [ALL]` and set `PUID`/`PGID` to the uid that owns that data dir **and** your media mounts (subarr writes sidecars there). See [Hardened deployment](#hardened-deployment-optional). The LaBSE QE cache lives under the data dir (`<data>/.cache/huggingface`) — drop any old `/root/.cache` mount.
- **Everything else upgrades transparently** — existing installs keep working and subarr's own data dir is reconciled automatically. Keep `SUBARR_DB_PATH` on a dedicated subarr volume (e.g. `/data` or `/config`); never point it at the media tree.

---

## In one breath

- **See your whole library's subtitle coverage at a glance.** Per-language gap view across Sonarr + Radarr + Bazarr, with audio language we trust.
- **We verify before we call it a gap.** A row only becomes an actionable gap once subarr has actually probed the file — so it never queues something that already has an embedded sub subgen would skip. Un-probed files wait in a visible "Analyzing" bucket, not silently dropped or falsely surfaced.
- **Calibrated audio language detection.** Three Whisper chunks across the file, conservative voting, confidence-gated. Cheap to skip files Whisper would hallucinate on.
- **We don't parrot the metadata, we verify it.** Subarr listens to the actual audio and tells a mislabeled track from a bilingual one from "genuinely unsure", then offers a one-click fix that flows back into coverage. Beside Bazarr, never instead of it.
- **Tune Whisper to your hardware.** The Tuning Lab sweeps recipe variants against your live subgen, a validated judge ranks them, and a per-language leaderboard surfaces the dependable default for each language.
- **Don't burn GPU on content nobody watches.** Scheduled walks with backpressure. Tautulli playback signal influences priority.
- **Provenance ledger.** Which provider gave you which sub, when, why. Survives re-search runs.
- **Embedded subs are first-class.** SDH, forced, PGS, full, all distinguished, not collapsed.

## Five-minute install

```yaml
# compose.yaml
services:
  subarr:
    image: ghcr.io/coaxk/subarr:latest
    container_name: subarr
    restart: unless-stopped
    ports:
      - "9922:9922"
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
      - UMASK=022
      - SUBARR_DB_PATH=/data/subarr.db     # SQLite + persisted settings live here
    volumes:
      - ./subarr/data:/data                # REQUIRED — your verifications + settings live here.
                                           # Without a persistent volume, everything is wiped on
                                           # every recreate (and subarr will warn you on boot).
      - /path/to/media:/media/library:rw   # same path Bazarr and subgen see
```

```bash
docker compose up -d
# Open http://localhost:9922, onboarding wizard auto-detects your stack.
```

> **Two choices that matter, everything else the wizard defaults sensibly:**
> 1. **Which subgen.** Use `ghcr.io/coaxk/subarr-subgen` for the full feature set (queue + cancel, the Tuning Lab, per-request language overrides, calibrated multi-chunk detection). Stock `mccloud/subgen` also works in compat mode with fewer features. Details in [I already have subgen](#i-already-have-subgen-what-do-i-do) below.
> 2. **GPU.** If you have an Nvidia GPU, pass it to your subgen container so you can run a larger Whisper model (`large-v3`). This is the single biggest lever on subtitle quality. CPU works, just slower.

The wizard tries to auto-detect Sonarr/Radarr/Bazarr/Tautulli/subgen on your existing Docker network and prefills URLs. Manual entry is available at every step as a safety net. Auto-detect plus manual fallback at every step is the design rule.

After onboarding you can edit any integration's URL and API key (and the Plex token) directly in Settings, with test-connection and live apply. Values you set via env vars stay authoritative and show as read-only.

**Why `:rw` on the media mount.** Subarr's sidecar mismatch detector renames orphaned `.srt` files whose basename drifted from the video. Read-only blocks this. If you don't want it, set `SUBARR_SIDECAR_RENAME=0` and mount `:ro`, the rest of the product works.

### Hardened deployment (optional)

Subarr runs as a **non-root** user (default `1000:1000`, set yours via `PUID`/`PGID`). Its entrypoint starts as root only long enough to fix data-dir ownership and drop privileges, so the app process itself runs unprivileged. That boot step needs a small, fixed set of capabilities — drop everything else:

```yaml
    cap_drop: [ALL]
    cap_add: [CHOWN, SETUID, SETGID, FOWNER, DAC_OVERRIDE]
    security_opt:
      - no-new-privileges:true
    environment:
      PUID: 1000   # match the uid that owns your /data + media
      PGID: 1000
    deploy:
      resources:
        limits:    { cpus: '1.0', memory: 1G }
        reservations: { cpus: '0.25', memory: 256M }
```

The five caps let the entrypoint reconcile ownership of **subarr's own data dir** — the volume holding `SUBARR_DB_PATH` (default `/data`), so an existing root-owned database stays writable after upgrade — and drop to `PUID/PGID`; the running app then holds **no** capabilities and is non-root — a stronger posture than the old root-with-everything default. It does **not** chown your media library: that's a separate mount (e.g. `/media/library`) subarr only writes sidecar `.srt` files into, so **you** must own it (set `PUID` to match; in multi-library setups every media mount must be writable by `PUID`). The LaBSE QE model caches under the data dir (`<data>/.cache/huggingface`, so it persists) — if you previously mounted a volume at `/root/.cache`, you can drop it.

The image already ships a `HEALTHCHECK` (hits `/api/health`), so Compose and orchestrators get container health for free — no `healthcheck:` block needed. The login is on by default (see [Security](#authentication)); add an `SUBARR_API_KEY` only if scripts or other automation need non-browser access.

**Plex (optional).** Set `PLEX_URL` + `PLEX_TOKEN` (and optionally `PLEX_SECTION`) to enable two things: an instant Plex library refresh the moment subarr writes a sub (instead of waiting for Plex's own periodic scan), and the opt-in per-show audio-language read (`PLEX_AUDIO_HINTS=1`). Plex shows in the dashboard + Settings integration health either way, so you can see its status at a glance. Activity/now-playing still comes through Tautulli.

## Two ways to use subarr

Pick whichever fits how you work. You can do both.

**Simple, "I just want a real frontend for subgen".** Install subarr, open the Library tab, tick a file or a folder or a whole series, hit "Queue for transcription". Watch it run. Re-queue, cancel, see what failed and why. Same way you'd use Sonarr's queue for downloads. No coverage walks, no rules, no scheduler. Just a working UI on top of subgen.

**Advanced, "tell me what I should fix first".** Open the Coverage tab. Subarr has already walked your library and sorted gaps by score with reason chips per row (no track, embedded-only, bazarr-wanted, audio-mislabel, low-score, unmonitored). Apply auto-queue rules, run scheduled walks, integrate Tautulli playback signal into priority. Set it up once, walk away. Subarr decides what's worth running.

Most installs start simple and grow into advanced as the coverage walk surfaces things worth doing. Nothing forces the move; both are valid forever.

## I already have subgen. What do I do?

The most-asked question. Quick answer.

| You have | What to do |
|---|---|
| Vanilla `mccloud/subgen` | Keep it. Add subarr next to it. Subarr detects vanilla and runs in compat mode. Coverage, provenance, scheduling, audio-language review all work. You miss calibrated multi-chunk detection and queue cancel, both require our subgen patches. |
| `mccloud/subgen` and you want everything | Swap to `ghcr.io/coaxk/subarr-subgen`. Same upstream image plus 22 small auditable patches. Pull, change one line in your compose, restart. No data loss, no config rewrite. |
| No subgen yet | Start with `ghcr.io/coaxk/subarr-subgen`. Everything works on day one. |
| You run Bazarr only | Subarr adds a coordination layer beside Bazarr. Bazarr keeps doing what it does. Subarr surfaces what is actually missing, schedules the work, and writes results back. |

You do not need to decide at install. Subarr re-probes subgen every 30 seconds and adopts new capabilities the moment you upgrade.

## Do you need subarr?

Skip subarr if any of these are true:

- Your library is single-language and you have never had a wrong-language subtitle land.
- You use one or two providers and never wonder which one delivered what.
- You don't run Whisper or any local transcription, and don't plan to.

Subarr's value compounds with: multi-language libraries, three or more Bazarr providers, Whisper-in-the-loop, and a habit of asking "why did Bazarr re-search this?"

## What's in subarr

| Surface | Function |
|---|---|
| Dashboard | Live column-as-stage pipeline (discovered → probing → bazarr-wanted → transcribing → written-back), GPU widget, integration health, next scheduled run, recent activity |
| Coverage | Scored gap list (tree-by-show or flat), score-gradient sort, reason chips (no-track, embedded-only, bazarr-wanted, audio-mislabel, low-score, unmonitored). **Probe-gate:** only files subarr has verified appear as gaps; un-probed files sit in a sticky "Analyzing" bucket (with a Probe-now action) and "Couldn't analyze" surfaces failures — nothing silently dropped. Bulk select + apply rule + queue. **(2.2) Transcribe a full sub** on forced-only files (an embedded sub that only covers foreign dialogue) without flipping subgen's global forced-subs knob |
| Library | Tree across all series and movies. Audio / sub / runtime columns with probe-state indicators. **(2.2) Per-title ignore** inline (suppress a whole show or one file) and **blacklist a bad provider sub** on any video |
| Queue | Featured Queue: Processing, Queued, Lost-on-restart, Issues, Recently done. Per-row and **bulk** requeue / remove / cancel (multi-select across every section). **Pending backlog** with **step-wise reorder + pause/resume + target-depth** — subarr holds its own queue in front of subgen and feeds it at a set depth instead of flooding. **Every submission routes through it** (1.4) — manual scans and requeues included — so nothing stampedes subgen; manual still jumps the line and starts near-instantly. **Backfill gaps** drains the whole verified-gap backlog at low priority |
| Review | Manual audio-language verification queue with audio player, multi-track support, batch cycle, Layer 3 Whisper detection inline. **Default-track mismatch (1.4):** flags files whose default audio is not the original language (the double-translation trap) with a one-click in-place track swap (`mkvpropedit`) or dismiss, single or bulk. **Speech-aware clip selection (1.1):** the player lands on actual dialogue via silero VAD, with a speech-detected badge |
| Aftercare | **(1.4)** Post-transcription quality review: every finished job is judged for failures + readability and surfaced (page + header pill + dashboard panel) with a country flag, language, source tag, composite score, and a legend. Requeue from the row, or **blacklist a bad provider sub (2.2)** straight to Bazarr. Flags problems, never a confident grade |
| Rules | Auto-queue rules with score thresholds, language filters, custom-format pre-classification. **(2.2)** An "Other subtitle controls" card signposts every force / ignore / language control to the page where it lives |
| Tuning Lab | Config arena: sweep Whisper recipes against your live subgen, judged by a validated tournament judge across multiple strata clips. Per-language herd view, global recipe leaderboard, and an Audio language issues panel surfacing mislabeled / bilingual / multi-track files from on-demand sweeps and the opt-in library-wide scan |
| Settings | Per-language Whisper kwargs, **in-app integration editing** (URLs + API keys + Plex token, test-connection + live apply, env-set fields stay read-only), integrations health, system actions, telemetry transparency panel showing the exact JSON last sent. **Speech-aware audio:** enable/disable + download the silero model |

### About ollama (optional, recommended)

Subarr does not require ollama. With it, you get two extras:

- **Structured enrichment.** Vague Bazarr wanted entries get classified by language, genre hints, dialog density. Improves prioritisation. Works with any text model.
- **Vision pre-filter.** A vision-capable model classifies Tautulli thumbnails as dialog-heavy / music-heavy / visual-only. Suppresses transcribe submissions where Whisper would hallucinate.

Vision and text models are separate (`OLLAMA_MODEL` and `OLLAMA_VISION_MODEL`). Default vision model is `qwen2.5vl:7b`. Subarr auto-detects any installed model from `qwen2.5vl`, `qwen2-vl`, `llama3.2-vision`, `llava`, `bakllava`, `minicpm-v`, `moondream`. Without a vision-capable model the pre-filter is cleanly disabled, not silently broken. Settings shows the active state.

## Screens

Real library, real foreign-language content — nothing staged.

**Dashboard** — live pipeline (discovered → probing → bazarr-wanted → transcribing → written-back), GPU, integration health, next scheduled run, recent activity.

![Dashboard](docs/screenshots/01-dashboard.png)

**Coverage** — the scored gap list with the probe-gate: verified gaps in the table, un-probed files held in "Analyzing", every explainer panel inline.

![Coverage](docs/screenshots/02-coverage.png)

**Queue** — a real frontend for subgen: Processing / Queued / Lost-on-restart / Issues, with per-row and bulk requeue · remove · cancel.

![Queue](docs/screenshots/03-queue.png)

**Library** — every series and movie with audio / sub / runtime + probe-state.

![Library](docs/screenshots/04-library.png)

**Review** — manual audio-language verification with an audio player, multi-track support, and inline Whisper detection. In 1.1 the clip lands on actual dialogue (silero VAD), not dead air.

![Review](docs/screenshots/05-review.png)

**Tuning Lab** — sweep Whisper recipes against your live subgen; a validated judge ranks them across multiple clips, with plain-language guidance and per-clip winners. Nothing is written to your library.

![Tuning Lab](docs/screenshots/10-tuning-lab.png)

**Recipe leaderboard** — every recipe's per-language results rolled into one overall ranking (mean of per-language means, so each language counts equally). Medals for the top three, a confidence signal, and an expandable per-language breakdown.

![Recipe leaderboard](docs/screenshots/11-leaderboard.png)

**Audio language issues** — subarr listened and disagreed with the tag: mislabeled, bilingual, and multi-track files flagged in one place, from on-demand sweeps and the opt-in library-wide scan. One click to review and confirm.

![Audio language issues](docs/screenshots/12-audio-issues.png)

**Rules** — auto-queue policy with score thresholds and language filters, plus a live "what would queue right now?" preview.

![Rules](docs/screenshots/06-rules.png)

**Settings — Integrations** — live online / version / badges per service.

![Settings — integrations](docs/screenshots/07-settings-integrations.png)

**Settings — Telemetry** — full transparency: install ID, opt-out, and the exact JSON last sent.

![Settings — telemetry](docs/screenshots/08-settings-telemetry.png)

**Logs** — structured, filterable runtime logs streamed live from subgen's container. This view (and the "restart subgen" button) needs **Docker socket access** — bind-mount `/var/run/docker.sock` (read-only is fine) into the subarr container, or use the [socket proxy](#architecture) from Tier 2 below. Without it, the Logs page shows a clear "can't reach Docker" notice with this fix, and the rest of subarr works normally.

![Logs](docs/screenshots/09-logs.png)

## How calibrated audio detection works

Vanilla subgen samples one 30-second window at the start of a file and trusts whatever Whisper says. That window is silent, intro music, or a foreign-language opening narration as often as not. Anime is the canonical failure case: an English-dub episode whose first 30 seconds are the Japanese OP gets transcribed in Japanese, the user gets garbage, nobody knows why.

Subarr's audio-language pipeline:

```
  L1  file metadata          ffprobe audio_language tag.
                             Cheap, often wrong on retags.

  L2  Tautulli signal        Which audio track is your household
                             actually picking when they watch?

  L3  Whisper robust detect  Sample 3 chunks across 10 / 50 / 90 percent
                             of the file. Vote by majority. Confidence
                             is the MINIMUM probability across the
                             agreeing chunks, one high-confidence
                             chunk cannot mask a disagreeing one.

  L4  user verification      Review queue surfaces every suspect row.
                             One click confirms, propagates to Sonarr
                             so Bazarr stops getting blinded.
```

Once a verification exists, every downstream submission carries it through an evidence gate. Confidence below 0.5, or missing source field, refuses to forward the override. Whisper transcribes from the audio, the way it was meant to.

## Common questions

**Is this just for anime?** No. The audio-language detection problem hits anything where the first 30 seconds of a file aren't representative: foreign-language openings on dubbed releases, silent cold opens, music-only intros, opening narrations in a different language than the dialog. Anime gets cited a lot because the OP pattern is universal across the genre, but the technical problem is general across multi-language libraries. Coverage, scheduling, provenance, and the queue UI are all language-and-genre-agnostic.

**Do I need ollama?** No. It enables two optional extras (structured enrichment and the vision pre-filter). Everything else works without it.

**Do I need Tautulli?** No, but you get NOW PLAYING boost, just-imported boost, and per-user language profiles if you have it. Without Tautulli the scheduler still works, it just has one fewer priority signal.

**Will this work with Jellyfin / Emby?** Not yet — a candidate if there's demand. Open a feature request.

## Multiple media locations (libraries)

One media root covers most setups, but if your library spans disjoint mounts — `/mnt/disk1/Movies` here, `/mnt/disk2/TV` there, a 4K library on its own share — subarr models each location as a **library**: a filesystem root (subarr's view), the prefix subgen sees it at, and the path prefix Sonarr/Radarr report it under.

- **The default library is your existing config** — `SUBARR_MEDIA_ROOT` / `SUBGEN_MEDIA_PREFIX` / `ARR_PATH_PREFIX`. A single-location install needs nothing new; nothing changes.
- **Extra libraries live in the UI** — Settings → Libraries (also offered during onboarding). Subarr reads your Sonarr/Radarr root folders and suggests any location not yet covered as a one-click "Add as library"; a manual add form covers anything auto-detect misses. Each path validates with a live reachability sample before saving, and changes apply immediately — no restart, no env vars.
- **Mount each location in subarr AND subgen** (mirrored on the *arr side), e.g.:

```yaml
# subarr
volumes:
  - /mnt/nas/Media:/media/library          # default library
  - /mnt/disk2/Movies4K:/media/disk2       # extra library (fs root: /media/disk2)

# subgen
volumes:
  - /mnt/nas/Media:/media                  # default library's subgen prefix
  - /mnt/disk2/Movies4K:/media2            # extra library's subgen prefix
```

Internally, extra libraries qualify their file keys with a stable `@<id>/` head while the default library keeps today's keys — which is why existing installs upgrade with zero migration. The simple union-mount workaround (binding several host paths under one container root) still works fine if you prefer it.

## Known limitations (v2.2)

Transparent before you install.

- Requires `ghcr.io/coaxk/subarr-subgen` for calibrated Layer 3 detection, queue cancel, curated per-language `initial_prompt`s, and the safe-decode preset. Vanilla subgen works in compat mode but you miss these.
- The default-track swap needs `mkvtoolnix` (`mkvpropedit`) in the runtime image — it ships in `ghcr.io/coaxk/subarr`; detection + the Review UI work regardless, the swap action just needs the binary present.
- Single-admin authentication. subarr ships a real built-in login (forced by default, with sessions, throttling, and managed API keys), but it's one admin account — no per-user accounts, roles, or audit. For multi-user, put it behind a reverse proxy (Authelia / Caddy / Traefik) and set `SUBARR_AUTH_DISABLED=1`.
- Auto-update is intentionally absent. Update notifications appear in the UI; you run the upgrade.
- Plex activity signal goes through Tautulli (the bridge). Reading a show's *selected* audio language straight from Plex metadata is an opt-in extra (`PLEX_AUDIO_HINTS=1`), off by default.
- Multi-episode disc images (a single `.iso` holding a whole season) can't be probed per-episode, so they're surfaced in a distinct "Couldn't analyze" (unsupported) bucket rather than becoming verified gaps or sitting in "Analyzing" forever. Standard per-episode files are unaffected.
- SQLite only. No Postgres backend.
- Single-host. Workers / multi-host are an explicit non-goal until users ask.
- Jellyfin / Emby are not yet supported.
- Compose example uses bind mounts. Named volumes work but you lose the "same path Bazarr and subgen see" sanity.

## Backing up your data

Everything subarr knows lives in two files on the `/data` volume, and some of it is genuinely irreplaceable:

- **`/data/subarr.db`** — the database. Contains your **audio-language verifications** (every "I listened and confirmed this track" click — hours of your judgment that cannot be regenerated), series language intents, the provenance ledger (which provider gave you which sub, when), Tuning Lab history, scan history, and the install's telemetry identity.
- **`/data/subarr-overrides.json`** — settings changed from the UI (credentials, libraries, toggles).

What to do about it:

- **Include `/data` in whatever backup tool you already run** (restic, borg, Backrest, duplicati — anything). Probe data and coverage rebuild themselves; verifications do not.
- For a consistent live copy of a running instance: `docker exec subarr sqlite3 /data/subarr.db ".backup /data/subarr-backup.db"`, then pick up the backup file. Copying `subarr.db` while subarr is writing can produce a torn copy (WAL); stopping the container first also works.
- **Or do it from the UI (2.2):** the **Health page** has a **Back up database now** button that writes a clean, defragmented, timestamped copy into `/data/backups/` (atomic `VACUUM INTO`, the 5 most recent kept), plus a **Run full integrity check** for a deep `PRAGMA integrity_check` — no shell needed. Include `/data` in your off-box backup either way.
- **Keep `/data` on a local disk, not NFS/SMB.** SQLite in WAL mode over network filesystems is a well-known corruption hazard. Your media library on NAS is fine — that's read-mostly; the database is not.
- Subarr runs an integrity check (`PRAGMA quick_check`) on every boot. If your database is damaged you'll see it on the **Health page** and the red header pill — back up `/data` immediately at that point, before anything else writes.
- **Make sure `/data` is an actual volume.** If you run without one (a bare container, or you removed the volume line), every `docker compose up` starts from an empty database — all your verifications gone, and a brand-new install each time. Subarr detects this on boot and flags it loudly on the Health page; if you see that warning, add a volume for `/data` before you do anything else.

## Security

### Authentication

Subarr's API can mutate Sonarr, trigger Bazarr tasks, edit your library roots, and restart subgen — so it **requires authentication by default** (matching modern Sonarr/Radarr).

- **First-run setup.** On first launch (or first launch after upgrading from a no-auth version) subarr shows a one-time screen to create an admin username + password. After that, a normal login page + session cookie. Existing installs that already set `SUBARR_USER`/`SUBARR_PASS` or `SUBARR_API_KEY` are **not** forced into setup — those credentials keep working, so cron/scripts don't break on upgrade.
- **Locked out? Three recovery paths** (none need DB surgery):
  1. **Env override** — set `SUBARR_USER` + `SUBARR_PASS` in your compose and restart; that pair always logs in.
  2. **Reset** — set `SUBARR_AUTH_RESET=1` and restart to clear the stored credential and return to the setup screen.
  3. **CLI** — `docker exec subarr python -m subarr.cli reset-auth` (or `set-password --username admin --password '…'`).
- **Behind a reverse proxy that already authenticates** (Authelia / Caddy / Traefik forward-auth)? Set `SUBARR_AUTH_DISABLED=1` to turn subarr's built-in login off and let the proxy own auth (no double login). A non-matching upstream `Authorization: Basic` header is ignored, never rejected, so chained proxies don't break subarr.
- **Sessions persist across restarts automatically** — the signing secret is stored in subarr's database, so a container restart or update no longer logs you out. Set `SUBARR_SESSION_SECRET` only if you want to pin it explicitly (e.g. to share one secret across replicas); leaving it unset is fine. Embedding subarr in a cross-site dashboard iframe (Organizr/Homer)? Set `SUBARR_COOKIE_SAMESITE=none` (requires HTTPS).
- **Session expiry is handled gracefully** — if your session lapses, the next API call shows a brief "session expired" notice and sends you to the login page (with a `?next=` back to where you were), instead of dead clicks. The login page also has a **"Forgot your password?"** panel spelling out the CLI / env recovery paths.
- **Brute-force throttle** — failed sign-ins are rate-limited per client IP (default: `SUBARR_LOGIN_MAX_ATTEMPTS=5` failures per `SUBARR_LOGIN_WINDOW_S=300` seconds, then a short wait — never a permanent lockout). Two optional CIDR lists tune it, set in your compose:
  - `SUBARR_TRUSTED_PROXIES` — your reverse proxy's IP/range, so the throttle keys on the **real client IP** from `X-Forwarded-For` instead of the proxy's address. Only XFF from these hops is trusted; a spoofed header from anywhere else is ignored.
  - `SUBARR_LOGIN_ALLOWLIST` — IPs/ranges that **never** get throttled (your LAN, an automation box).
  - Both default empty. The effective values are shown read-only under **Settings → Login security**.
- **API key** (`SUBARR_API_KEY`) and **HTTP Basic** (`SUBARR_USER`/`SUBARR_PASS`) remain accepted principals for automation/recovery, sent as `X-Api-Key`/`?apikey=` or a Basic header.
- **Managed API keys.** Beyond the single env `SUBARR_API_KEY`, you can mint named keys in **Settings → API keys** for scripts and integrations. Each has full access and is shown **once** at creation (copy it then — it's stored only as a SHA-256 hash, never retrievable). Send it as `X-API-Key`/`?apikey=` just like the env key; revoke any key instantly from the same panel. The list shows when each key was last used, so stale keys are easy to spot.
- **CSRF protection** is on by default: cross-origin browser writes to `/api/*` are rejected. Non-browser clients (curl, the subgen webhook) are unaffected. Set `SUBARR_CSRF_PROTECTION=0` only if a trusted automation client trips it.

### Posture

- Bandit, Semgrep, pip-audit, Trivy, CodeQL, and zizmor (GitHub Actions auditor) run on every push to `coaxk/subarr`; the same gate set (minus pip-audit, which is N/A — subgen ships no pip package) runs on `coaxk/subarr-subgen`. SARIF uploads to the GitHub Security tab.
- Constant-time auth comparison (`secrets.compare_digest`). Regression tested.
- API keys never appear in any HTTP response, masked surface, raw key only in dataclass internals. Regression tested.
- Every filesystem operation routes through `canonical_to_fs()` which rejects path-traversal outside the configured media root. Regression tested.
- Parameterised SQL throughout. Zero string-concat. Grepped in CI.
- `shell=False` everywhere. No user input flows into `subprocess.run`. Grepped in CI.
- Telemetry payload contents enumerated in `src/subarr/telemetry.py` with a regression test (`test_payload_never_includes_forbidden_fields`) guarding against accidental fingerprintable fields.
- Reporting a vulnerability: `security@subarr.com`. We acknowledge within 72 hours. Full policy in [`SECURITY.md`](SECURITY.md).

## API reference

Every subarr instance serves its own interactive API docs (FastAPI):

- **`/docs`** — Swagger UI: browse and try every endpoint live.
- **`/openapi.json`** — the raw OpenAPI 3 spec; feed it to Postman/Insomnia/Bruno or generate a typed client (`openapi-typescript`, `orval`).

When `SUBARR_API_KEY` is set, send it as `X-Api-Key` (or `?apikey=`) on `/api/*` calls — the docs pages themselves stay open so you can read the surface before authenticating.

## Telemetry

Subarr ships with anonymous telemetry **on by default**. We are explicit about what it buys you, and the opt-out is one click in Settings and one click in the onboarding wizard.

**What gets sent:** install ID (random UUID generated locally, not a user identity), subarr version, Python version, OS / arch, subgen kind (subarr-subgen / vanilla / unreachable), subgen version, integration booleans (configured yes / no, never URLs or keys), library-size bucket (under 100 / 100-1k / 1k-10k / over 10k), scheduler mode, walks-per-day rolling average, error counts by exception class, docker tier.

**Never sent:** file paths, titles, IPs, hostnames, API keys, languages, anything user-fingerprintable. Enforced by a regression test on the client AND by an allow-list / forbidden-pattern check on the receiving Cloudflare Worker. Both pin against the same forbidden-fields list.

**What it buys you:** the Tuning Lab and recipe leaderboard shipped in 1.2 are the *local* half of a feedback loop. Telemetry is what lets the *global* half follow:
- A global Whisper-kwargs leaderboard built from aggregated telemetry. The more installs send their per-language kwargs plus verification outcomes, the more accurate the "best French settings" recommendation gets.
- A global provider success leaderboard, the same loop for Bazarr providers.
- Tuning Lab variant suggestions pre-filled from cohort data.

These cross-install loops are the next roadmap step. The reference-free quality judge they were gated on now ships (LaBSE cross-lingual adequacy, validated) — so crowd-aggregation has the trustworthy ranking signal it needs.

**Where to verify:** Settings → Telemetry shows the exact JSON of the last ping. Receiving worker source at [`coaxk/subarr-telemetry`](https://github.com/coaxk/subarr-telemetry). Public stats dashboard at [`stats.subarr.com`](https://stats.subarr.com).

**Note for Pi-hole users:** there are two subarr subdomains and they do different things.

- `telemetry.subarr.com`, the receiver your install posts heartbeats to. Privacy-conscious regex blocklists deny anything matching `*telemetry*` by default, which catches this one. That is working as intended: blocking it switches telemetry off without any further action.
- `stats.subarr.com`, the public read-only dashboard. No PII, no auth, no requests from your install, just the aggregated numbers anyone can view. Most blocklists do not catch it because the name is honest about what it is.

We picked these names deliberately. Hiding the sender behind something like `analytics.subarr.com` or putting it on the apex would be the opposite of honest. If you want telemetry off, do not allow `telemetry.subarr.com`. If you want it on, allow that one specifically rather than wildcarding the whole zone.

## Updates

Subarr polls GitHub releases once per 24 hours for both `coaxk/subarr` and `coaxk/subarr-subgen`. The subarr-subgen comparison uses patch-stack revision so patch-level updates are detected even when upstream subgen version stays the same.

```bash
# In the directory with your compose.yaml
docker compose pull
docker compose up -d
```

The Settings panel shows the current vs latest version per product with release notes inline. No auto-update by design, you run upgrades when you know it is happening.

## Architecture

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/architecture-dark.png">
  <img alt="subarr sits between your stack's inputs and subgen: Bazarr's wanted-list, Sonarr/Radarr file paths, library files on disk, and Tautulli/Plex hints feed into subarr — scheduler, probe-gate (ffprobe), coverage, queue — which coordinates transcription out to subgen (Whisper), the written .srt, and a Plex library refresh." src="docs/architecture.png" width="850">
</picture>

**How it runs.** subarr is a long-running service with its own scheduler — it reads Bazarr's wanted list and walks your library on a cadence you set (and on demand from the UI). You don't wire it into Sonarr/Radarr as a custom script or trigger it manually; it just runs beside them.

| Layer | Detail |
|---|---|
| Backend | Python 3.12 + FastAPI + httpx. Async throughout. |
| Storage | Single SQLite file, default `/data/subarr.db` (override with `SUBARR_DB_PATH`). Hand-rolled migrations runner. |
| Frontend | React 18 + esbuild. CDN React. Bundles committed so `pip install` ships a working SPA. |
| Subgen drive | HTTP. 22 small patches over upstream McCloudS/subgen. Living patch stack at [`coaxk/subarr-subgen`](https://github.com/coaxk/subarr-subgen). |
| Discovery | Read-only Docker API via [tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy). |
| Telemetry receiver | Cloudflare Worker + D1. Open source at [`coaxk/subarr-telemetry`](https://github.com/coaxk/subarr-telemetry). |

Three deployment tiers (full templates in [`deploy/templates/`](deploy/templates/README.md)):

| Tier | What you get | What you give up | Who it is for |
|---|---|---|---|
| 1, Standalone | Manual integration URLs, no Docker access | Auto-detect, container-name hostnames | Non-Docker hosts |
| 2, Socket proxy (recommended) | Auto-detect on your existing Docker network | Slightly more setup | Most homelabs |
| 3, Full integration | Tier 2 + API-key auto-extract from config volumes | Subarr can read every mounted config dir | Trust your single-tenant box |

## Roadmap

**v2.2 (this release):**

- **Blacklist a bad provider sub** (shipped): from Aftercare or the Library tree, straight to Bazarr, so it stops re-fetching the same broken release.
- **Transcribe forced-only files** (shipped): a per-file full-sub override on forced-only files, without flipping subgen's global forced-subs setting (needs `subarr-subgen` r10+).
- **Per-title ignore** (shipped): suppress a whole show or a single file, inline on Library or Review.
- **Subtitle-controls hub** (shipped): one card on the Rules page that signposts every force / ignore / language control to where it lives.
- **On-demand database backup + integrity check** (shipped): from the Health page.
- **Reliability pass** (shipped): event-loop UI-freeze fix, database durability, Plex-client circuit breaker, queue reconciliation, telemetry + auth hardening.

*Previously: security hardening, non-root container, and activation (2.0); guided subgen setup (1.6); Job Aftercare, default-track mismatch fix, and queue authority (1.4); the Tuning Lab, verified audio, and the global recipe leaderboard (1.2); speech-aware audio (1.1). See the [changelog](CHANGELOG.md).*

**Later** — still on the list:

- **Provider success leaderboard**: aggregate Bazarr per-provider history across opt-in installs into a global ranking. Closes "which subtitle providers actually deliver?", a long-standing Bazarr feature request.
- **The federated tuning loop**: cross-install kwargs aggregation ranked by verification outcomes, and **"use community-best for &lt;language&gt;"** one-click adoption. The reference-free quality judge it was gated on now ships (LaBSE cross-lingual adequacy, validated).
- **First-class media-server integration**: Jellyfin / Emby backends alongside Plex.

## The subgen patch story

Subarr drives subgen through 22 small patches over upstream McCloudS/subgen. Each is independent, idempotent on reapply, required for one specific subarr orchestration behaviour. Living patch stack at [`coaxk/subarr-subgen`](https://github.com/coaxk/subarr-subgen).

The maintained image is `ghcr.io/coaxk/subarr-subgen:<tag>`. Tagged releases: `v2026.05.3-r9` current (Blackwell/RTX 50xx CUDA 12.8, gnupg CVE patch, the verified "strongpad" segmentation baked in as the default, plus the tuned Whisper kwargs, a runtime `/config` endpoint that powers guided setup's live-apply, and a GPU device-guard entrypoint), with `latest` and per-version tags.

You do not need our patched image. See the "I already have subgen" table at the top.

## Development

```bash
git clone https://github.com/coaxk/subarr
cd subarr
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
PYTHONPATH=src uvicorn subarr.app:app --reload --port 9922
PYTHONPATH=src pytest -q                    # 1159 passing
npm install && npm run build:frontend       # SPA bundles
```

## Related

- [Bazarr](https://github.com/morpheus65535/bazarr), the librarian. Subarr reads its wanted list and writes back its scan-disk trigger.
- [McCloudS/subgen](https://github.com/McCloudS/subgen), the worker. Subarr drives it via the patches in [`coaxk/subarr-subgen`](https://github.com/coaxk/subarr-subgen).
- [subsyncarr](https://github.com/johnpc/subsyncarr), the synchroniser. Recommended companion for sync issues subarr does not tackle.

## License

MIT. See [LICENSE](LICENSE). The patched subgen image (`ghcr.io/coaxk/subarr-subgen`) is a derived work of upstream McCloudS/subgen. See that repo's [`NOTICE`](https://github.com/coaxk/subarr-subgen/blob/main/NOTICE) for attribution.
