# subarr

The coordination layer for the *arr subtitle stack. Stands beside Bazarr.

Subarr decides what subtitles are actually missing across your library, which providers are worth your time, and when it is worth running Whisper. Bazarr finds and downloads. Subgen transcribes. Subarr coordinates.

[![status](https://img.shields.io/badge/status-v1.0-violet)](https://github.com/coaxk/subarr)
[![tests](https://img.shields.io/badge/tests-266_passing-22d3ee)](https://github.com/coaxk/subarr/actions/workflows/ci.yml)
[![security](https://img.shields.io/badge/Bandit_%2B_Semgrep_%2B_Trivy_%2B_pip--audit-22c55e)](#security)
[![license](https://img.shields.io/badge/license-MIT-c8c8cc)](LICENSE)

> Built with AI assistance from Claude. Code is open, every PR is human-reviewed. Telemetry, security scans, and a published test count are how we stay honest about that.

![Subarr in action](docs/hero.gif)

---

## In one breath

- **See your whole library's subtitle coverage at a glance.** Per-language gap view across Sonarr + Radarr + Bazarr, with audio language we trust.
- **Calibrated audio language detection.** Three Whisper chunks across the file, conservative voting, confidence-gated. Cheap to skip files Whisper would hallucinate on.
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
    volumes:
      - ./subarr/config:/config
      - /path/to/media:/media/library:rw   # same path Bazarr and subgen see
```

```bash
docker compose up -d
# Open http://localhost:9922, onboarding wizard auto-detects your stack.
```

The wizard tries to auto-detect Sonarr/Radarr/Bazarr/Tautulli/subgen on your existing Docker network and prefills URLs. Manual entry is available at every step as a safety net. Auto-detect plus manual fallback at every step is the design rule.

**Why `:rw` on the media mount.** Subarr's sidecar mismatch detector renames orphaned `.srt` files whose basename drifted from the video. Read-only blocks this. If you don't want it, set `SUBARR_SIDECAR_RENAME=0` and mount `:ro`, the rest of the product works.

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
| `mccloud/subgen` and you want everything | Swap to `ghcr.io/coaxk/subarr-subgen`. Same upstream image plus 13 small auditable patches. Pull, change one line in your compose, restart. No data loss, no config rewrite. |
| No subgen yet | Start with `ghcr.io/coaxk/subarr-subgen`. Everything works on day one. |
| You run Bazarr only | Subarr adds a coordination layer beside Bazarr. Bazarr keeps doing what it does. Subarr surfaces what is actually missing, schedules the work, and writes results back. |

You do not need to decide at install. Subarr re-probes subgen every 30 seconds and adopts new capabilities the moment you upgrade.

## Do you need subarr?

Skip subarr if any of these are true:

- Your library is single-language and you have never had a wrong-language subtitle land.
- You use one or two providers and never wonder which one delivered what.
- You don't run Whisper or any local transcription, and don't plan to.

Subarr's value compounds with: multi-language libraries, three or more Bazarr providers, Whisper-in-the-loop, and a habit of asking "why did Bazarr re-search this?"

## What is in v1.0

| Surface | Function |
|---|---|
| Dashboard | Live column-as-stage pipeline (discovered → probing → bazarr-wanted → transcribing → written-back), GPU widget, integration health, next scheduled run, recent activity |
| Coverage | Flat, dense gap-list table. Score-gradient sort. Reason chips (no-track, embedded-only, bazarr-wanted, audio-mislabel, low-score, unmonitored). Bulk select + apply rule + queue |
| Library | Tree across all series and movies. Audio / sub / runtime columns with probe-state indicators |
| Queue | Featured Queue: Processing, Queued, Lost-on-restart, Issues, Recently done. Per-row requeue, remove, cancel. Promote / demote / reorder / pause are roadmap (need subgen v4.9 queue-mutation endpoints) |
| Review | Manual audio-language verification queue with audio player, multi-track support, batch cycle, Layer 3 Whisper detection inline |
| Rules | Auto-queue rules with score thresholds, language filters, custom-format pre-classification |
| Settings | Per-language Whisper kwargs, integrations health, system actions, telemetry transparency panel showing the exact JSON last sent |

### About ollama (optional, recommended)

Subarr does not require ollama. With it, you get two extras:

- **Structured enrichment.** Vague Bazarr wanted entries get classified by language, genre hints, dialog density. Improves prioritisation. Works with any text model.
- **Vision pre-filter.** A vision-capable model classifies Tautulli thumbnails as dialog-heavy / music-heavy / visual-only. Suppresses transcribe submissions where Whisper would hallucinate.

Vision and text models are separate (`OLLAMA_MODEL` and `OLLAMA_VISION_MODEL`). Default vision model is `qwen2.5vl:7b`. Subarr auto-detects any installed model from `qwen2.5vl`, `qwen2-vl`, `llama3.2-vision`, `llava`, `bakllava`, `minicpm-v`, `moondream`. Without a vision-capable model the pre-filter is cleanly disabled, not silently broken. Settings shows the active state.

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

**Will this work with Jellyfin / Emby?** Not in v1.0. v1.1 candidate if there's demand. Open a feature request.

## Known limitations (v1.0)

Transparent before you install.

- Requires `ghcr.io/coaxk/subarr-subgen` for calibrated Layer 3 detection, queue cancel, curated per-language `initial_prompt`s, and the safe-decode preset. Vanilla subgen works in compat mode but you miss these.
- No built-in multi-user auth. Basic-auth env vars exist as a single-admin fallback. Run behind a reverse proxy (Authelia / Caddy / Traefik) for anything serious.
- Queue reorder / promote / demote / pause are not in v1.0, they need subgen patch v4.9 to land first. Requeue / remove / cancel ship today.
- Auto-update is intentionally absent. Update notifications appear in the UI; you run the upgrade.
- Plex integration goes through Tautulli for activity signal, not Plex directly. Tautulli is the bridge.
- SQLite only. No Postgres backend in v1.0.
- Single-host. Workers / multi-host are an explicit non-goal until users ask.
- Jellyfin / Emby are not yet supported.
- arm64 builds are not yet published. Pi 4 / 5 users need to build locally for now.
- Compose example uses bind mounts. Named volumes work but you lose the "same path Bazarr and subgen see" sanity.

## Security

- Bandit, Semgrep, pip-audit, Trivy run on every push to `coaxk/subarr` and `coaxk/subarr-subgen`. SARIF uploads to the GitHub Security tab.
- Constant-time auth comparison (`secrets.compare_digest`). Regression tested.
- API keys never appear in any HTTP response, masked surface, raw key only in dataclass internals. Regression tested.
- Every filesystem operation routes through `canonical_to_fs()` which rejects path-traversal outside the configured media root. Regression tested.
- Parameterised SQL throughout. Zero string-concat. Grepped in CI.
- `shell=False` everywhere. No user input flows into `subprocess.run`. Grepped in CI.
- Telemetry payload contents enumerated in `src/subarr/telemetry.py` with a regression test (`test_payload_never_includes_forbidden_fields`) guarding against accidental fingerprintable fields.
- Reporting a vulnerability: `security@subarr.com`. We acknowledge within 72 hours. Full policy in [`SECURITY.md`](SECURITY.md).

## Telemetry

Subarr ships with anonymous telemetry **on by default**. We are explicit about what it buys you, and the opt-out is one click in Settings and one click in the onboarding wizard.

**What gets sent:** install ID (random UUID generated locally, not a user identity), subarr version, Python version, OS / arch, subgen kind (subarr-subgen / vanilla / unreachable), subgen version, integration booleans (configured yes / no, never URLs or keys), library-size bucket (under 100 / 100-1k / 1k-10k / over 10k), scheduler mode, walks-per-day rolling average, error counts by exception class, docker tier.

**Never sent:** file paths, titles, IPs, hostnames, API keys, languages, anything user-fingerprintable. Enforced by a regression test on the client AND by an allow-list / forbidden-pattern check on the receiving Cloudflare Worker. Both pin against the same forbidden-fields list.

**What it buys you:**
- The v1.1 global Whisper kwargs leaderboard is built from aggregated telemetry. The more installs send their per-language kwargs plus verification outcomes, the more accurate the "best French settings" recommendation gets.
- The v1.1 global provider success leaderboard is the same loop for Bazarr providers.
- The v1.1 tuning lab pre-fills variant suggestions from cohort data.

**Where to verify:** Settings → Telemetry shows the exact JSON of the last ping. Receiving worker source at [`coaxk/subarr-telemetry`](https://github.com/coaxk/subarr-telemetry). Public stats dashboard at [`stats.subarr.com`](https://stats.subarr.com).

**Note for Pi-hole users:** there are two subarr subdomains and they do different things.

- `telemetry.subarr.com`, the receiver your install posts heartbeats to. Privacy-conscious regex blocklists deny anything matching `*telemetry*` by default, which catches this one. That is working as intended: blocking it switches telemetry off without any further action.
- `stats.subarr.com`, the public read-only dashboard. No PII, no auth, no requests from your install, just the aggregated numbers anyone can view. Most blocklists do not catch it because the name is honest about what it is.

We picked these names deliberately. Hiding the sender behind something like `analytics.subarr.com` or putting it on the apex would be the opposite of honest. If you want telemetry off, do not allow `telemetry.subarr.com`. If you want it on, allow that one specifically rather than wildcarding the whole zone.

## Authentication

No built-in auth by default. Designed for a reverse proxy (Authelia, Caddy basicauth, Traefik forward-auth). In-product fallback is HTTP Basic via env vars:

```yaml
environment:
  SUBARR_USER: youradmin
  SUBARR_PASS: a-very-long-random-password
```

When both are set, every non-monitoring request requires Basic credentials. `/api/health` always bypasses for monitoring tools.

Honest limitations of basic auth: one global user, no per-user audit, credentials transmitted on every request. Reverse-proxy auth is the right answer for anything that matters.

## Updates

Subarr polls GitHub releases once per 24 hours for both `coaxk/subarr` and `coaxk/subarr-subgen`. The subarr-subgen comparison uses patch-stack revision so patch-level updates are detected even when upstream subgen version stays the same.

```bash
# In the directory with your compose.yaml
docker compose pull
docker compose up -d
```

The Settings panel shows the current vs latest version per product with release notes inline. No auto-update by design, you run upgrades when you know it is happening.

## Architecture

| Layer | Detail |
|---|---|
| Backend | Python 3.12 + FastAPI + httpx. Async throughout. |
| Storage | Single SQLite file at `/config/subarr.db`. Hand-rolled migrations runner. |
| Frontend | React 18 + esbuild. CDN React. Bundles committed so `pip install` ships a working SPA. |
| Subgen drive | HTTP. 13 small patches over upstream McCloudS/subgen. Living patch stack at [`coaxk/subarr-subgen`](https://github.com/coaxk/subarr-subgen). |
| Discovery | Read-only Docker API via [tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy). |
| Telemetry receiver | Cloudflare Worker + D1. Open source at [`coaxk/subarr-telemetry`](https://github.com/coaxk/subarr-telemetry). |

Three deployment tiers (full templates in [`deploy/templates/`](deploy/templates/README.md)):

| Tier | What you get | What you give up | Who it is for |
|---|---|---|---|
| 1, Standalone | Manual integration URLs, no Docker access | Auto-detect, container-name hostnames | Non-Docker hosts |
| 2, Socket proxy (recommended) | Auto-detect on your existing Docker network | Slightly more setup | Most homelabs |
| 3, Full integration | Tier 2 + API-key auto-extract from config volumes | Subarr can read every mounted config dir | Trust your single-tenant box |

## Roadmap

**v1.1** ships the global feedback loops:

- **Provider success leaderboard** [v1.1]: aggregates Bazarr per-provider history across opt-in installs into a global ranking. Closes "which subtitle providers actually deliver?", a long-standing Bazarr feature request.
- **In-app Whisper tuning lab** [v1.1]: run multiple Whisper kwargs variants against a single file, score them, review side by side, adopt the winner per language. The aggregated kwargs distribution becomes the global "best for French" recommendation.
- **Queue mutation** [v1.1]: promote, demote, reorder, pause. Requires subarr-subgen v4.9 patch first.

**v1.2** closes the consensus loops:

- Cross-install kwargs aggregation ranked by verification outcomes.
- "Use community-best for &lt;language&gt;" one-click adoption in Settings.
- Series-level audio-language intent memory propagating to newly discovered episodes.

## The subgen patch story

Subarr drives subgen through 13 small patches over upstream McCloudS/subgen. Each is independent, idempotent on reapply, required for one specific subarr orchestration behaviour. Living patch stack at [`coaxk/subarr-subgen`](https://github.com/coaxk/subarr-subgen).

The maintained image is `ghcr.io/coaxk/subarr-subgen:<tag>`. Tagged releases: `v4.7` current, with `latest`, `stable` (7-day soak), and per-version tags.

You do not need our patched image. See the "I already have subgen" table at the top.

## Development

```bash
git clone https://github.com/coaxk/subarr
cd subarr
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
PYTHONPATH=src uvicorn subarr.app:app --reload --port 9922
PYTHONPATH=src pytest -q                    # 266 passing
npm install && npm run build:frontend       # SPA bundles
```

## Related

- [Bazarr](https://github.com/morpheus65535/bazarr), the librarian. Subarr reads its wanted list and writes back its scan-disk trigger.
- [McCloudS/subgen](https://github.com/McCloudS/subgen), the worker. Subarr drives it via the patches in [`coaxk/subarr-subgen`](https://github.com/coaxk/subarr-subgen).
- [subsyncarr](https://github.com/McCloudS/subsyncarr), the synchroniser. Recommended companion for sync issues subarr does not tackle.

## License

MIT. See [LICENSE](LICENSE). The patched subgen image (`ghcr.io/coaxk/subarr-subgen`) is a derived work of upstream McCloudS/subgen. See that repo's [`NOTICE`](https://github.com/coaxk/subarr-subgen/blob/main/NOTICE) for attribution.
