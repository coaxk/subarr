# Guided subgen setup — design

**Date:** 2026-06-13
**Status:** approved (brainstorm) → pending implementation plan
**Repos:** `coaxk/subarr` (the flow) + `coaxk/subarr-subgen` (the r9 image)

## Problem

A fresh `ghcr.io/coaxk/subarr-subgen` install runs with bare upstream defaults
except for one baked setting (the strongpad `CUSTOM_REGROUP`). The Phase-1
kwargs research (VAD/temperature/log_prob tuning that fights hallucination and
looping) is **not** in the image — it lives only in the maintainer's personal
compose. The Whisper model defaults to `medium`, the transcribe device defaults
to `cpu` even on a GPU box, and the user is offered **no** guidance at
onboarding (the wizard collects only the subgen URL). Net effect: the public
pitch "subarr-subgen ships tuned defaults" is half-true, and new users get a
sub-optimal, hardware-blind configuration with no path to fix it short of
hand-editing env vars they don't know exist.

This feature closes that gap in two coupled pieces:

1. **r9 image** — bake the hardware-*independent* tuning (kwargs + regroup) so
   "tuned defaults" becomes true, add a startup device-guard so the GPU is
   actually used, and expose a runtime-config endpoint so subarr can apply
   hardware-*dependent* settings without editing the host's Docker config.
2. **Guided setup flow** — detect the user's GPU, guide the model choice with
   VRAM-aware recommendations, auto-derive the compute type, and deliver the
   resulting config either as a copy-paste block (universal) or applied live
   (subarr-subgen r9+).

The launch is being held to ship this.

## The organizing principle

Subgen settings split into two kinds, and the split drives the whole design:

- **Hardware-independent** (`SUBGEN_KWARGS`, `CUSTOM_REGROUP`): identical on
  every machine. These belong **baked in the image** — they are just "the right
  defaults," not a decision.
- **Hardware-dependent** (`WHISPER_MODEL`, `TRANSCRIBE_DEVICE`, `COMPUTE_TYPE`):
  vary by GPU/VRAM. These belong in the **guided flow**, computed from detected
  hardware.

Within the hardware-dependent set, the three settings are *different kinds of
decision*:

- **Model** = a genuine user *preference* (quality vs speed vs VRAM has no
  single right answer) → guided choice, we recommend, the user picks.
- **Device** = a safety *floor* (use the GPU if it's there) → the image
  entrypoint guarantees it; the flow confirms.
- **compute_type** = a derived *optimization* (no preference axis; there is a
  correct answer given card + model) → auto-derived and explained, with an
  advanced override for the rare shared-GPU case.

---

# Part 1 — subarr-subgen r9 image

All baked values are `ENV` defaults, so any compose/env value the user sets
**wins** (same override semantics as the existing `CUSTOM_REGROUP` bake).

### 1.1 Baked tuning (hardware-independent)

- **`SUBGEN_KWARGS`** = the full maintainer-tuned block verbatim: temperature
  ladder `[0.0,0.2,0.4,0.6,0.8,1.0]`, `log_prob_threshold -0.8`,
  `length_penalty 1.0`, `patience 1.0`, `repetition_penalty 1.05`,
  `no_repeat_ngram_size 3`, `compression_ratio_threshold 2.4`, `beam_size 5`,
  the VAD timing block (`min_silence_duration_ms 500`, `speech_pad_ms 600`,
  `threshold 0.5`, `min_speech_duration_ms 250`), `no_speech_threshold 0.6`,
  and `condition_on_previous_text: false`. The contested knobs
  (`condition_on_previous_text`, VAD timing) get an explicit callout in the r9
  release notes.
- **`SUBGEN_KWARGS_LANG_JA`** = kept (the one survivor of the 17-override prune).
- **`CUSTOM_REGROUP`** = strongpad, unchanged from r8.

### 1.2 Baked release tag (#224)

The GHCR release tag (e.g. `v2026.05.3-r9`) is baked at build time and emitted
in the `/queue` capabilities body as `subarr_subgen_release_tag`. The subarr
side already reads this field (shipped in #225), so r9 is what lights up real
update detection and ends the permanent fake "update available" badge.

### 1.3 Entrypoint device-guard (new, ~5 lines)

At container startup, **only filling unset vars**:

- If a GPU is visible to the container (`nvidia-smi -L` exits 0) **and**
  `TRANSCRIBE_DEVICE` is unset → set `TRANSCRIBE_DEVICE=cuda`, and if
  `COMPUTE_TYPE` is also unset → `COMPUTE_TYPE=float16` (a safe floor; the flow
  refines it from `compute_cap`).
- Otherwise leave them at upstream defaults.
- **Never touches `WHISPER_MODEL`** — model is always the flow's guided choice
  (or upstream `medium` if nobody chooses).

Because it only fills *unset* vars, any value the flow or user sets always wins.
This kills the cpu-on-a-gpu-box footgun for flow users, flow-skippers, and
pure-subgen users alike.

### 1.4 Runtime-config endpoint (new — enables delivery mode C)

`POST /config` accepts `{model?: str, compute_type?: str}` and re-instantiates
subgen's model in place. Advertises a `runtime_config` capability in the
`/queue` caps body so subarr only offers live-apply when it's present.

**Safety contract (the load-bearing requirement):**

- subgen **guarantees it ends on a working model**. On any load failure (e.g.
  the requested model does not fit in VRAM), it rolls back to the
  previously-working model and returns a structured error
  (`{ok: false, reason: "oom"|"...", current_model: "<unchanged>"}`).
- A `/config` call must **never** leave subgen unable to transcribe. The exact
  unload/load ordering (load-new-before-drop-old vs drop-then-reload-fallback)
  is an implementation detail for the plan; the contract is only that the end
  state is always a working model and the outcome is reported.
- Model reload has a few-second cost and churns VRAM; this is acceptable for an
  occasional, deliberate config change.

### 1.5 Scope boundary

r9 does **not** bake `WHISPER_MODEL`/`TRANSCRIBE_DEVICE`/`COMPUTE_TYPE` as fixed
values (device/compute are handled by the guard's *conditional* fill; model is
the flow's job). r9 ships and soaks on subgen-next on its own timeline; the flow
targets r9 and degrades gracefully against an older image (no `release_tag`, no
`runtime_config` capability → the flow falls back to delivery mode A).

---

# Part 2 — Guided setup flow (subarr)

A single reusable **detect → guide → apply** component, mounted in two places:

- **Onboarding wizard step** ("Configure subgen", after subgen connectivity) —
  captures the high-value first-run moment.
- **Settings → "Subgen tuning" panel** — re-runnable any time (GPU upgrade,
  model change, after moving to subarr-subgen). Consistent with every other
  integration already being editable in Settings.

### 2.1 GPU detection cascade

Best signal first, each degrading gracefully:

1. **subarr's own `nvidia-smi`** (if subarr has detection passthrough) →
   `name, memory.total, memory.free, compute_cap, driver_version`. The gold
   input: authoritative model-fit and compute derivation.
2. **`docker info` Runtimes contains `nvidia`** → host is GPU-capable even
   though subarr can't read VRAM. Route to manual VRAM entry rather than
   assuming "no GPU".
3. **subgen `container_info` `HostConfig.DeviceRequests` + `Config.Env`**
   (already read today) → is subgen *currently* on a GPU, and what
   model/device/compute it runs now → feeds the current-vs-recommended diff.
4. **Manual fallback** (paste-parse) → see 2.2.

**Detection passthrough:** subarr's nvidia access needs only the `utility`
driver capability (`NVIDIA_DRIVER_CAPABILITIES=utility` / a device reservation
with `capabilities: [utility]`) — it queries the card but reserves **no** VRAM or
compute, so it never competes with subgen. It is **optional but recommended**:
enabled → automatic numeric detection; not enabled → the cascade falls to
docker-info + manual paste, and the flow still works.

### 2.2 Manual fallback (paste-parse)

When subarr cannot self-detect VRAM:

1. **Honest why:** "subarr can't read your GPU directly — it doesn't have
   detection access to the card. Run this and paste the result:"
2. **Command:** `nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader`
3. **Paste box** → parse `name`, `memory.total`, `compute_cap` from the CSV →
   full authoritative guidance (model fit *and* derived compute_type), identical
   to the auto path. No transcribing numbers, no unit confusion.
4. **PATH fallback line:** "or run it inside a GPU container:
   `docker exec <your-subgen-container> nvidia-smi --query-gpu=...`"
5. **Nudge footer:** "Want this automatic next time? Add detection access to
   subarr — [3-line `utility` compose snippet]."

### 2.3 Hardware logic

- **Model** — guided list; each model annotated with a fit indicator against
  detected VRAM ("large-v3 — recommended, fits your 12 GB", "medium — faster,
  lighter"). The user picks.
- **Device** — shown (cuda/cpu) from the entrypoint floor / detection; confirmed.
- **compute_type** — auto-derived and shown read-only with a plain-language
  reason, plus an "advanced: override":

  | Situation | compute_type | Reason shown |
  |---|---|---|
  | CPU (no GPU) | `int8` | "no GPU — int8 is the fast CPU mode" |
  | GPU, `compute_cap ≥ 7.0`, model fits | `float16` | "your <card> supports fast half-precision" |
  | GPU, `compute_cap ≥ 7.0`, VRAM tight | `int8_float16` | "fits <model> in less VRAM, keeps FP16 speed" |
  | GPU, `compute_cap < 7.0` | `int8` | "your <card> is pre-Volta, so float16 would be slower" |

  The override exists for the one real edge case: deliberately running int8 to
  leave VRAM for Plex/Ollama on a shared card.

### 2.4 VRAM headroom advisory

Whenever recommending a model against a GPU, render: VRAM total, **live-free
(snapshot)**, the chosen model's footprint, and the snapshot caveat. Fit math
keys off **`memory.total` with a headroom rule**, not the transient free figure;
the live figure is shown only as an advisory.

**Per-model VRAM footprint table** (CTranslate2/faster-whisper, plan establishes
exact figures; rough anchors: tiny/base ~1 GB, small ~2 GB, medium ~5 GB,
large-v3 ~6 GB at float16 with `beam_size 5`; int8 variants roughly half). The
"fits / tight" branch in §2.3 and the §2.5 C pre-check both read this table:
`footprint(model, compute_type) + headroom_margin` vs `memory.total`. Copy:

> **Live snapshot: X GB of Y GB in use right now.** This is a single moment in
> time, not your norm. Choose your model based on how much VRAM is *usually*
> free when subgen runs — if this card also handles Plex transcoding, Ollama, or
> anything else, leave headroom for those at their busiest, not for how quiet
> (or busy) it happens to be this second.

(Tight one-line variant for the Settings re-check.)

### 2.5 Delivery model

Routing is **capability-gated** (same pattern as the rest of subarr):

| Detected situation | Mode | What subarr produces |
|---|---|---|
| No subgen reachable | **A** | Full subarr-subgen `compose.yaml` (greenfield, recommended) with chosen model/device/compute + required mounts/GPU reservation |
| Vanilla subgen | **A** | Env *additions* to paste, + soft "switch to subarr-subgen" upsell |
| subarr-subgen < r9 (no `runtime_config`) | **A** | Env additions + "upgrade to r9 for hands-off" |
| subarr-subgen r9+ (`runtime_config` present) | **C** | "Apply now" → `POST /config`; A always available as the manual path |

- **Mode A (generate & apply, universal):** subarr emits the exact block; the
  user pastes and runs `docker compose up -d`. Zero new Docker privilege.
- **Mode C (live apply, subarr-subgen r9+ only):** subarr **pre-checks the VRAM
  fit** (2.4 math), then calls `POST /config`; subgen reloads under the §1.4
  safety contract; subarr surfaces the outcome ("applied" or "large-v3 didn't
  fit alongside what's on your GPU right now — kept your current model. Try
  int8_float16, or free up VRAM and retry").
- **Mode B (subarr recreates the container) is explicitly rejected** — changing
  the model needs a container recreate, which needs create/remove/start scope,
  breaking subarr's safe `INFO+CONTAINERS+NETWORKS+IMAGES`-read socket posture.
  C delivers the same hands-off UX via a subgen *capability* instead of Docker
  privilege escalation.

### 2.6 Wizard step sequence (the shared component)

1. **Detect** — run the cascade; show what was found (GPU + VRAM + compute_cap,
   or "no GPU", or "GPU-capable host — paste your nvidia-smi", or manual prompt).
2. **Confirm hardware** — detected card or manual paste; device floor shown.
3. **Choose model** — guided list with per-model fit indicators; VRAM advisory
   rendered here.
4. **Derived compute_type** — read-only with the "why"; advanced override.
5. **Review** — current-vs-recommended **diff** (from `container_info`): what
   subgen runs now vs what we'll set, with a note that kwargs/regroup come baked
   from r9.
6. **Apply** — branches on routing (2.5): "Apply now" (C) and/or the generated
   copy-paste block (A) with instructions.

---

## Testing strategy

- **r9 image:** unit-test the entrypoint device-guard (GPU-visible+unset → cuda;
  set → untouched; no-GPU → cpu); `/config` happy path + rollback-on-OOM
  contract (mock a load failure, assert the previous model stays live and the
  error is structured); caps body includes `release_tag` + `runtime_config`.
  Soak on subgen-next before promotion.
- **Detection cascade:** unit-test each tier and the fallthrough order with
  faked nvidia-smi output, faked `docker info` runtimes, and faked
  `container_info`; parse-the-pasted-CSV path with real sample strings.
- **compute_type derivation:** table-test every row of the 2.3 matrix
  (CPU→int8; ≥7.0 fit→float16; ≥7.0 tight→int8_float16; <7.0→int8).
- **VRAM fit math:** total-with-headroom vs model footprints; advisory renders
  the snapshot + caveat.
- **Delivery routing:** the 2.5 matrix — each detected situation yields the
  right mode and output; C pre-check blocks a known-too-big model before calling.
- **Component reuse:** the same flow component renders correctly in both the
  onboarding mount and the Settings mount.

## Implementation phasing

1. **r9 image** (subarr-subgen): bake + device-guard + release-tag + `/config`
   endpoint. Build, soak on subgen-next.
2. **Flow** (subarr): detection cascade, hardware logic, advisory, A + C
   delivery, shared component in onboarding + Settings. Targets r9; degrades to
   A-only against older images.

The flow can be built against a soaking r9 on subgen-next; both promote together
once r9 clears soak.

## Banked follow-ups (explicitly out of scope for this spec)

- **subarr-as-GPU-historian:** subarr already polls the GPU; accumulate rolling
  average + peak VRAM, ideally sampled *while subgen is transcribing*, and
  upgrade the §2.4 advisory from a snapshot-plus-nudge into measured data ("your
  free VRAM during subgen jobs averages 6 GB, peaked at 11"). No rework of the
  advisory component — it just gains a data source.

## Decisions log (every fork settled in brainstorm)

- Scope: r9 bake first (foundation), flow on top; one design doc, phased build.
- Baked kwargs: full personal block incl. `condition_on_previous_text: false`,
  debated knobs called out in r9 notes. Keep JA per-language override.
- Image smartness: minimal entrypoint device-guard (B), model always a guided
  user choice.
- compute_type: auto-derived + explained + advanced override (not a second
  user choice).
- VRAM: fit math off total-with-headroom; live-free shown as snapshot advisory
  with "think in your norm" wording.
- Delivery: A (universal) + C (subarr-subgen runtime-switch); B-recreate
  rejected. Build A+C together.
- Where it lives: both onboarding + Settings, one shared component.
- C safety: subarr pre-checks fit; subgen rolls back to a working model on
  failure and reports it.
