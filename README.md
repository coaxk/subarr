# subarr

The coordination, measurement, and quality layer that subgen never had.

Subarr is a peer service in the *arr family. It sits between Bazarr, Sonarr, Radarr, Tautulli, Plex, ollama, and subgen, deciding what is actually missing across your library, what is worth generating, when to run the work, and writing the result back so Bazarr's wanted list actually shrinks.

> Bazarr is the librarian. Subgen is the worker. Subarr is the brain.

[![status](https://img.shields.io/badge/status-v1.0-violet)](https://github.com/coaxk/subarr)
[![tests](https://img.shields.io/badge/tests-246_passing-22d3ee)](https://github.com/coaxk/subarr/actions/workflows/ci.yml)
[![security](https://img.shields.io/badge/security-bandit_%2B_semgrep_%2B_trivy_%2B_pip--audit-22c55e)](#security)
[![license](https://img.shields.io/badge/license-MIT-c8c8cc)](LICENSE)

> **Proudly developed with AI assistance from Claude.** Please direct all complaints to `/dev/null`. The code is open and every PR is reviewed by a human (me) before it lands. Telemetry, security scans, and a published test count are how we keep ourselves honest about it.

---

## "I already have subgen. What do I do?"

The most-asked question from existing subgen users. Quick answer.

| You have | What to do |
|---|---|
| Vanilla `mccloud/subgen` | Keep it. Add subarr next to it. Subarr detects vanilla on startup and runs in compat mode: Coverage, Provenance, scheduling, audio-language review all work. Scan submission shows a friendly "needs subarr-subgen" for the features that require our patches. You lose calibrated Layer 3 audio detection, queue cancel, and the curated initial_prompts (those need our patches). |
| `mccloud/subgen` and you want everything | Swap to `ghcr.io/coaxk/subarr-subgen`. Same upstream image, plus 13 small patches that subarr's orchestration needs. Pull the new image, change one line in your compose, restart. No data loss, no config rewrite. The patches are open and auditable in [`coaxk/subarr-subgen`](https://github.com/coaxk/subarr-subgen). |
| No subgen yet | Start with `ghcr.io/coaxk/subarr-subgen`. You get every feature on day one. |
| You are happy with Bazarr alone | Subarr is the layer above Bazarr that adds the coordination, prioritisation, audio-language ground truth, and quality measurement that Bazarr does not do. You can still install subarr without changing anything in Bazarr; Bazarr keeps doing what it does today and subarr adds the brain. |

You do not need to pick at install time. Subarr re-probes subgen every 30 seconds and adopts new capabilities the moment you upgrade. You can start vanilla, decide you want Layer 3 detection a week later, swap the image, restart. No subarr config change needed.

**Where we're going next:** v1.1 ships the in-app Whisper tuning lab (run multiple kwargs variants on one file, score them, adopt the winner per language). v1.2 closes the loop with a crowdsourced global "best kwargs by language" leaderboard from opt-in telemetry. Full [Roadmap](#roadmap) below.

---

## Three things nothing else in the *arr ecosystem does

These are the anchor differentiators. Everything else in this README is plumbing in service of them.

### 1. Calibrated audio-language detection (Layer 3 Whisper, multi-chunk)

Vanilla subgen samples one 30-second window at the start of a file and trusts whatever Whisper says about the language. That window is silent, intro music, or a foreign-language opening narration as often as not. Anime is the canonical failure case: an English-dub episode whose first 30 seconds are the Japanese OP gets transcribed in Japanese, the user gets garbage, nobody knows why.

Subarr ships a four-layer ground-truth funnel that arrives at a calibrated confidence value before any transcription begins:

```
  L1  file metadata          ffprobe audio_language tag.
                             Cheap, often wrong on retags.

  L2  Tautulli signal        Which audio track is your household
                             actually picking when they watch?

  L3  Whisper robust detect  Sample 3 chunks across 10/50/90 percent
                             of the file, vote by majority,
                             confidence is the MINIMUM probability
                             across the agreeing chunks (conservative,
                             so one high-confidence chunk cannot
                             mask a disagreeing chunk).

  L4  user verification      Review queue surfaces every suspect
                             row. One click confirms, propagates
                             back to Sonarr to unblind Bazarr.
```

Layer 3 is the part nothing else has. Three chunks across the middle 80 percent of the file, full per-chunk evidence rendered in the review modal, conservative aggregation. A 3-of-3 agreement at probability 0.97 is trustworthy. A 2-of-3 agreement at probability 0.41 lands in the manual review queue with the chunk evidence visible.

Once a verification exists, every downstream submission to subgen carries it through an evidence gate. A confidence below 0.5, or a missing source field, refuses to forward the override. Whisper transcribes from the audio, the way it was meant to.

### 2. Provider success leaderboard

Bazarr's UI shows you which providers are configured. It does not show you which ones actually deliver subs that survive contact with your library. Subarr aggregates Bazarr's per-provider history and produces a per-install leaderboard ranked by:

- attempts (volume signal, how often the provider gets tried at all)
- success rate (delivered a sub that passed Bazarr's score threshold)
- auto-blacklist rate (sub delivered then later flagged as desynced or wrong)
- net success (the only column that matters)

First time anyone in the *arr space has measured what providers actually do for a real library rather than what they claim on their marketing page.

The v1.1 roadmap closes the loop by aggregating across installs through the opt-in telemetry channel and publishing a global leaderboard at subarr.com/stats. Then a new user can answer "which providers should I bother with for French TV" by looking at what worked for the global cohort already running that exact pattern.

### 3. In-app Whisper tuning lab (v1.1 preview)

Whisper has roughly 20 knobs that materially affect output quality: initial_prompt, condition_on_previous_text, temperature schedule, beam_size, no_speech_threshold, vad parameters, repetition penalty, length penalty, and more. The defaults are reasonable for podcast English and progressively worse for everything else. Foreign-language content, music-heavy sequences, and dialog-sparse scenes each have a different optimal kwargs set.

Today users either inherit cargo-cult kwargs from a forum post or accept whatever vanilla subgen ships. There is no objective way to know what is best for your library, your language, your content style.

Subarr v1.1 ships a tuning lab inside the app. Pick a representative file, pick a language, pick four variants from curated starter packs ("Anime", "Drama", "Documentary", "Music-heavy", "Foreign-original"). Subarr runs each variant through subgen via a kwargs-override patch, scores them automatically on hallucination signals (repeated phrase loops, blank intervals, opening hallucinations), renders the SRTs side by side with synchronized playback, lets you rank by keyboard, and one-click adopts the winner into your per-language settings.

The v1.2 loop publishes anonymized winning-kwargs distributions to subarr.com/stats so new users can borrow the community-best for their language without running the lab themselves. Settings gains a "use community-best for French (87 percent verification rate, N installations)" row.

The marketing question this answers: are your subs gibberish, drifting, or hallucinating? Run the lab. See exactly what changes per variant. Adopt the one that works. Beat the consensus locally if you can.

---

## What else nothing in the market does (the under-the-radar list)

The three anchor differentiators are the headlines. Pulling the surface back, these are the structural capabilities that are also new to this space. Semi-technical inventory for anyone evaluating subarr against what they already run.

- **Subgen restart reconciliation.** When the subgen container reboots (config change, version upgrade, crash) the in-flight queue evaporates. Subarr detects the restart via a watchdog that probes patch revision plus reachability transitions, reconciles every in-flight scan_store entry against the provenance ledger, and surfaces the orphans in a dedicated "Lost on restart" bucket on the Queue page. One click requeues each one through the audio-language override path so re-submission actually transcribes. Nothing else in this space has any concept of "in-flight item that became a ghost."

- **Capability-driven graceful degrade.** Subarr probes subgen's `/queue` and `/status` on startup and again every 30 seconds. Each feature in the UI gates on a specific capability flag. If you swap subgen versions mid-flight, subarr picks up the change inside a minute and lights up or hides the affected surfaces automatically. The Settings panel shows you exactly which capabilities are present and what each one enables.

- **Whisper-or-Bazarr arbiter.** When a file has Bazarr providers exhausted plus a clear Whisper signal, subarr chooses between submitting to subgen and asking Bazarr to retry, based on per-provider success history. Eliminates the common "Bazarr keeps churning a file no provider has" failure pattern.

- **NOW PLAYING priority boost.** Tautulli `get_activity` is polled every cycle. Whatever your household is actively watching gets boosted in the coverage score so subs arrive in time. Reactive without the storm.

- **Vision pre-filter via ollama qwen2.5vl.** Optional. Tautulli serves thumbnails; ollama's qwen2.5vl vision model classifies whether the content is dialog-heavy, music-heavy, or visual-only. Subarr suppresses transcribe submissions for content where Whisper would just hallucinate (extended musical performances, mostly-visual sequences). Cuts wasted GPU minutes substantially. Optional integration; subarr does not require ollama, it just gets smarter when it is present.

- **Calendar JIT walks.** Sonarr's calendar tells subarr what is airing in the next 24 hours. A targeted walk runs against just those upcoming episodes, so subs are ready when the recording lands rather than queued behind a nightly batch.

- **History-driven "just imported" score.** Recently imported episodes get a transient priority boost. The arrival pattern matches when humans actually want them.

- **Sidecar basename mismatch detector.** When subgen and your renamer disagree, the .srt is invisible to Bazarr. Subarr scans for mismatches and proposes the corrected name; one click renames it.

- **Auto-blacklist desync'd subs.** Subs that timing-match wrong stay in your library forever because nothing notices. Subarr catches the drift, blacklists the entry in Bazarr, and re-queues the search.

- **Audio review with multi-track and batch cycle.** Files with multiple audio tracks (dub plus original) get a track selector in the review modal. Sample positions are auto-chosen from dialog-dense regions (silence avoided). Batch mode cycles through every pending row without leaving the modal.

- **Provenance ledger.** Every transcribe job records: who submitted, which subgen patch revision, completion time, Bazarr scan-disk trigger time. Click any file anywhere in the UI, get the full timeline.

- **Coverage caching with background refresh.** Coverage page loads instantly off a snapshot; refresh happens in the background. On a real-size library this is the difference between a 60 to 90 second wait per page load and a sub-100ms render.

- **Concurrent transcribes surfaced.** Subgen has supported `CONCURRENT_TRANSCRIPTIONS` for a long time but no UI ever told you. Subarr's Settings panel surfaces the knob with a per-Whisper-model VRAM budget table so you can pick N correctly.

---

## What it solves today

The most-felt pains in the Bazarr plus subgen plus subtitle-automation space, documented across r/bazarr, GitHub issues, and TRaSH guide forums. Subarr fixes that, and a lot more besides.

1. **Bazarr keeps re-searching subs you already have.** Subarr probes your media with ffprobe and knows what is already embedded or sidecar'd. Coverage walks suppress the false-positive gap rows that make Bazarr re-search forever.

2. **See your whole library's subtitle coverage at a glance.** Gap list across every series and movie, prioritised by Tautulli watch history. No native tool does this.

3. **Stop burning GPU on content you will never watch.** Scheduled coverage walks instead of reactive event storms. You tell subarr the rule once. It runs nightly and only queues what matches.

4. **Know exactly which provider gave you this sub.** Provenance ledger records every transcribe job: who submitted, what subgen version, completion time, Bazarr scan-disk trigger. Unique to subarr in this space.

5. **Treat embedded subs as first-class.** SDH versus forced versus commentary versus full are distinct states, not a binary "has subs" flag. Per-track audio language detection too.

6. **Recover automatically when subgen restarts.** A container reboot, version upgrade, or crash that used to lose in-flight work now triggers automatic reconciliation. Lost items appear in a dedicated bucket on the Queue page with a one-click requeue that passes the verified audio language through, so re-submission actually transcribes.

---

## Quickstart

The fastest path: drop a four-line compose, run it, walk the wizard. The wizard does the actual configuration.

```yaml
# compose.yaml
services:
  subarr:
    image: ghcr.io/coaxk/subarr:latest
    container_name: subarr
    restart: unless-stopped
    ports: ["9922:9922"]
    volumes:
      - /mnt/nas/Media:/media/library:rw    # your library root
      - ./data:/data                        # subarr's SQLite + ledger
```

Bring it up.

```bash
docker compose up -d
```

Open `http://localhost:9922`. The 10-step onboarding wizard takes it from here: auto-detects your *arr stack if you bind the docker socket (Tier 2 template below), falls back to manual entry of URLs and API keys for every integration if not, and validates each connection live before committing it to settings. Auto-detect plus manual fallback is the design point. You always have a path forward.

**Why `:rw` on the media mount.** Subarr's sidecar mismatch detector renames orphaned .srt files in place when you approve the rename. Mount read-only and that feature is the only thing that breaks; everything else still works. If you want the strongest containment posture and do not need auto-rename, change to `:ro` and live without that feature.

### Full template with auto-detect and integration URLs

For shared-network deploys where subarr can reach your *arr containers by name, use the Tier 2 template. It pre-fills the integration URLs so the wizard skips the manual entry steps.

```bash
mkdir -p ~/subarr && cd ~/subarr
curl -O https://raw.githubusercontent.com/coaxk/subarr/main/deploy/templates/tier2-socket-proxy.compose.yaml
curl -O https://raw.githubusercontent.com/coaxk/subarr/main/deploy/templates/.env.example
mv .env.example .env
$EDITOR .env  # TZ, MEDIA_ROOT, your *arr docker network name
docker compose -f tier2-socket-proxy.compose.yaml up -d
```

Three deployment tiers are available. See [`deploy/templates/README.md`](deploy/templates/README.md) for the trade-offs. Tier 2 is the recommended default.

---

## Architecture

Subarr is a coordinator, not a transcriber. It owns no GPU code. Subgen does that. State lives in the upstream services (Bazarr, Sonarr, Radarr, Tautulli) plus Docker plus subarr's own SQLite for the work subarr itself initiates.

```
   +-----------+        +---------+        +---------+
   | Bazarr    |<------>|         |------->| subgen  |
   | Sonarr    |        |         |        | whisper |
   | Radarr    |<------>| subarr  |<-------|         |
   | Tautulli  |        |         |        +---------+
   | Plex      |<------>|         |
   | ollama    |<------>|         |        +---------+
   +-----------+        +---------+------->| host    |
                                           | docker  |
                                           +---------+
```

| Layer | Tech |
|---|---|
| Backend | Python 3.11+, FastAPI, uvicorn, httpx, SQLite |
| Frontend | React 18 from CDN with design tokens, esbuild bundling |
| Migrations | Hand-rolled SQL runner. One file per change |
| Discovery | Read-only docker API via [tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy) |
| Storage | Single SQLite file at `/data/subarr.db` |
| Telemetry | Anonymous, opt-out, roughly 1 KB per day. Public stats at subarr.com/stats |

### About ollama (optional, recommended)

Subarr does not require ollama. If you do run it, subarr uses it for two things:

- **Structured enrichment.** When Bazarr's wanted entries are vague, subarr asks ollama (any local model that supports the structured JSON output format) to classify the entry by language, genre hints, expected dialog density. Improves prioritisation accuracy.
- **Vision pre-filter (qwen2.5vl).** Tautulli serves thumbnails; qwen2.5vl classifies the content frame by frame as dialog-heavy, music-heavy, visual-only, or mixed. Subarr suppresses transcribe submissions for content where Whisper would just hallucinate. Cuts wasted GPU minutes significantly on libraries with concerts, performances, or visual-only material.

If you do not have ollama running, subarr falls back to less precise heuristics for both. Nothing breaks.

---

## What is in v1.0

| Surface | Function |
|---|---|
| Dashboard | Live column-as-stage pipeline (discovered, probing, bazarr-wanted, transcribing, written-back) plus GPU widget, integration health, next scheduled run, recent activity |
| Coverage | Flat, dense gap-list table. Score-gradient sort. Reason chips (no-track, embedded-only, bazarr-wanted, audio-mislabel, low-score, unmonitored). Bulk select plus apply rule plus queue |
| Library | Tree view across all series and movies with audio, sub, runtime columns and probe-state indicators |
| Queue | Featured Queue: Processing, Queued, Lost on restart (subgen-reboot reconciliation), Issues (silent fails subgen swallowed), and Recently submitted history. Per-row actions: requeue, remove, cancel (when subgen is v4.4+). Promote, demote, reorder, and pause are not in v1.0 (see roadmap) |
| Review | Manual audio-language verification queue with audio player, multi-track support, batch cycle, and Layer 3 Whisper detection inline |
| Onboarding | 10-step wizard. Auto-detect via docker-socket-proxy plus manual fallback at every step |
| Rules | Build, Test, Deploy triad for auto-queue rules. Dry-run preview before commit. Curated rule packs (Anime, Hearing-impaired, Foreign-language learner, Movies-only) |
| Settings | Integration test, telemetry transparency panel, updates panel, per-language Whisper kwargs surface, concurrent-transcribe VRAM guide, capability detection |
| Per-file verdict | Modal timeline showing every probe, scan, write-back for any video file |

---

## The audio-language ground-truth pipeline

This is the centerpiece. Most subtitle-generation failures are not Whisper failures. They are pipeline failures upstream of Whisper. Subarr fixes the pipeline.

```
  STEP 1   ffprobe reads audio_language tag from the file container
           Captures: declared language, declared track count
           Failure modes: unset (und), retagged wrong, embedded
           in a foreign track ordering

  STEP 2   Cross-check vs Sonarr or Radarr originalLanguage
           Captures: what the upstream metadata thinks
           Mismatches flagged audio_label_suspect

  STEP 3   Tautulli get_activity for live signal
           Captures: which audio track is your household choosing?
           Resolves cases where the file has both EN dub and JA
           original; your watch behaviour disambiguates

  STEP 4   Subgen v4.5 robust language detection on demand
           Triggered from the review modal when L1-L3 disagree
           Three Whisper chunks at 10/50/90 percent of the file
           Confidence is min(probabilities of agreeing chunks)

  STEP 5   User confirms in review modal
           Audio player with sample positions chosen from
           dialog-dense regions (silence avoided automatically)
           Multi-track UI when N audio tracks present
           Batch review cycle across all pending rows

  STEP 6   Verification persists to audio_lang_store
           Propagates back to Sonarr to unblind Bazarr
           Forwarded as audio_language_override to subgen
           on every subsequent submission (with evidence gate)
```

The evidence gate is the part that took two iterations to get right. Forwarding a wrong override is worse than no override because subgen will trust subarr's bad value and transcribe English audio as if it were Japanese, producing unreadable text rather than degraded text. The gate refuses any override below 0.5 confidence and any override with an empty source field. Risky non-Latin script languages (Japanese, Korean, Chinese) get a distinct log line so post-hoc audits can grep specifically for the dangerous category.

---

## Roadmap

v1.0 ships the brain. v1.1 turns it into a measurement and tuning layer. v1.2 closes the loop with crowdsourced consensus.

### v1.1: In-app Whisper tuning lab

The killer feature. Run multiple Whisper kwargs variants against a single file, score the outputs automatically, review them side by side, adopt the winner into your per-language settings. See the [In-app Whisper tuning lab](#3-in-app-whisper-tuning-lab-v11-preview) section above.

Phases:
- v1.1-A: subarr-subgen patch v4.8 for kwargs override, experiment backend, Experiments tab MVP
- v1.1-B: adopt button plus persistent per-language settings store
- v1.1-C: telemetry payload extension (opt-in, sanitised) for kwargs and verification outcomes

### v1.1 queue controls

Promote, demote, reorder, and pause buttons on the Queue page. Requires a subgen-side patch (planned v4.9) that exposes queue mutation endpoints. Currently subgen's `DeduplicatedQueue` only supports insert plus cancel, so reorder needs the upstream change first.

### v1.2: Global tuning consensus

- Cross-install aggregation of kwargs distributions ranked by verification outcomes
- subarr.com/stats publishes "best Whisper kwargs by language" with confidence
- "Use community-best for French" one-click adoption in Settings
- "Beat the consensus" challenge in the Experiments tab for users who want to push past the global default

### Other items in flight

- Global provider leaderboard published at subarr.com/stats
- Tuning lab presets for genre-specific content (Anime, Drama, Documentary, Music-heavy)
- subarr.com/stats public telemetry dashboard

---

## The subgen patch story

Subarr drives subgen through 13 small patches over upstream McCloudS/subgen. Each is independent, idempotent on reapply, and required for one specific subarr orchestration behaviour. Living patch stack at [`coaxk/subarr-subgen`](https://github.com/coaxk/subarr-subgen).

| Patch | Capability | Why subarr needs it |
|---|---|---|
| 0001 | Per-language `SUBGEN_KWARGS_LANG_XX` env overrides | Different Whisper kwargs per source language |
| 0002 | Eager model load on container start | Removes the cold-start delay on first batch |
| 0003 | Reverse sort for transcribe-existing walks | Newest files first |
| 0004 | `POST /batch` structured response | Subarr can count queued vs skipped vs error per submission |
| 0005-0006 | Internal correctness fixes | `LanguageCode.from_string` and import safety |
| 0007 | `GET /queue` with type-tracked deduplicated queue | Featured Queue UI |
| 0008 | Log language detection probability | Debug visibility |
| 0009 | `audio_language_override` query param | Subarr can bypass `SKIP_IF_AUDIO_LANGUAGES` with verified evidence |
| 0010 | `POST /queue/cancel` | Queue page cancel button |
| 0011 | `POST /detect_language_robust` | Layer 3 multi-chunk Whisper detection |
| 0012 | Curated per-language `initial_prompt`s | Punctuation seeding for 12 major languages |
| 0013 | `SUBARR_SUBGEN_SAFE_DECODE` preset | Opt-in anti-hallucination kwargs |

The maintained image is `ghcr.io/coaxk/subarr-subgen:<tag>`. Tagged releases: `v4.7` is current, with `latest`, `stable` (7-day soak), and per-version tags published.

You do not need our patched image. See the decision table at the top of this README for the "I already have subgen" walkthrough.

---

## Compat mode

Subarr works with any subgen. On startup it probes `/queue` and `/status` and figures out what is available. The watchdog re-probes every 30 seconds so capability changes between subgen restarts get picked up automatically.

| Subgen build | /queue | /batch structured | Capabilities | What works |
|---|---|---|---|---|
| `ghcr.io/coaxk/subarr-subgen` v4.5 plus | yes | yes | queue_cancel, robust_language_detection, audio_language_override, curated_language_prompts, safe_decode_preset | Everything |
| `ghcr.io/coaxk/subarr-subgen` v4.3 to v4.4 | yes | yes | audio_language_override | Most things, no Layer 3 |
| `mccloud/subgen` vanilla | no | plain text | none | Coverage, Provenance, scheduling. Scan submission shows "needs subarr-subgen" |

The Settings panel shows the detected mode and version so there is never confusion about which features are active. When a feature requires a newer subgen than what is running, the UI surfaces the specific patch revision needed instead of silently degrading.

---

## Security

Subarr is built to run inside your trusted LAN, but we hold the codebase to a higher bar than that. Full policy and threat model in [`SECURITY.md`](SECURITY.md); the headlines:

**Continuous scanning, four tools, two repos.** Every push to `coaxk/subarr` and `coaxk/subarr-subgen` runs:

| Tool | What it catches | Where to read |
|---|---|---|
| **Bandit** | Python security smells (eval, shell=True, weak crypto, hardcoded secrets) | GitHub Security tab, SARIF upload |
| **Semgrep** | Pattern-based vulns (SQL injection, path traversal, auth bypass, unsafe deserialisation) | GitHub Security tab, SARIF upload |
| **pip-audit** | Known CVEs in pinned dependencies | CI log, fails the build on HIGH or CRITICAL |
| **Trivy** | Container image vulns on the published GHCR image | GitHub Security tab |

Weekly scheduled runs catch CVEs disclosed between releases. Every dependency in `pyproject.toml` is version-pinned, so a transitive bump never sneaks in unverified.

**Defences in the code itself** (each one regression-tested):

- **Basic-auth** uses `secrets.compare_digest` (constant-time, defeats timing attacks). Test: `test_uses_constant_time_compare`.
- **API keys never leave the backend.** Every API response that touches an integration key returns a masked form (`••••f6c0`). Raw key only lives in a dataclass internal field. Test: `test_api_key_never_surfaces_in_response`.
- **Path safety.** Every filesystem operation routes through `canonical_to_fs()` which rejects path-traversal outside the configured media root. Test: `test_canonical_to_fs_rejects_traversal`.
- **SQL.** Parameterised queries everywhere. Zero string-concat-into-SQL. Grepped in CI.
- **Subprocess.** `shell=False` everywhere. No user input ever flows into `subprocess.run` args. Grepped in CI.
- **Telemetry.** Payload contents are enumerated in `src/subarr/telemetry.py` and a regression test (`test_payload_never_includes_forbidden_fields`) guards against accidentally adding a fingerprintable field. The Settings panel shows the exact JSON of the last ping.

**Reporting a vulnerability.** Email `security@subarr.com`. We acknowledge within 72 hours, scope, coordinate, and credit you in the release notes if you'd like recognition. Full details in [`SECURITY.md`](SECURITY.md).

**246 tests passing on every push.** API contracts, security regressions, frontend smoke, capability negotiation, the whole audio-language pipeline. CI fails closed: if the security gates fail, the release does not ship.

---

## Telemetry

Subarr ships with anonymous telemetry on by default. We are being explicit about what telemetry buys you because there is a direct line between the data the cohort sends and the value the cohort gets back.

**Why telemetry being on by default helps you specifically:**

- The v1.2 global Whisper kwargs leaderboard is built from aggregated telemetry. The more installs that send their per-language kwargs plus verification outcomes, the more accurate the "best French settings" recommendation gets. If you opt out, you still get the recommendations; you just do not contribute to making them better.
- The v1.1 global provider leaderboard is the same loop for Bazarr providers. Send your per-provider success rates, see the cohort's success rates, decide which providers are worth your time based on what works for installs like yours.
- The v1.1 tuning lab pre-fills variant suggestions from the cohort. Without telemetry contributions, the tool still works locally; with them, your starting variants are already the ones the cohort has found best for your language plus genre profile.

Concretely:

- Roughly 1 KB per day payload
- Public dashboard at subarr.com/stats (goes live with v1.0 publish)
- Settings panel shows the **exact JSON** sent on the most recent ping
- One-click opt-out in Settings or during the onboarding wizard

**What is in the v1.0 payload:** install ID (random UUID, generated locally), subarr version, Python version, OS or arch, subgen kind (subarr-subgen, vanilla, unreachable), subgen version, integration booleans (configured yes or no, never URLs or keys), library size bucket (under 100, 100 to 1k, 1k to 10k, over 10k), scheduler mode, walks per day rolling average, error counts by exception class, docker tier.

**Never in the payload:** file paths, titles, IPs, hostnames, API keys, languages, anything user-fingerprintable. Enforced by a regression test.

**Coming in v1.1 (opt-in granularity, separate toggles in Settings):** per-language Whisper kwargs hash plus shape (never the raw `initial_prompt` string, only the hash), verification confirmation counts per language, experiment winning-kwargs hash. Three separate toggles so users can choose to share any combination.

**Note for Pi-hole users:** many privacy-conscious Pi-hole regex blocklists deny anything matching `*telemetry*` by default. We use the literal subdomain `telemetry.subarr.com` because hiding behind a misleading name (for example `stats.subarr.com`) would be the opposite of honest. If you actively want telemetry off, do not allow it. If you want to send it, allow `subarr.com` in your Pi-hole and the regex deny will no longer apply.

---

## Authentication

Subarr ships with no built-in auth by default. Designed to sit behind a reverse proxy (Authelia, Caddy basicauth, Traefik forward-auth) for production. The in-product fallback is HTTP Basic auth via env vars.

```yaml
environment:
  SUBARR_USER: youradmin
  SUBARR_PASS: a-very-long-random-password
```

When both are set, every non-monitoring request requires Basic credentials. `/api/health` always bypasses for monitoring tools.

**Honest limitations** of basic auth: one global user, no per-user audit trail, credentials transmitted on every request (use HTTPS via the proxy). Reverse-proxy auth is the right answer for anything that matters.

---

## Updates

Subarr polls GitHub releases once per 24 hours for both `coaxk/subarr` and `coaxk/subarr-subgen`. The subarr-subgen comparison uses patch-stack revision (v4.7 versus v4.8) rather than upstream subgen version, so patch-level updates are detected even when the underlying McCloudS/subgen version stays the same.

When a new version is available the header version label gets a soft violet pip and the Home dashboard surfaces an "Update available" tile. Click through to Settings, Updates panel, which shows:

- Current version versus latest, per product (subarr and subarr-subgen)
- The release notes for the new version, inline
- Copy-paste compose edit instructions for changing the image tag
- A breaking-change banner if the GitHub release flags it

The actual update is two commands you run on your host. The panel shows you the exact commands for your detected compose location.

```bash
# In the directory with your compose.yaml
docker compose pull
docker compose up -d
```

No auto-update by design. You always run the upgrade yourself so you know exactly when it happens.

---

## Networking: how subarr finds your *arr stack

Subarr's integrations (Sonarr, Radarr, Bazarr, Tautulli, subgen, Plex, ollama) reach those services via the URLs you provide in the wizard or in your `.env`. There are two common topologies.

**1. Shared docker network (recommended).** Add subarr to the same docker network as your *arr stack. Then you can address each service by its container name on the default arr ports.

```yaml
networks:
  - safe-bridge       # or whatever your *arr stack already uses

# .env
SONARR_URL=http://sonarr:8989
RADARR_URL=http://radarr:7878
BAZARR_URL=http://bazarr:6767
SUBGEN_URL=http://subgen:9000
TAUTULLI_URL=http://tautulli:8181
PLEX_URL=http://plex:32400
```

This is what `docker-compose.yaml` in `deploy/templates/` ships with. DNS resolves container names within the network, so no IP addresses get baked in.

**2. Bypass: subarr on host network or different stack.** If subarr is deployed standalone (no shared network with the *arr stack), reach each service by host IP plus published port.

```env
SONARR_URL=http://192.168.1.10:8989
RADARR_URL=http://192.168.1.10:7878
BAZARR_URL=http://192.168.1.10:6767
SUBGEN_URL=http://192.168.1.10:9000
```

Common gotcha: **`localhost` from inside a container points at the container, not the host.** Use the host's LAN IP (or `host.docker.internal` on Docker Desktop) instead of `localhost`.

The onboarding wizard's "Test connection" button validates each URL against the live service before you commit it to settings. If it fails the chip stays red with the actual httpx error so you can fix the URL without restarting subarr.

---

## Performance and concurrency

Subgen supports running multiple Whisper workers in parallel via `CONCURRENT_TRANSCRIPTIONS` in the subgen container env. Subarr's pipeline handles N parallel jobs end-to-end: the Featured Queue surfaces N processing rows, each with its own progress bar and cancel button. Subarr's Settings panel surfaces the knob plus a VRAM budget table per Whisper model size so you can pick N correctly.

| Whisper model | VRAM per worker (float16) |
|---|---|
| tiny | roughly 1 GB |
| base | roughly 1 GB |
| small | roughly 2 GB |
| medium | roughly 5 GB |
| large or large-v3 | roughly 10 GB |

Leave 1 to 2 GB headroom for the container itself plus CUDA cache. Subarr's GPU widget on the Dashboard shows live VRAM utilisation so you can verify your N choice is right.

---

## Development

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

Migrations:

```bash
# Add a new migration:
touch src/subarr/migrations/004_my_change.sql
# Write your SQL. See src/subarr/migrations/README.md for conventions.
```

---

## Related

- [Bazarr](https://github.com/morpheus65535/bazarr): the librarian. Subarr reads its wanted list and writes back its scan-disk trigger.
- [McCloudS/subgen](https://github.com/McCloudS/subgen): the worker. Subarr drives it via the patches in [`coaxk/subarr-subgen`](https://github.com/coaxk/subarr-subgen).
- [subsyncarr](https://github.com/McCloudS/subsyncarr): the synchroniser. Recommended companion for sync issues subarr does not tackle. Mentioned in the wizard.

---

## License

MIT. See [LICENSE](LICENSE).

The patched subgen image (`ghcr.io/coaxk/subarr-subgen`) is a derived work of upstream McCloudS/subgen. See that repo's [`NOTICE`](https://github.com/coaxk/subarr-subgen/blob/main/NOTICE) for attribution.
