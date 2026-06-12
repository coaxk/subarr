# Guided Subgen Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship hardware-matched subgen configuration: the r9 image bakes the tuned defaults + a runtime-config endpoint, and subarr gains a guided detect→guide→apply flow (onboarding + Settings) that recommends model/device/compute from detected GPU hardware and applies it via copy-paste (A) or live API call (C).

**Architecture:** Two phased tracks. Track 1 (repo `C:\Projects\subarr-subgen`): Dockerfile ENV bakes (kwargs), a release-tag build-arg, an entrypoint device-guard patch, and a `/config` runtime-switch patch with a rollback safety contract. Track 2 (repo `C:\Projects\subarr`): a pure hardware-logic module (CSV parse, compute derivation, VRAM fit), a detection cascade router, config generators (mode A), live-apply (mode C, capability-gated), and one shared React component mounted in the onboarding wizard and Settings.

**Tech Stack:** Track 1: bash entrypoint, git-apply patch stack against `upstream/subgen.py` (FastAPI), Dockerfile. Track 2: FastAPI + pytest (TDD), React JSX → esbuild bundles.

**Spec:** `docs/superpowers/specs/2026-06-13-guided-subgen-setup-design.md`

**Conventions that apply to every task:**
- TDD: failing test first, then minimal implementation, then green, then commit.
- subarr tests run as: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/<file> -q` (PowerShell) or `PYTHONPATH=src python -m pytest tests/<file> -q` (bash).
- Lint before any "done": `ruff check src/subarr tests` and `ruff format <touched files>`.
- subarr commits via PR; merge with `gh pr merge N --squash --admin` on green. subarr-subgen work lands on a branch, PR'd the same way.
- subarr-subgen patch-authoring loop (used by Tasks 3–4):
  1. `./scripts/apply-patches.sh` (resets upstream to pinned commit, applies series)
  2. edit `upstream/subgen.py` (or `upstream/entrypoint.sh`)
  3. `git -C upstream diff > patches/00NN-<name>.patch`, then hand-edit the patch header to match existing patches (From/Date/Subject + prose description)
  4. add filename to `patches/series`
  5. `./scripts/apply-patches.sh && python3 scripts/validate-patched.py` must pass

---

# Track 1 — subarr-subgen r9 (cwd: `C:\Projects\subarr-subgen`, branch: `r9-guided-setup`)

### Task 1: Bake the tuned SUBGEN_KWARGS into the image

**Files:**
- Modify: `docker/Dockerfile` (after the existing `ENV CUSTOM_REGROUP=...` block, ~line 81)

- [ ] **Step 1: Create the branch**

```bash
cd /mnt/c/Projects/subarr-subgen && git checkout main && git pull && git checkout -b r9-guided-setup
```

- [ ] **Step 2: Add the ENV bake to docker/Dockerfile**

Insert directly below the `ENV CUSTOM_REGROUP=...` line:

```dockerfile
# [r9] Bake the Phase-1 kwargs research as the image default. These are the
# hardware-INDEPENDENT anti-failure settings (hallucination/looping fixes,
# each issue-cited in coaxk/subarr-subgen docs): temperature ladder re-enables
# Whisper's fallback, log_prob -0.8 tightens the hallucination gate,
# length_penalty 1.0 stops hallucinated bursts being cheaper than full
# transcriptions (faster-whisper #569), VAD timing fixes over-merging
# (faster-whisper #477). Overridable: any SUBGEN_KWARGS you set wins.
# Contested knobs (condition_on_previous_text=false, VAD timing) are called
# out in the r9 release notes.
ENV SUBGEN_KWARGS='{"no_speech_threshold": 0.6, "vad_filter": true, "vad_parameters": {"threshold": 0.5, "min_speech_duration_ms": 250, "min_silence_duration_ms": 500, "speech_pad_ms": 600}, "condition_on_previous_text": false, "beam_size": 5, "patience": 1.0, "length_penalty": 1.0, "repetition_penalty": 1.05, "no_repeat_ngram_size": 3, "compression_ratio_threshold": 2.4, "log_prob_threshold": -0.8, "temperature": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]}'
# [r9] The one per-language override that survived the 17-override prune
# (JA subtitle convention plausibly matches the longer length_penalty).
ENV SUBGEN_KWARGS_LANG_JA='{"patience": 1.0, "length_penalty": 1.3}'
```

- [ ] **Step 3: Verify the JSON parses**

```bash
python3 -c "import json; json.loads('{\"no_speech_threshold\": 0.6, \"vad_filter\": true, \"vad_parameters\": {\"threshold\": 0.5, \"min_speech_duration_ms\": 250, \"min_silence_duration_ms\": 500, \"speech_pad_ms\": 600}, \"condition_on_previous_text\": false, \"beam_size\": 5, \"patience\": 1.0, \"length_penalty\": 1.0, \"repetition_penalty\": 1.05, \"no_repeat_ngram_size\": 3, \"compression_ratio_threshold\": 2.4, \"log_prob_threshold\": -0.8, \"temperature\": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]}'); print('valid')"
```

Expected: `valid`. (Simpler: extract the value from the Dockerfile line and `json.loads` it in one piped command; either way the gate is "parses as JSON".)

- [ ] **Step 4: Commit**

```bash
git add docker/Dockerfile && git commit -m "r9: bake Phase-1 SUBGEN_KWARGS + JA override as image defaults"
```

### Task 2: Bake the release tag (closes the image side of subarr#224)

**Files:**
- Modify: `docker/Dockerfile` (ARG/ENV block, ~lines 32–40)
- Modify: `.github/workflows/release.yml` (the docker build step that passes `--build-arg`)

- [ ] **Step 1: Add ARG + ENV to docker/Dockerfile**

Below the existing `ARG PATCH_REV=unknown`:

```dockerfile
# [r9 / subarr#224] The GHCR release tag this image was built from
# (e.g. v2026.05.3-r9). Emitted in the /queue caps body as
# subarr_subgen_release_tag so subarr's update check compares
# like-for-like instead of patch-rev vs date-tag (which made every
# install show a permanent fake "update available").
ARG RELEASE_TAG=dev
ENV SUBARR_SUBGEN_RELEASE_TAG="${RELEASE_TAG}"
```

And a provenance label next to the existing labels:

```dockerfile
LABEL subarr.subgen.release_tag="${RELEASE_TAG}"
```

- [ ] **Step 2: Wire the build-arg in release.yml**

In `.github/workflows/release.yml`, the workflow already derives `TAG="${GITHUB_REF#refs/tags/}"` (~line 59). Find the docker build/push step's `build-args:` (or `--build-arg` list, alongside the existing `UPSTREAM_VERSION` + `PATCH_REV`) and add:

```yaml
RELEASE_TAG=${{ env.TAG }}
```

(Match the exact mechanism the file already uses for `PATCH_REV` — if it's `docker/build-push-action` use the `build-args:` block; if it's a raw `docker build` add `--build-arg "RELEASE_TAG=${TAG}"`.)

- [ ] **Step 3: Wire the build-arg in scripts/build.sh (local builds get `dev-<sha>`)**

In `scripts/build.sh`, add to the `docker build` invocation:

```bash
  --build-arg "RELEASE_TAG=dev-${PATCH_REV}" \
```

- [ ] **Step 4: Commit**

```bash
git add docker/Dockerfile .github/workflows/release.yml scripts/build.sh
git commit -m "r9: bake RELEASE_TAG build-arg as SUBARR_SUBGEN_RELEASE_TAG env (subarr#224)"
```

### Task 3: Patch 0021 — entrypoint device-guard (fill-only-unset)

**Files:**
- Create: `patches/0021-entrypoint-device-guard.patch` (via the patch-authoring loop)
- Modify: `patches/series`

- [ ] **Step 1: Apply the stack, then edit `upstream/entrypoint.sh`**

```bash
./scripts/apply-patches.sh
```

Edit `upstream/entrypoint.sh`: insert this block immediately after the shebang + before the `SAFETY CHECK` section (so it runs for root and non-root paths alike, and the exported vars survive the `exec`):

```bash
# ----------------------------------------------------------------
# [r9] DEVICE GUARD — use the GPU if it's there.
# Upstream defaults TRANSCRIBE_DEVICE=cpu, so a user who passes a GPU
# to the container but never configures subgen silently transcribes on
# CPU. Fill ONLY unset vars: anything the user (or subarr's guided
# setup) sets always wins. Never touches WHISPER_MODEL — model choice
# is a guided user decision, not a container default.
# ----------------------------------------------------------------
if [ -z "${TRANSCRIBE_DEVICE:-}" ]; then
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
        export TRANSCRIBE_DEVICE="cuda"
        if [ -z "${COMPUTE_TYPE:-}" ]; then
            # Safe floor; subarr's guided setup refines from compute_cap
            # (pre-Volta cards get int8 there).
            export COMPUTE_TYPE="float16"
        fi
        echo "[device-guard] GPU visible and TRANSCRIBE_DEVICE unset -> cuda (COMPUTE_TYPE=${COMPUTE_TYPE})"
    else
        echo "[device-guard] no GPU visible; leaving TRANSCRIBE_DEVICE at upstream default (cpu)"
    fi
fi
```

- [ ] **Step 2: Generate the patch + register it**

```bash
git -C upstream diff > patches/0021-entrypoint-device-guard.patch
```

Hand-edit the patch header to match the house format (compare `patches/0019-*.patch`): add the `From 0000…`/`From: subarr-subgen <subarr@localhost>`/`Date:`/`Subject: [PATCH] 0021: entrypoint device-guard` lines plus a prose paragraph (use the comment block text). Then append `0021-entrypoint-device-guard.patch` to `patches/series`.

- [ ] **Step 3: Verify idempotent re-apply**

```bash
./scripts/apply-patches.sh && python3 scripts/validate-patched.py
```

Expected: `applied 21 patch(es) cleanly.` and all VALIDATE gates `ok`.

- [ ] **Step 4: Shell-syntax gate**

```bash
bash -n upstream/entrypoint.sh && echo "entrypoint syntax ok"
```

Expected: `entrypoint syntax ok`

- [ ] **Step 5: Commit**

```bash
git add patches/0021-entrypoint-device-guard.patch patches/series
git commit -m "patch 0021: entrypoint device-guard - cuda when GPU visible and TRANSCRIBE_DEVICE unset"
```

### Task 4: Patch 0022 — `/config` runtime switch + release-tag/runtime_config in caps

**Files:**
- Create: `patches/0022-runtime-config-endpoint.patch` (via the patch-authoring loop)
- Modify: `patches/series`, `scripts/validate-patched.py`

Background for the engineer: `upstream/subgen.py` holds the model as a module global (`model = None`, line ~214) loaded lazily by `start_model()` (line ~1268) from the globals `whisper_model`, `transcribe_device`, `compute_type`. The `/queue` endpoint (`def queue_status()`, patched in by 0007 and extended through 0019) returns a body containing `subarr_subgen_patch_rev` and a `"capabilities": {...}` dict — patch 0019's hunk at `@@ -758,…` shows the exact insertion pattern.

- [ ] **Step 1: Edit `upstream/subgen.py` — bump patch_rev**

Top of file (the 0019 pattern): change `subarr_subgen_patch_rev = 'v4.13'` → `'v4.14'`.

- [ ] **Step 2: Add the release-tag global**

Next to the patch_rev line:

```python
# [v4.14 PATCH / subarr#224] The GHCR release tag baked at build time
# (Dockerfile ARG RELEASE_TAG -> ENV). 'dev' on local builds. Surfaced in
# /queue caps so subarr's update check compares tags like-for-like.
subarr_subgen_release_tag = os.getenv('SUBARR_SUBGEN_RELEASE_TAG', 'dev')
```

- [ ] **Step 3: Extend the `/queue` caps body**

In `queue_status()`'s return dict: add alongside `subarr_subgen_patch_rev`:

```python
        "subarr_subgen_release_tag": subarr_subgen_release_tag,
```

and inside the `"capabilities": {` dict (after `"ignore_forced_subtitles": ...`):

```python
            # [v4.14 PATCH] POST /config can switch model/compute_type at
            # runtime with rollback — subarr's guided setup live-apply.
            "runtime_config": True,
```

- [ ] **Step 4: Add the `/config` endpoint**

Insert after the `queue_status()` function (same FastAPI app object the other patched endpoints use — match `@app.get("/queue")`'s decorator style):

```python
# [v4.14 PATCH] Runtime model/compute switch for subarr's guided setup.
# SAFETY CONTRACT: this endpoint always ends on a WORKING model. On any
# load failure (bad name, OOM) it rolls back the globals and re-loads the
# previous model, and reports the outcome. Refuses while jobs are
# processing (a mid-job model swap would corrupt the run).
_ALLOWED_RUNTIME_MODELS = {
    'tiny', 'tiny.en', 'base', 'base.en', 'small', 'small.en',
    'medium', 'medium.en', 'large-v1', 'large-v2', 'large-v3',
    'large-v3-turbo', 'distil-large-v3',
}
_ALLOWED_COMPUTE_TYPES = {'auto', 'int8', 'int8_float16', 'float16', 'float32'}


@app.post("/config")
def runtime_config(model_name: str = Query(None, alias="model"),
                   compute: str = Query(None, alias="compute_type")):
    global whisper_model, compute_type, model
    if model_name is None and compute is None:
        return JSONResponse(status_code=400, content={
            "ok": False, "reason": "nothing_to_change",
            "current_model": whisper_model, "current_compute_type": compute_type})
    if model_name is not None and model_name not in _ALLOWED_RUNTIME_MODELS:
        return JSONResponse(status_code=400, content={
            "ok": False, "reason": "unknown_model",
            "current_model": whisper_model, "current_compute_type": compute_type})
    if compute is not None and compute not in _ALLOWED_COMPUTE_TYPES:
        return JSONResponse(status_code=400, content={
            "ok": False, "reason": "unknown_compute_type",
            "current_model": whisper_model, "current_compute_type": compute_type})
    if len(task_queue.queue) > 0 or any(_processing.values()):
        return JSONResponse(status_code=409, content={
            "ok": False, "reason": "busy",
            "detail": "jobs queued or processing; retry when idle",
            "current_model": whisper_model, "current_compute_type": compute_type})

    prev_model_name, prev_compute = whisper_model, compute_type
    try:
        # Drop the live model so start_model() reloads from the new globals.
        if model is not None:
            del model
            model = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()
        if model_name is not None:
            whisper_model = model_name
        if compute is not None:
            compute_type = compute
        start_model()  # raises on bad load / OOM
        logging.info(f"[runtime_config] switched: model={whisper_model} compute_type={compute_type}")
        return {"ok": True, "model": whisper_model, "compute_type": compute_type}
    except Exception as e:
        # ROLLBACK: restore globals and reload the previous model so we
        # always end on a working configuration.
        logging.error(f"[runtime_config] load failed ({e}); rolling back to {prev_model_name}/{prev_compute}")
        whisper_model, compute_type = prev_model_name, prev_compute
        model = None
        try:
            start_model()
        except Exception as e2:
            logging.error(f"[runtime_config] ROLLBACK LOAD ALSO FAILED: {e2}")
        return JSONResponse(status_code=422, content={
            "ok": False,
            "reason": "oom" if "out of memory" in str(e).lower() else "load_failed",
            "detail": str(e)[:300],
            "current_model": whisper_model, "current_compute_type": compute_type})
```

Adaptation notes (verify against the applied tree, don't assume): (a) the queue/processing idle check must use whatever structures patch 0007 actually tracks — search `subgen.py` for `_queued` / `_processing` and mirror how `queue_status()` counts them; (b) `Query` import — match how patched endpoints already import from fastapi; (c) `torch`/`gc` are already imported at module top (used by patch 0002's hunk).

- [ ] **Step 5: Generate patch, register, validate**

```bash
git -C upstream diff > patches/0022-runtime-config-endpoint.patch
# hand-edit header like 0019's; prose = the SAFETY CONTRACT paragraph
# append to patches/series
./scripts/apply-patches.sh && python3 scripts/validate-patched.py
```

- [ ] **Step 6: Extend validate-patched.py gates**

Add to `scripts/validate-patched.py` (text gates, matching the existing style):

```python
    if '@app.post("/config")' not in text:
        fail("'/config' endpoint missing (patch 0022)")
    ok("'/config' endpoint present")
    if '"runtime_config": True' not in text:
        fail("runtime_config capability missing (patch 0022)")
    ok("runtime_config capability present")
    if 'subarr_subgen_release_tag' not in text:
        fail("release tag emission missing (patch 0022)")
    ok("release tag emission present")
```

Run `python3 scripts/validate-patched.py` → all gates ok.

- [ ] **Step 7: Commit**

```bash
git add patches/0022-runtime-config-endpoint.patch patches/series scripts/validate-patched.py
git commit -m "patch 0022: POST /config runtime model switch with rollback + release-tag in caps (v4.14)"
```

### Task 5: Build, smoke-test, and PR the r9 candidate

**Files:** none new (build + verify + RELEASES.md)

- [ ] **Step 1: Local build**

```bash
./scripts/build.sh
```

Expected: ends with `built: subarr-subgen:dev-2026.05.3-<sha>`.

- [ ] **Step 2: Throwaway-container smoke — env bakes + device guard + caps + /config rollback**

```bash
# (1) Baked env visible
docker run --rm --entrypoint env subarr-subgen:dev | grep -E "SUBGEN_KWARGS=|SUBGEN_KWARGS_LANG_JA=|CUSTOM_REGROUP=|SUBARR_SUBGEN_RELEASE_TAG="
# expect all four lines, RELEASE_TAG=dev-<sha>

# (2) Run with GPU, no TRANSCRIBE_DEVICE set -> guard fires
docker run -d --name r9smoke --gpus all -p 9011:9000 subarr-subgen:dev
sleep 25 && docker logs r9smoke 2>&1 | grep "device-guard"
# expect: "[device-guard] GPU visible and TRANSCRIBE_DEVICE unset -> cuda (COMPUTE_TYPE=float16)"

# (3) Caps body carries the new fields
curl -s http://localhost:9011/queue | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['subarr_subgen_patch_rev'], d['subarr_subgen_release_tag'], d['capabilities']['runtime_config'])"
# expect: v4.14 dev-<sha> True

# (4) /config rollback on a bad model name (no GPU risk; pure validation path)
curl -s -X POST "http://localhost:9011/config?model=not-a-model" | python3 -m json.tool
# expect: {"ok": false, "reason": "unknown_model", "current_model": "medium", ...}

# (5) /config real switch (small -> verifies load+report path on the live GPU)
curl -s -X POST "http://localhost:9011/config?model=small" | python3 -m json.tool
# expect: {"ok": true, "model": "small", "compute_type": "float16"}

# (6) explicit env still wins over the guard
docker rm -f r9smoke
docker run -d --name r9smoke2 --gpus all -e TRANSCRIBE_DEVICE=cpu subarr-subgen:dev
sleep 10 && docker logs r9smoke2 2>&1 | grep -c "device-guard" ; docker exec r9smoke2 env | grep TRANSCRIBE_DEVICE
# expect: no guard "-> cuda" line fired for device; TRANSCRIBE_DEVICE=cpu
docker rm -f r9smoke2
```

- [ ] **Step 3: RELEASES.md candidate row + the contested-knobs callout**

Add an r9 row to `RELEASES.md` in the existing table style: baked SUBGEN_KWARGS (with the `condition_on_previous_text: false` + VAD-timing callout sentence), device-guard, release-tag (#224 fix), `/config` + `runtime_config` capability, patch_rev v4.14.

- [ ] **Step 4: Commit + PR**

```bash
git add RELEASES.md && git commit -m "RELEASES: r9 candidate notes"
git push -u origin r9-guided-setup
gh pr create --title "r9: baked tuned defaults + device guard + runtime /config + release tag" --body "Implements Track 1 of subarr's guided-subgen-setup spec. Closes the image side of coaxk/subarr#224."
```

Merge on green; **deploy `subarr-subgen:dev` to subgen-next (port 9008) for soak** — Track 2's mode-C work tests against it.

---

# Track 2 — subarr flow (cwd: `C:\Projects\subarr`, branch: `feat/guided-subgen-setup`)

### Task 6: Hardware logic module (pure functions, the core brain)

**Files:**
- Create: `src/subarr/subgen_hardware.py`
- Test: `tests/test_subgen_hardware.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Guided subgen setup: pure hardware logic (spec §2.3/§2.4).

No I/O here — parsing nvidia-smi CSV, deriving compute_type, and VRAM fit
math are all table-driven pure functions so every row of the spec matrix
is directly testable.
"""

from __future__ import annotations

import pytest


# ── nvidia-smi CSV parsing (auto-detect AND the paste box use the same parser)

def test_parse_smi_csv_full_row(subarr_env):
    from subarr.subgen_hardware import parse_smi_csv

    gpu = parse_smi_csv("NVIDIA GeForce RTX 3060, 12288, 4343, 8.6, 610.52")
    assert gpu.name == "NVIDIA GeForce RTX 3060"
    assert gpu.vram_total_mb == 12288
    assert gpu.vram_free_mb == 4343
    assert gpu.compute_cap == 8.6
    assert gpu.driver_version == "610.52"


def test_parse_smi_csv_paste_variant_without_free_and_driver(subarr_env):
    # The manual paste command asks for name,memory.total,compute_cap only.
    from subarr.subgen_hardware import parse_smi_csv

    gpu = parse_smi_csv("NVIDIA GeForce GTX 1070, 8192, 6.1")
    assert gpu.name == "NVIDIA GeForce GTX 1070"
    assert gpu.vram_total_mb == 8192
    assert gpu.vram_free_mb is None
    assert gpu.compute_cap == 6.1


def test_parse_smi_csv_units_suffix_tolerated(subarr_env):
    # nvidia-smi without ,nounits appends " MiB" — paste box must cope.
    from subarr.subgen_hardware import parse_smi_csv

    gpu = parse_smi_csv("NVIDIA RTX A4000, 16376 MiB, 8.6")
    assert gpu.vram_total_mb == 16376


def test_parse_smi_csv_garbage_returns_none(subarr_env):
    from subarr.subgen_hardware import parse_smi_csv

    assert parse_smi_csv("") is None
    assert parse_smi_csv("bash: nvidia-smi: command not found") is None


# ── compute_type derivation (every row of the spec §2.3 matrix)

@pytest.mark.parametrize(
    "compute_cap,vram_mb,model,expected,reason_fragment",
    [
        (None, None, "small", "int8", "no GPU"),                     # CPU
        (8.6, 12288, "large-v3", "float16", "half-precision"),       # fits
        (8.6, 6144, "large-v3", "int8_float16", "less VRAM"),        # tight
        (6.1, 8192, "medium", "int8", "pre-Volta"),                  # old card
    ],
)
def test_derive_compute_type_matrix(subarr_env, compute_cap, vram_mb, model, expected, reason_fragment):
    from subarr.subgen_hardware import derive_compute_type

    d = derive_compute_type(compute_cap=compute_cap, vram_total_mb=vram_mb, model=model)
    assert d.compute_type == expected
    assert reason_fragment.lower() in d.reason.lower()


# ── model fit (total-with-headroom, never the live-free snapshot)

def test_model_fit_indicators(subarr_env):
    from subarr.subgen_hardware import model_fit

    # 12GB card: large-v3 (~5.5GB + headroom) fits comfortably
    assert model_fit("large-v3", "float16", vram_total_mb=12288) == "fits"
    # 6GB card: large-v3 float16 is tight (int8_float16 territory)
    assert model_fit("large-v3", "float16", vram_total_mb=6144) == "tight"
    # 2GB card: large-v3 doesn't fit at all
    assert model_fit("large-v3", "float16", vram_total_mb=2048) == "no_fit"
    # CPU (no VRAM): every model "fits" (speed is the cost, not memory)
    assert model_fit("large-v3", "int8", vram_total_mb=None) == "fits"


def test_recommend_model_by_vram(subarr_env):
    from subarr.subgen_hardware import recommend_model

    assert recommend_model(vram_total_mb=12288) == "large-v3"   # >=10GB
    assert recommend_model(vram_total_mb=8192) == "medium"      # 6-10GB
    assert recommend_model(vram_total_mb=4096) == "small"       # <6GB
    assert recommend_model(vram_total_mb=None) == "small"       # CPU
```

- [ ] **Step 2: Run to verify failure**

`PYTHONPATH=src python -m pytest tests/test_subgen_hardware.py -q` → FAIL (`ModuleNotFoundError: subarr.subgen_hardware`).

- [ ] **Step 3: Implement `src/subarr/subgen_hardware.py`**

```python
"""Guided subgen setup — pure hardware logic (spec §2.3/§2.4).

Three responsibilities, all side-effect-free so the spec matrices are
directly table-testable:
  - parse_smi_csv: one parser for BOTH the auto-detected nvidia-smi line
    and the user-pasted CSV (manual fallback) — same code path, same trust.
  - derive_compute_type: compute_type is a derived optimization, not a
    user preference. There is a correct answer per card + model.
  - model_fit / recommend_model: fit math keys off memory.TOTAL with a
    headroom rule — the live free figure is a snapshot shown only as an
    advisory (the card may be momentarily busy or momentarily idle).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Approximate steady-state VRAM footprint in MB (CTranslate2/faster-whisper,
# beam_size 5). float16 figures; int8 variants roughly halve. Deliberately
# conservative — the headroom margin below absorbs measurement noise.
MODEL_FOOTPRINT_MB: dict[str, int] = {
    "tiny": 1000,
    "base": 1200,
    "small": 1800,
    "medium": 3500,
    "large-v3": 5500,
}
# Room left for CUDA context + other tenants (Plex/Ollama bursts).
HEADROOM_MB = 1536
# compute_cap >= 7.0 (Volta+) has native FP16; below it float16 is emulated
# and SLOWER than int8.
FP16_MIN_COMPUTE_CAP = 7.0

MODEL_VRAM_RECOMMEND_MB = (
    ("large-v3", 10240),  # >=10GB
    ("medium", 6144),     # >=6GB
    ("small", 0),         # anything less / CPU
)


@dataclass(frozen=True)
class GpuInfo:
    name: str
    vram_total_mb: int
    compute_cap: float | None = None
    vram_free_mb: int | None = None
    driver_version: str | None = None


@dataclass(frozen=True)
class ComputeDerivation:
    compute_type: str
    reason: str


def _num(field: str) -> float | None:
    m = re.search(r"[\d.]+", field)
    return float(m.group()) if m else None


def parse_smi_csv(line: str) -> GpuInfo | None:
    """Parse one nvidia-smi CSV row: `name, memory.total[, memory.free],
    compute_cap[, driver_version]`. Tolerates ' MiB' suffixes (paste without
    ,nounits). Returns None on anything unparseable — callers fall through
    to manual entry, never crash."""
    parts = [p.strip() for p in (line or "").strip().splitlines()[0].split(",")] if (line or "").strip() else []
    if len(parts) < 3:
        return None
    name = parts[0]
    total = _num(parts[1])
    if not name or total is None or total <= 0:
        return None
    # Disambiguate the two shapes by column count:
    #   3 cols: name, total, compute_cap            (the paste command)
    #   5 cols: name, total, free, cap, driver      (the auto-detect query)
    if len(parts) >= 5:
        free, cap, driver = _num(parts[2]), _num(parts[3]), parts[4]
    else:
        free, cap, driver = None, _num(parts[2]), None
    if cap is None or cap > 20:  # compute_cap is single-digit.dot; a big number here means columns are off
        return None
    return GpuInfo(
        name=name,
        vram_total_mb=int(total),
        vram_free_mb=int(free) if free is not None else None,
        compute_cap=cap,
        driver_version=driver,
    )


def model_fit(model: str, compute_type: str, *, vram_total_mb: int | None) -> str:
    """'fits' | 'tight' | 'no_fit' against TOTAL VRAM with headroom.
    CPU (vram None) always 'fits' — the cost there is speed, not memory."""
    if vram_total_mb is None:
        return "fits"
    footprint = MODEL_FOOTPRINT_MB.get(model.split(".")[0].replace("-turbo", ""), MODEL_FOOTPRINT_MB["large-v3"])
    if compute_type.startswith("int8"):
        footprint = footprint // 2
    if footprint + HEADROOM_MB <= vram_total_mb:
        return "fits"
    if footprint <= vram_total_mb:
        return "tight"
    return "no_fit"


def derive_compute_type(*, compute_cap: float | None, vram_total_mb: int | None, model: str) -> ComputeDerivation:
    """Spec §2.3 matrix. compute_cap None == no GPU."""
    if compute_cap is None or vram_total_mb is None:
        return ComputeDerivation("int8", "no GPU — int8 is the fast CPU mode")
    if compute_cap < FP16_MIN_COMPUTE_CAP:
        return ComputeDerivation(
            "int8",
            f"compute capability {compute_cap:g} is pre-Volta, so float16 would actually be slower — int8 is the fast path",
        )
    if model_fit(model, "float16", vram_total_mb=vram_total_mb) == "fits":
        return ComputeDerivation("float16", "your GPU supports fast half-precision")
    return ComputeDerivation(
        "int8_float16",
        f"fits {model} in less VRAM while keeping FP16 speed",
    )


def recommend_model(*, vram_total_mb: int | None) -> str:
    if vram_total_mb is None:
        return "small"
    for model, min_mb in MODEL_VRAM_RECOMMEND_MB:
        if vram_total_mb >= min_mb:
            return model
    return "small"
```

- [ ] **Step 4: Run to green**

`PYTHONPATH=src python -m pytest tests/test_subgen_hardware.py -q` → all pass. `ruff check src/subarr/subgen_hardware.py tests/test_subgen_hardware.py`.

- [ ] **Step 5: Commit**

```bash
git checkout -b feat/guided-subgen-setup
git add src/subarr/subgen_hardware.py tests/test_subgen_hardware.py
git commit -m "feat: subgen hardware logic - smi parse, compute derivation, VRAM fit (guided setup core)"
```

### Task 7: Detection inputs — gpu router compute_cap + docker nvidia-runtime + subgen current-config

**Files:**
- Modify: `src/subarr/routers/gpu.py` (`_GPU_FIELDS`, ~line 39, and the row parse in `gpu_status()`)
- Modify: `src/subarr/docker_client.py` (add two methods to `DockerOps`)
- Test: `tests/test_subgen_setup_detection.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Detection inputs for guided subgen setup (spec §2.1 tiers 1-3)."""

from __future__ import annotations


def test_gpu_fields_include_compute_cap(subarr_env):
    from subarr.routers.gpu import _GPU_FIELDS

    assert "compute_cap" in _GPU_FIELDS
    assert "driver_version" in _GPU_FIELDS


def test_docker_nvidia_runtime_available(subarr_env):
    from subarr.docker_client import DockerOps

    ops = DockerOps()

    class _FakeClient:
        def info(self):
            return {"Runtimes": {"runc": {}, "nvidia": {"path": "nvidia-container-runtime"}}}

    ops._client = _FakeClient()
    assert ops.nvidia_runtime_available() is True

    class _FakeNoNvidia:
        def info(self):
            return {"Runtimes": {"runc": {}}}

    ops._client = _FakeNoNvidia()
    assert ops.nvidia_runtime_available() is False


def test_docker_nvidia_runtime_unavailable_docker_down(subarr_env):
    from subarr.docker_client import DockerOps

    ops = DockerOps()

    class _Boom:
        def info(self):
            raise RuntimeError("socket gone")

    ops._client = _Boom()
    # Fail-soft: detection tier degrades, never raises into the wizard.
    assert ops.nvidia_runtime_available() is None


def test_subgen_current_config_reads_env_and_gpu(subarr_env):
    from subarr.docker_client import DockerOps

    ops = DockerOps()

    class _FakeContainer:
        name = "subgen"
        short_id = "abc123"
        attrs = {
            "State": {"Status": "running", "Running": True, "StartedAt": "x"},
            "Config": {
                "Image": "ghcr.io/coaxk/subarr-subgen:2026.05.3-r8",
                "Env": [
                    "WHISPER_MODEL=large-v3",
                    "TRANSCRIBE_DEVICE=cuda",
                    "COMPUTE_TYPE=float16",
                    "PATH=/usr/bin",
                ],
            },
            "HostConfig": {"DeviceRequests": [{"Driver": "nvidia", "Count": 1}]},
        }

    class _FakeContainers:
        def get(self, name):
            return _FakeContainer()

    class _FakeClient:
        containers = _FakeContainers()

    ops._client = _FakeClient()
    import asyncio

    cfg = asyncio.get_event_loop().run_until_complete(ops.subgen_current_config())
    assert cfg["whisper_model"] == "large-v3"
    assert cfg["transcribe_device"] == "cuda"
    assert cfg["compute_type"] == "float16"
    assert cfg["has_gpu_reservation"] is True
    assert cfg["image"].endswith(":2026.05.3-r8")
```

- [ ] **Step 2: Run to verify failure**

`PYTHONPATH=src python -m pytest tests/test_subgen_setup_detection.py -q` → FAILs (missing attr/method).

- [ ] **Step 3: Implement**

`src/subarr/routers/gpu.py` — extend the query (existing `_GPU_FIELDS` tuple, keep field order in sync with the row parse below it):

```python
_GPU_FIELDS = (
    "name,memory.used,memory.total,memory.free,"
    "utilization.gpu,utilization.memory,temperature.gpu,"
    # ...existing fields unchanged...
    # [guided setup] compute_cap derives compute_type (>=7.0 -> native fp16);
    # driver_version supports a too-old-driver warning.
    "compute_cap,driver_version"
)
```

and append to the parsed response dict in `gpu_status()` (mirroring the existing `_parse_float` use):

```python
            "compute_cap": _parse_float(parts[<next index>]),
            "driver_version": parts[<next index + 1>],
```

(The engineer must count the existing CSV columns — the indexes follow the current `parts[...]` accesses; nvidia-smi returns columns strictly in query order.)

`src/subarr/docker_client.py` — add to `DockerOps`:

```python
    def nvidia_runtime_available(self) -> bool | None:
        """Detection tier 2 (spec §2.1): does the Docker host have the
        nvidia runtime registered? True/False, or None when Docker itself
        is unreachable (fail-soft — the wizard degrades to manual entry,
        it never errors)."""
        try:
            info = self._get().info()
            return "nvidia" in (info.get("Runtimes") or {})
        except Exception as e:
            log.debug("nvidia_runtime_available failed (non-fatal): %s", e)
            return None

    async def subgen_current_config(self) -> dict:
        """Detection tier 3 (spec §2.1): subgen's CURRENT transcription env +
        GPU reservation, for the current-vs-recommended diff. Reads the same
        container attrs container_info() already trusts."""

        def _do() -> dict:
            client = self._get()
            try:
                c = client.containers.get(settings.subgen_container)
            except NotFound:
                raise DockerUnavailable(f"container {settings.subgen_container!r} not found")
            attrs = c.attrs
            env_list = ((attrs.get("Config") or {}).get("Env")) or []
            env = dict(e.split("=", 1) for e in env_list if "=" in e)
            device_requests = ((attrs.get("HostConfig") or {}).get("DeviceRequests")) or []
            has_gpu = any((d or {}).get("Driver") == "nvidia" for d in device_requests)
            return {
                "whisper_model": env.get("WHISPER_MODEL"),
                "transcribe_device": env.get("TRANSCRIBE_DEVICE"),
                "compute_type": env.get("COMPUTE_TYPE"),
                "has_gpu_reservation": has_gpu,
                "image": (attrs.get("Config") or {}).get("Image"),
            }

        return await asyncio.to_thread(_do)
```

- [ ] **Step 4: Run to green + lint**

`PYTHONPATH=src python -m pytest tests/test_subgen_setup_detection.py tests/ -q -k "gpu or docker"` → pass. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/gpu.py src/subarr/docker_client.py tests/test_subgen_setup_detection.py
git commit -m "feat: detection inputs - compute_cap in gpu query, nvidia runtime probe, subgen current-config reader"
```

### Task 8: subgen client — `runtime_config` capability + `post_config()`

**Files:**
- Modify: `src/subarr/subgen_client.py` (SubgenCapabilities + probe parser + new method)
- Test: `tests/test_subgen_setup_client.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Mode C plumbing: runtime_config capability + POST /config call."""

from __future__ import annotations

import httpx
import pytest


def _client_with(handler):
    from subarr.subgen_client import SubgenClient

    c = SubgenClient(base_url="http://subgen.test")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://subgen.test")
    return c


@pytest.mark.asyncio
async def test_probe_parses_runtime_config_capability(subarr_env):
    def handler(req):
        if req.url.path == "/status":
            return httpx.Response(200, json={"version": "2026.05.3"})
        if req.url.path == "/queue":
            return httpx.Response(200, json={
                "queued": [], "processing": [],
                "subarr_subgen_patch_rev": "v4.14",
                "subarr_subgen_release_tag": "v2026.05.3-r9",
                "capabilities": {"runtime_config": True},
            })
        return httpx.Response(404)

    caps = await _client_with(handler).probe_capabilities()
    assert caps.runtime_config is True
    assert caps.release_tag == "v2026.05.3-r9"


@pytest.mark.asyncio
async def test_probe_defaults_runtime_config_false_on_old_image(subarr_env):
    def handler(req):
        if req.url.path == "/status":
            return httpx.Response(200, json={"version": "2026.05.3"})
        if req.url.path == "/queue":
            return httpx.Response(200, json={"queued": [], "processing": [],
                                             "subarr_subgen_patch_rev": "v4.13",
                                             "capabilities": {}})
        return httpx.Response(404)

    caps = await _client_with(handler).probe_capabilities()
    assert caps.runtime_config is False


@pytest.mark.asyncio
async def test_post_config_success_and_failure_shapes(subarr_env):
    def ok_handler(req):
        assert req.url.path == "/config"
        assert req.url.params["model"] == "large-v3"
        return httpx.Response(200, json={"ok": True, "model": "large-v3", "compute_type": "float16"})

    out = await _client_with(ok_handler).post_config(model="large-v3", compute_type="float16")
    assert out == (200, {"ok": True, "model": "large-v3", "compute_type": "float16"})

    def oom_handler(req):
        return httpx.Response(422, json={"ok": False, "reason": "oom",
                                         "current_model": "medium", "current_compute_type": "float16"})

    status, body = await _client_with(oom_handler).post_config(model="large-v3")
    assert status == 422 and body["reason"] == "oom" and body["current_model"] == "medium"
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement**

In `SubgenCapabilities`: add field `runtime_config: bool = False` (+ `to_dict()` key + `unreachable()` kwarg, matching how `ignore_forced_subtitles` is threaded). In `probe_capabilities()`'s caps_block parsing add `runtime_config = bool(caps_block.get("runtime_config"))` and pass it through the constructor. (`release_tag` parsing already exists from #225.)

New method on `SubgenClient` (mirror `batch()`'s param/return style):

```python
    async def post_config(
        self, *, model: str | None = None, compute_type: str | None = None
    ) -> tuple[int, dict]:
        """POST /config — runtime model/compute switch (subarr-subgen r9+,
        gated on caps.runtime_config). Returns (status_code, body). subgen
        guarantees it ends on a working model (rollback contract); callers
        surface body['reason']/'current_model' on failure."""
        params: dict[str, str] = {}
        if model:
            params["model"] = model
        if compute_type:
            params["compute_type"] = compute_type
        try:
            r = await self._client.post("/config", params=params)
        except httpx.HTTPError as e:
            raise SubgenUnavailable(f"subgen /config failed: {e}") from e
        try:
            body = r.json()
        except ValueError:
            body = {}
        return r.status_code, body
```

- [ ] **Step 4: Green + lint**, **Step 5: Commit**

```bash
git add src/subarr/subgen_client.py tests/test_subgen_setup_client.py
git commit -m "feat: subgen client runtime_config capability + post_config (mode C plumbing)"
```

### Task 9: Mode-A generators — full compose + env additions

**Files:**
- Create: `src/subarr/subgen_config_gen.py`
- Test: `tests/test_subgen_config_gen.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Mode A artifacts (spec §2.5): exact text the user pastes."""

from __future__ import annotations


def test_env_additions_block(subarr_env):
    from subarr.subgen_config_gen import generate_env_additions

    text = generate_env_additions(model="large-v3", device="cuda", compute_type="float16")
    assert "WHISPER_MODEL: large-v3" in text
    assert "TRANSCRIBE_DEVICE: cuda" in text
    assert "COMPUTE_TYPE: float16" in text
    # compose `environment:` mapping style — indented keys, no leading dashes
    assert not text.strip().startswith("-")


def test_full_compose_gpu(subarr_env):
    from subarr.subgen_config_gen import generate_full_compose

    text = generate_full_compose(
        model="large-v3", device="cuda", compute_type="float16",
        media_host_path="/mnt/media", port=9000, gpu=True,
    )
    assert "image: ghcr.io/coaxk/subarr-subgen:latest" in text
    assert "WHISPER_MODEL: large-v3" in text
    assert "driver: nvidia" in text
    assert "/mnt/media:/media" in text
    assert "9000:9000" in text
    # kwargs/regroup must NOT appear — they come baked from r9
    assert "SUBGEN_KWARGS" not in text
    assert "CUSTOM_REGROUP" not in text


def test_full_compose_cpu_has_no_gpu_block(subarr_env):
    from subarr.subgen_config_gen import generate_full_compose

    text = generate_full_compose(
        model="small", device="cpu", compute_type="int8",
        media_host_path="/mnt/media", port=9000, gpu=False,
    )
    assert "driver: nvidia" not in text
    assert "TRANSCRIBE_DEVICE: cpu" in text


def test_detection_passthrough_snippet(subarr_env):
    from subarr.subgen_config_gen import detection_passthrough_snippet

    text = detection_passthrough_snippet()
    assert "driver: nvidia" in text
    assert "utility" in text          # utility-only: queries the card,
    assert "compute" not in text      # reserves no compute/VRAM from subgen
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement `src/subarr/subgen_config_gen.py`**

```python
"""Mode A delivery (spec §2.5): generate the exact config text the user
applies. Pure string templates — no I/O, no docker writes (mode B was
explicitly rejected: subarr never edits the host's Docker config).

The generated artifacts deliberately set ONLY the hardware-dependent vars
(WHISPER_MODEL / TRANSCRIBE_DEVICE / COMPUTE_TYPE). The tuned kwargs +
regroup are baked in the r9 image and must not be duplicated here — a
duplicated value would pin the user to today's tuning and mask image
default upgrades.
"""

from __future__ import annotations

from textwrap import dedent


def generate_env_additions(*, model: str, device: str, compute_type: str) -> str:
    """The three lines to add under an existing subgen service's
    `environment:` mapping."""
    return dedent(f"""\
        # guided subgen setup — hardware-matched (generated by subarr)
        WHISPER_MODEL: {model}
        TRANSCRIBE_DEVICE: {device}
        COMPUTE_TYPE: {compute_type}
    """)


_GPU_BLOCK = """\
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
"""


def generate_full_compose(
    *, model: str, device: str, compute_type: str,
    media_host_path: str, port: int = 9000, gpu: bool,
) -> str:
    """Greenfield subarr-subgen compose for the no-subgen-yet user."""
    env = generate_env_additions(model=model, device=device, compute_type=compute_type)
    env_indented = "\n".join(f"      {line}" for line in env.strip().splitlines())
    gpu_block = _GPU_BLOCK if gpu else ""
    return dedent(f"""\
        services:
          subgen:
            image: ghcr.io/coaxk/subarr-subgen:latest
            container_name: subgen
            restart: unless-stopped
            ports:
              - "{port}:9000"
            environment:
              TZ: Etc/UTC
        {env_indented}
            volumes:
              - ./subgen/models:/subgen/models
              - {media_host_path}:/media
        {gpu_block}""").rstrip() + "\n"


def detection_passthrough_snippet() -> str:
    """The optional-but-recommended utility-only GPU passthrough for the
    SUBARR container (spec §2.1): lets subarr query the card (nvidia-smi)
    without reserving any VRAM/compute from subgen."""
    return dedent("""\
        # add under subarr's service to enable automatic GPU detection
        deploy:
          resources:
            reservations:
              devices:
                - driver: nvidia
                  count: 1
                  capabilities: [utility]
    """)
```

- [ ] **Step 4: Green + lint.** Note for the engineer: if the indentation assertions fight you, print the output and fix the template, not the test — the artifact text IS the product here.

- [ ] **Step 5: Commit**

```bash
git add src/subarr/subgen_config_gen.py tests/test_subgen_config_gen.py
git commit -m "feat: mode-A config generators - env additions, greenfield compose, detection passthrough snippet"
```

### Task 10: Setup router — detect cascade, plan, apply

**Files:**
- Create: `src/subarr/routers/subgen_setup.py`
- Modify: `src/subarr/app.py` (router import + `include_router`, matching how `r_probe`/`gpu` are registered)
- Test: `tests/test_subgen_setup_router.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Guided setup API: GET /api/subgen-setup/detect, POST .../plan, POST .../apply.

Uses the app TestClient pattern from existing router tests (see
tests/test_security_hardening.py for the construction idiom — reuse the
same fixture/factory this suite already has for building a test app)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(subarr_env, monkeypatch):
    # Build the app the way existing router tests do; then monkeypatch the
    # detection inputs so tests control every tier.
    from subarr.app import app

    return TestClient(app)


def test_detect_reports_gpu_when_smi_available(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    async def fake_smi():
        return "NVIDIA GeForce RTX 3060, 12288, 4343, 8.6, 610.52"

    monkeypatch.setattr(ss, "_run_local_smi", fake_smi)
    r = client.get("/api/subgen-setup/detect")
    assert r.status_code == 200
    d = r.json()
    assert d["tier"] == "smi"
    assert d["gpu"]["name"].endswith("3060")
    assert d["gpu"]["compute_cap"] == 8.6
    assert d["recommended_model"] == "large-v3"


def test_detect_falls_to_docker_info_then_manual(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    async def no_smi():
        return None

    monkeypatch.setattr(ss, "_run_local_smi", no_smi)
    monkeypatch.setattr(ss, "_nvidia_runtime", lambda app: True)
    r = client.get("/api/subgen-setup/detect")
    d = r.json()
    assert d["tier"] == "docker_info"
    assert d["gpu"] is None
    assert d["host_gpu_capable"] is True   # -> UI shows paste-parse, not "no GPU"


def test_plan_derives_compute_and_routes_mode(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    # capability-gated routing: r9 present -> mode C offered
    monkeypatch.setattr(ss, "_subgen_caps", lambda app: type("C", (), {
        "reachable": True, "is_subarr_subgen": True, "runtime_config": True})())
    r = client.post("/api/subgen-setup/plan", json={
        "model": "large-v3", "vram_total_mb": 12288, "compute_cap": 8.6})
    d = r.json()
    assert d["compute_type"] == "float16"
    assert "half-precision" in d["compute_reason"]
    assert d["mode"] == "live_apply"
    assert "WHISPER_MODEL: large-v3" in d["env_additions"]   # A always available


def test_plan_mode_a_against_vanilla(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    monkeypatch.setattr(ss, "_subgen_caps", lambda app: type("C", (), {
        "reachable": True, "is_subarr_subgen": False, "runtime_config": False})())
    r = client.post("/api/subgen-setup/plan", json={
        "model": "medium", "vram_total_mb": 8192, "compute_cap": 8.6})
    d = r.json()
    assert d["mode"] == "generate"
    assert d["upsell"] == "switch_to_subarr_subgen"


def test_plan_no_subgen_greenfield_compose(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    monkeypatch.setattr(ss, "_subgen_caps", lambda app: type("C", (), {
        "reachable": False, "is_subarr_subgen": False, "runtime_config": False})())
    r = client.post("/api/subgen-setup/plan", json={
        "model": "small", "vram_total_mb": None, "compute_cap": None})
    d = r.json()
    assert d["mode"] == "generate_full"
    assert "ghcr.io/coaxk/subarr-subgen" in d["compose"]
    assert d["compute_type"] == "int8"     # CPU row of the matrix


def test_apply_precheck_blocks_no_fit(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    monkeypatch.setattr(ss, "_subgen_caps", lambda app: type("C", (), {
        "reachable": True, "is_subarr_subgen": True, "runtime_config": True})())
    r = client.post("/api/subgen-setup/apply", json={
        "model": "large-v3", "compute_type": "float16", "vram_total_mb": 2048})
    d = r.json()
    assert r.status_code == 200 and d["ok"] is False
    assert d["reason"] == "precheck_no_fit"   # never even called subgen


def test_apply_surfaces_subgen_rollback(client, monkeypatch):
    import subarr.routers.subgen_setup as ss

    monkeypatch.setattr(ss, "_subgen_caps", lambda app: type("C", (), {
        "reachable": True, "is_subarr_subgen": True, "runtime_config": True})())

    async def fake_post_config(app, **kw):
        return 422, {"ok": False, "reason": "oom", "current_model": "medium",
                     "current_compute_type": "float16"}

    monkeypatch.setattr(ss, "_post_config", fake_post_config)
    r = client.post("/api/subgen-setup/apply", json={
        "model": "large-v3", "compute_type": "float16", "vram_total_mb": 12288})
    d = r.json()
    assert d["ok"] is False and d["reason"] == "oom"
    assert d["current_model"] == "medium"   # rollback contract surfaced
```

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement `src/subarr/routers/subgen_setup.py`**

```python
"""Guided subgen setup (spec §2): detect → plan → apply.

Module-level indirection functions (_run_local_smi, _nvidia_runtime,
_subgen_caps, _post_config) exist so tests monkeypatch the seams without
faking subprocesses or app state internals.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..subgen_hardware import (
    derive_compute_type,
    model_fit,
    parse_smi_csv,
    recommend_model,
)
from ..subgen_config_gen import (
    detection_passthrough_snippet,
    generate_env_additions,
    generate_full_compose,
)

router = APIRouter(prefix="/api/subgen-setup", tags=["subgen-setup"])
log = logging.getLogger(__name__)


async def _run_local_smi() -> str | None:
    """Tier 1: subarr's own nvidia-smi (utility passthrough). None when the
    binary is missing or errors — fail-soft to the next tier."""
    import shutil

    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, "--query-gpu=name,memory.total,memory.free,compute_cap,driver_version",
            "--format=csv,nounits,noheader",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return out.decode(errors="replace") if proc.returncode == 0 else None
    except Exception as e:  # noqa: BLE001 - detection must never error the wizard
        log.debug("local smi failed (non-fatal): %s", e)
        return None


def _nvidia_runtime(app) -> bool | None:
    """Tier 2: docker info Runtimes."""
    try:
        return app.state.docker.nvidia_runtime_available()
    except Exception:
        return None


def _subgen_caps(app):
    return getattr(app.state, "subgen_caps", None)


async def _post_config(app, **kw):
    return await app.state.subgen.post_config(**kw)


@router.get("/detect")
async def detect(request: Request) -> dict[str, Any]:
    """Run the §2.1 cascade; report the best tier that produced signal."""
    smi = await _run_local_smi()
    gpu = parse_smi_csv(smi) if smi else None
    # Tier 3 input regardless of GPU tier — feeds the current-vs-recommended diff.
    try:
        current = await request.app.state.docker.subgen_current_config()
    except Exception:
        current = None
    if gpu:
        return {
            "tier": "smi",
            "gpu": gpu.__dict__,
            "host_gpu_capable": True,
            "recommended_model": recommend_model(vram_total_mb=gpu.vram_total_mb),
            "subgen_current": current,
            "passthrough_snippet": None,
        }
    capable = _nvidia_runtime(request.app)
    return {
        "tier": "docker_info" if capable else "manual",
        "gpu": None,
        "host_gpu_capable": capable,
        "recommended_model": None,
        "subgen_current": current,
        # the manual-fallback path teaches the upgrade (spec §2.2 footer)
        "passthrough_snippet": detection_passthrough_snippet(),
        "smi_command": "nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader",
    }


class ParseBody(BaseModel):
    pasted: str


@router.post("/parse-smi")
def parse_pasted(body: ParseBody) -> dict[str, Any]:
    """§2.2 paste box: same parser as the auto path."""
    gpu = parse_smi_csv(body.pasted)
    if gpu is None:
        return {"ok": False, "error": "could not parse that output — paste the unmodified command result"}
    return {"ok": True, "gpu": gpu.__dict__,
            "recommended_model": recommend_model(vram_total_mb=gpu.vram_total_mb)}


class PlanBody(BaseModel):
    model: str
    vram_total_mb: int | None = None
    compute_cap: float | None = None
    media_host_path: str = "/path/to/media"


@router.post("/plan")
def plan(body: PlanBody, request: Request) -> dict[str, Any]:
    """Derive compute, compute fit, and route the delivery mode (§2.5)."""
    derivation = derive_compute_type(
        compute_cap=body.compute_cap, vram_total_mb=body.vram_total_mb, model=body.model)
    fit = model_fit(body.model, derivation.compute_type, vram_total_mb=body.vram_total_mb)
    gpu = body.compute_cap is not None
    device = "cuda" if gpu else "cpu"
    env_additions = generate_env_additions(
        model=body.model, device=device, compute_type=derivation.compute_type)

    caps = _subgen_caps(request.app)
    if caps is None or not getattr(caps, "reachable", False):
        mode, upsell = "generate_full", None
    elif getattr(caps, "runtime_config", False):
        mode, upsell = "live_apply", None
    elif getattr(caps, "is_subarr_subgen", False):
        mode, upsell = "generate", "upgrade_to_r9"
    else:
        mode, upsell = "generate", "switch_to_subarr_subgen"

    out: dict[str, Any] = {
        "model": body.model,
        "device": device,
        "compute_type": derivation.compute_type,
        "compute_reason": derivation.reason,
        "fit": fit,
        "mode": mode,
        "upsell": upsell,
        "env_additions": env_additions,
    }
    if mode == "generate_full":
        out["compose"] = generate_full_compose(
            model=body.model, device=device, compute_type=derivation.compute_type,
            media_host_path=body.media_host_path, gpu=gpu)
    return out


class ApplyBody(BaseModel):
    model: str
    compute_type: str
    vram_total_mb: int | None = None


@router.post("/apply")
async def apply(body: ApplyBody, request: Request) -> dict[str, Any]:
    """Mode C: pre-check the fit, then POST /config; surface the outcome.
    Defense in depth — the §2.4 math stops known-impossible loads before the
    call, subgen's rollback contract (§1.4) backstops the rest."""
    caps = _subgen_caps(request.app)
    if not (caps and getattr(caps, "runtime_config", False)):
        return {"ok": False, "reason": "not_supported",
                "detail": "connected subgen has no runtime_config capability (needs subarr-subgen r9+)"}
    if model_fit(body.model, body.compute_type, vram_total_mb=body.vram_total_mb) == "no_fit":
        return {"ok": False, "reason": "precheck_no_fit",
                "detail": f"{body.model} ({body.compute_type}) cannot fit in "
                          f"{body.vram_total_mb} MB — pick a smaller model or int8 variant"}
    status, resp = await _post_config(request.app, model=body.model, compute_type=body.compute_type)
    if status == 200 and resp.get("ok"):
        return {"ok": True, "model": resp.get("model"), "compute_type": resp.get("compute_type")}
    return {
        "ok": False,
        "reason": resp.get("reason", f"http_{status}"),
        "detail": resp.get("detail"),
        "current_model": resp.get("current_model"),
        "current_compute_type": resp.get("current_compute_type"),
    }
```

Register in `app.py`: add `subgen_setup as r_subgen_setup` to the `from .routers import (...)` block and `app.include_router(r_subgen_setup.router)` next to the existing `include_router` lines (search `include_router(gpu` for the spot).

- [ ] **Step 4: Green + lint.** Run the new file plus a full-suite sweep: `PYTHONPATH=src python -m pytest tests/ -q --ignore=tests/e2e -k "subgen_setup"` then the whole suite in background before the PR.

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/subgen_setup.py src/subarr/app.py tests/test_subgen_setup_router.py
git commit -m "feat: guided-setup API - detect cascade, plan with mode routing, capability-gated live apply"
```

### Task 11: Shared frontend component + both mounts

**Files:**
- Create: `src/subarr/static/v1/home-hifi/subgen-setup.jsx` (the shared detect→guide→apply component)
- Modify: `src/subarr/static/v1/home-hifi/onboarding.jsx` (add step to `STEPS`, ~line 17, render the component for it)
- Modify: `src/subarr/static/v1/home-hifi/settings.jsx` (nav entry + panel: extend the `id: 'updates'` nav-array pattern ~line 897, the hash allowlist ~line 2073, the crumbs/title ternaries ~lines 2095–2116, and the `{view === 'updates' && <UpdatesPanel />}` render block ~line 2176 with a `subgen-tuning` view)
- Build: `npm run build:frontend`

UI contract (all data comes from Task 10's API; no business logic in JSX):

- [ ] **Step 1: Create `subgen-setup.jsx`**

```jsx
// Guided subgen setup (spec §2.6) — ONE component, two mounts (onboarding
// step + Settings panel). All decisions come from /api/subgen-setup/*;
// this file only renders states and relays choices.

import { SectionCard, Row, Hint } from './atoms.jsx';   // match the actual atom exports — check atoms.jsx and reuse what settings.jsx imports

const { useState, useEffect, useCallback } = React;

const MODELS = ['tiny', 'base', 'small', 'medium', 'large-v3'];
const FIT_LABEL = { fits: 'fits comfortably', tight: 'tight — int8 variant advised', no_fit: 'will not fit' };

export function SubgenSetupFlow({ onComplete }) {
  const [detect, setDetect] = useState(null);        // GET /detect result
  const [gpu, setGpu] = useState(null);              // GpuInfo (auto or pasted)
  const [pasted, setPasted] = useState('');
  const [pasteError, setPasteError] = useState(null);
  const [model, setModel] = useState(null);
  const [plan, setPlan] = useState(null);            // POST /plan result
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState(null);

  useEffect(() => {
    fetch('/api/subgen-setup/detect').then(r => r.json()).then((d) => {
      setDetect(d);
      if (d.gpu) { setGpu(d.gpu); setModel(d.recommended_model); }
    }).catch(() => setDetect({ tier: 'manual', gpu: null }));
  }, []);

  const submitPaste = useCallback(() => {
    fetch('/api/subgen-setup/parse-smi', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pasted }),
    }).then(r => r.json()).then((d) => {
      if (!d.ok) { setPasteError(d.error); return; }
      setPasteError(null); setGpu(d.gpu); setModel(d.recommended_model);
    }).catch(() => setPasteError('request failed'));
  }, [pasted]);

  // Re-plan whenever the model choice changes.
  useEffect(() => {
    if (!model) return;
    fetch('/api/subgen-setup/plan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        vram_total_mb: gpu ? gpu.vram_total_mb : null,
        compute_cap: gpu ? gpu.compute_cap : null,
      }),
    }).then(r => r.json()).then(setPlan).catch(() => {});
  }, [model, gpu]);

  const applyNow = useCallback(() => {
    setApplying(true);
    fetch('/api/subgen-setup/apply', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model, compute_type: plan.compute_type,
        vram_total_mb: gpu ? gpu.vram_total_mb : null,
      }),
    }).then(r => r.json()).then((d) => { setApplyResult(d); if (d.ok && onComplete) onComplete(); })
      .catch(() => setApplyResult({ ok: false, reason: 'request_failed' }))
      .finally(() => setApplying(false));
  }, [model, plan, gpu, onComplete]);

  if (!detect) return <div style={{ color: 'var(--fg-2)' }}>Detecting hardware…</div>;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* 1 — detected hardware / manual paste */}
      <SectionCard label="Your hardware">
        {gpu ? (
          <div>
            <div style={{ color: 'var(--fg-1)' }}>
              {gpu.name} · {Math.round(gpu.vram_total_mb / 1024)} GB VRAM · compute {gpu.compute_cap}
            </div>
            {gpu.vram_free_mb != null && (
              <Hint>
                Live snapshot: {Math.round((gpu.vram_total_mb - gpu.vram_free_mb) / 1024)} GB of{' '}
                {Math.round(gpu.vram_total_mb / 1024)} GB in use right now. This is a single moment in
                time, not your norm. Choose your model based on how much VRAM is usually free when
                subgen runs — if this card also handles Plex transcoding, Ollama, or anything else,
                leave headroom for those at their busiest, not for how quiet (or busy) it happens to
                be this second.
              </Hint>
            )}
          </div>
        ) : (
          <div>
            <div style={{ color: 'var(--fg-2)', marginBottom: 8 }}>
              {detect.host_gpu_capable
                ? 'Your Docker host can do GPU passthrough, but subarr can’t read the card directly — it doesn’t have detection access. Run this and paste the result:'
                : 'No GPU detected. If you do have an Nvidia GPU, run this and paste the result — otherwise continue on CPU.'}
            </div>
            <pre className="mono" style={{ fontSize: 'var(--text-xs)', background: 'var(--bg-2)', padding: 8 }}>
              {detect.smi_command || 'nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader'}
            </pre>
            <textarea value={pasted} onChange={(e) => setPasted(e.target.value)} rows={2}
              placeholder="paste the command output here"
              style={{ width: '100%', background: 'var(--bg-2)', color: 'var(--fg-1)' }} />
            <button className="btn sm" onClick={submitPaste} disabled={!pasted.trim()}>Parse</button>
            {pasteError && <div style={{ color: 'var(--error-500)' }}>{pasteError}</div>}
            <Hint>
              No nvidia-smi on your PATH? Run it inside a GPU container instead:
              docker exec &lt;your-subgen-container&gt; nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader
            </Hint>
            {detect.passthrough_snippet && (
              <Hint>Want this automatic next time? Give subarr detection access (queries only, reserves nothing):
                <pre className="mono" style={{ fontSize: 'var(--text-2xs)' }}>{detect.passthrough_snippet}</pre>
              </Hint>
            )}
            <button className="btn ghost sm" onClick={() => setModel('small')}>Continue without GPU (CPU)</button>
          </div>
        )}
      </SectionCard>

      {/* 2 — model choice (guided) */}
      {(gpu || model) && (
        <SectionCard label="Whisper model">
          {MODELS.map((m) => (
            <Row key={m} label={m}
              hint={plan && model === m ? FIT_LABEL[plan.fit] : ''}
              control={
                <button className={`btn sm ${model === m ? '' : 'ghost'}`} onClick={() => setModel(m)}>
                  {model === m ? 'selected' : 'select'}
                </button>
              } />
          ))}
        </SectionCard>
      )}

      {/* 3 — derived compute + review + apply */}
      {plan && (
        <SectionCard label="Recommended configuration">
          <Row label="Transcribe device" control={<span className="mono">{plan.device}</span>} />
          <Row label="Compute type" hint={plan.compute_reason}
            control={<span className="mono">{plan.compute_type}</span>} />
          {detect.subgen_current && detect.subgen_current.whisper_model && (
            <Hint>
              Currently: {detect.subgen_current.whisper_model} / {detect.subgen_current.transcribe_device || '?'} /{' '}
              {detect.subgen_current.compute_type || '?'} → recommended: {model} / {plan.device} / {plan.compute_type}.
              Tuned kwargs + regroup come baked in the subarr-subgen image.
            </Hint>
          )}

          {plan.mode === 'live_apply' && (
            <div>
              <button className="btn" onClick={applyNow} disabled={applying}>
                {applying ? 'Applying…' : 'Apply now'}
              </button>
              {applyResult && !applyResult.ok && (
                <div style={{ color: 'var(--warn-500)' }}>
                  {applyResult.reason === 'precheck_no_fit' || applyResult.reason === 'oom'
                    ? `${model} didn’t fit on your GPU — kept ${applyResult.current_model || 'your current model'}. Try an int8 variant or free up VRAM and retry.`
                    : `Apply failed: ${applyResult.reason}`}
                </div>
              )}
              {applyResult && applyResult.ok && (
                <div style={{ color: 'var(--ok-500, #34d399)' }}>Applied — subgen is now running {applyResult.model}.</div>
              )}
            </div>
          )}

          {plan.mode !== 'live_apply' && (
            <div>
              <Hint>
                {plan.mode === 'generate_full'
                  ? 'No subgen detected — here’s a ready-to-run compose for subarr-subgen with your hardware-matched settings:'
                  : plan.upsell === 'switch_to_subarr_subgen'
                    ? 'Add these to your subgen service’s environment, then docker compose up -d. (Switching to the subarr-subgen image also gets you the tuned defaults + hands-off apply.)'
                    : 'Add these to your subgen service’s environment, then docker compose up -d. (Upgrading to subarr-subgen r9+ enables hands-off apply.)'}
              </Hint>
              <pre className="mono" style={{ fontSize: 'var(--text-xs)', background: 'var(--bg-2)', padding: 10, overflow: 'auto' }}>
                {plan.mode === 'generate_full' ? plan.compose : plan.env_additions}
              </pre>
            </div>
          )}
        </SectionCard>
      )}
    </div>
  );
}
```

Adaptation note: confirm the actual exports of `atoms.jsx` / the local `SectionCard`/`Row`/`Hint` (settings.jsx defines or imports these — reuse exactly what `UpdatesPanel` uses, including style conventions).

- [ ] **Step 2: Mount in onboarding**

In `onboarding.jsx`: add to `STEPS` after the `subgen` integration step:

```jsx
  { id: 'subgen-setup', label: 'subgen tuning', group: 'integrations' },
```

and in the step-rendering switch/conditional (find where `svc === 'subgen'` renders its form), render `<SubgenSetupFlow onComplete={advance}/>` for the new step id, importing `{ SubgenSetupFlow } from '../subgen-setup.jsx'` (relative path per the entry's existing imports — onboarding's entry lives in `entries/onboarding.entry.jsx`; match how onboarding.jsx imports shared modules today). Keep a "Skip for now" button consistent with the wizard's other optional steps.

- [ ] **Step 3: Mount in Settings**

In `settings.jsx`, following the `updates` pattern end-to-end: nav item `{ id: 'subgen-tuning', label: 'Subgen tuning', ... }` in the SUBARR group (~line 897 array), `'subgen-tuning'` in the hash allowlist (~2073), crumbs `['Settings', 'Subgen tuning']`, title `'Subgen tuning'`, subtitle `'Hardware-matched Whisper model, device and compute type.'`, and `{view === 'subgen-tuning' && <SubgenSetupFlow />}` in the render block. Import the component at the top of settings.jsx.

- [ ] **Step 4: Build + map hygiene**

```bash
npm run build:frontend
```

Then restore map-only churn: `git status --short -- "*.map"` and `git restore` any `.map` whose `.bundle.js` is unchanged.

- [ ] **Step 5: Live-verify on :9923**

Restart subarr-next, then with Playwright or browser: `/settings#subgen-tuning` renders the flow; `/onboarding` shows the new step. With the dev box's real GPU + utility passthrough absent, expect the manual-paste path — paste a real `nvidia-smi` line and confirm model recommendation + derived compute render. If subgen-next runs the r9 dev image (Task 5 soak), confirm "Apply now" appears and a `small`→`medium` round-trip works.

- [ ] **Step 6: Commit**

```bash
git add src/subarr/static/v1/home-hifi/subgen-setup.jsx src/subarr/static/v1/home-hifi/onboarding.jsx src/subarr/static/v1/home-hifi/settings.jsx src/subarr/static/v1/home-hifi/*.bundle.js src/subarr/static/v1/home-hifi/*.map
git commit -m "feat: guided subgen setup UI - shared flow component, onboarding step + Settings panel"
```

### Task 12: Full verification + PR

- [ ] **Step 1: Full suite + lint**

```bash
PYTHONPATH=src python -m pytest tests/ -q --ignore=tests/e2e
ruff check src/subarr tests
```

Expected: all green (905+ tests).

- [ ] **Step 2: PR**

```bash
git push -u origin feat/guided-subgen-setup
gh pr create --title "Guided subgen setup: hardware detection, model guidance, A+C delivery" --body "Implements Track 2 of docs/superpowers/specs/2026-06-13-guided-subgen-setup-design.md. Detection cascade (smi -> docker info -> container inspect -> paste-parse), VRAM-aware model guidance, auto-derived compute_type with explanation, mode A generators, capability-gated mode C live-apply against subarr-subgen r9. Degrades to A-only against pre-r9 images."
```

Merge on green (squash, admin). Update board #6, comment on subarr#224 (image side shipping in r9), and note the launch-readiness state.

---

## Self-review notes (run after drafting — issues found and fixed inline)

- **Spec coverage check:** §1.1–1.5 → Tasks 1–5; §2.1 → Tasks 7+10; §2.2 → Tasks 6 (parser) + 10 (endpoint) + 11 (UI); §2.3/2.4 → Task 6 (+advisory copy in Task 11); §2.5 → Tasks 8–10; §2.6 → Task 11. Banked GPU-historian: explicitly not planned (spec says out of scope).
- **Type consistency:** `GpuInfo.__dict__` keys (`name/vram_total_mb/vram_free_mb/compute_cap/driver_version`) match what Task 11's JSX reads (`gpu.vram_total_mb`, `gpu.compute_cap`, `gpu.vram_free_mb`). `post_config` returns `(status, body)` tuple — Task 10's `_post_config` seam and tests match. `plan.mode` strings (`live_apply/generate/generate_full`) consistent across router + JSX.
- **Known adaptation points (deliberate, flagged in-task):** queue-idle check in patch 0022 must mirror patch 0007's actual structures; gpu.py CSV indexes must follow the real column count; atoms.jsx export names; onboarding step-render mechanism. Each task names the exact search anchor for the engineer.
