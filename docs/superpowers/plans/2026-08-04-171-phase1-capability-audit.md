# #171 Phase 1 — Capability Survival Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine, without any GPU time or patch porting, whether every capability subarr depends on has a place to live on upstream's `refactor/drop-stable-ts` branch — and produce a veto verdict that decides whether Phase 2 runs at all.

**Architecture:** A standalone audit script in `subarr-subgen` that (1) derives the capability→patch map from the patch files themselves rather than a hand-typed list, (2) locates each capability's *seam* — the upstream function or dispatch point the patch attaches to — and (3) checks whether that seam exists on the branch. Mechanical output is a first cut; anything classed GONE gets a documented manual second pass before it counts. Output is a committed markdown report plus an explicit verdict line.

**Tech Stack:** Python 3.11 stdlib only (no new deps), `git` for branch access, `pytest` for the pure-logic tests. ⚠️ This repo has **no existing pytest suite and no pytest in CI** — Task 0 adds the minimal local harness; wiring CI is out of scope. Runs against the `refactor/drop-stable-ts` branch in the `upstream/` submodule.

---

## Context the engineer needs

You are working in `C:\Projects\subarr-subgen` (WSL path `/mnt/c/Projects/subarr-subgen`). This repo is a **patch stack**: `patches/*.patch` are applied in the order given by `patches/series` onto a pinned checkout of `McCloudS/subgen` in the `upstream/` git submodule. `scripts/apply-patches.sh` does the applying; `scripts/validate-patched.py` asserts structural invariants afterwards.

**Why this audit exists:** upstream has a branch, `refactor/drop-stable-ts`, that deletes `stable-ts` and replaces it with a direct faster-whisper pipeline plus its own segmenter. 30 of our 34 patches conflict against it. But *a patch conflicting is uninteresting* — context drift is normal and re-porting is routine sync work. What matters is whether the **seam** each patch attaches to still exists. A capability with no seam cannot be re-ported at any price, and that is a different decision.

**The contract that defines "capability":** our patched subgen advertises flags on `GET /queue` under `capabilities`. subarr negotiates against those flags at runtime. They are the real interface. There are currently 16.

**Read the design spec first:** `C:\Projects\subarr\docs\superpowers\specs\2026-08-04-171-drop-stable-ts-evidence-design.md`. In particular the veto condition and the note that `CUSTOM_REGROUP` is known-GONE but deliberately does not trigger the veto.

**Ground truth for the branch:** SHA `7997624` at time of writing, 44 commits ahead of `origin/main`. Fetch with `git -C upstream fetch origin refactor/drop-stable-ts`, then reference it as `FETCH_HEAD`.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/capability_audit.py` (create) | All audit logic. Pure functions for parsing and classification; a thin `main()` that does git/file I/O and prints the report. Kept in one file because it is a single-purpose tool of ~200 lines and splitting it would spread one idea across four files. |
| `tests/test_capability_audit.py` (create) | Tests for the pure functions only. No git, no network. |
| `docs/drop-stable-ts-capability-audit-2026-08-04.md` (create, generated) | The committed report: capability → verdict → evidence, plus the verdict line. |

Nothing existing is modified. This audit is read-only with respect to the patch stack.

---

## Task 0: Make `scripts/` importable from tests

**Why this task exists.** `subarr-subgen` has **no pytest suite** — `tests/` holds a
single standalone script (`smoke_patched_behaviour.py`, run directly with
`python`), there is no `conftest.py`, no `pyproject.toml`, no `scripts/__init__.py`,
and **no pytest step in CI** (the checks are apply+validate, zizmor, docker build,
trivy, bandit, semgrep, codeql).

Without this task, `from scripts.capability_audit import ...` raises
`ModuleNotFoundError` **both before and after** you write the implementation. Every
"Expected: FAIL with ModuleNotFoundError" step below would then be satisfied by the
wrong cause, and you would have no signal that your code works. Fix the harness
first so the red/green transitions mean something.

Note `scripts/validate-patched.py` and `scripts/fix-action-pins.py` carry hyphens
and are therefore not importable at all; `capability_audit.py` is deliberately
named with an underscore so it can be imported.

**Scope note:** this adds a local test harness only. Wiring pytest into CI is a
separate concern and explicitly **not** part of this plan — these tests are a local
gate, run by hand.

**Files:**
- Create: `conftest.py`

- [ ] **Step 1: Create the conftest**

```python
# conftest.py
"""Put the repo root on sys.path so tests can `from scripts.x import y`.

This repo has no packaging metadata and its only other test is a standalone
script, so there is nothing else establishing import paths.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
```

- [ ] **Step 2: Verify pytest collects and the path works**

```bash
cd /mnt/c/Projects/subarr-subgen
python -m pytest --collect-only -q 2>&1 | tail -3
python -c "import sys; sys.path.insert(0,'.'); import scripts; print('scripts is importable')"
```

Expected: pytest runs without an import error (it may collect 0 tests — that is fine,
none exist yet). The second command prints `scripts is importable`.

If `import scripts` fails with `ModuleNotFoundError: No module named 'scripts'`,
Python needs the directory to be a package on your version — create an empty
`scripts/__init__.py` and re-run:

```bash
cd /mnt/c/Projects/subarr-subgen && touch scripts/__init__.py
```

- [ ] **Step 3: Commit**

```bash
cd /mnt/c/Projects/subarr-subgen
git add conftest.py
git commit -m "test(#171): make scripts/ importable so TDD red/green is meaningful"
```

---

## Task 1: Derive the capability → patch map

**Why derive rather than hard-code:** a hand-typed list silently goes stale the moment a patch is added, and this audit will be re-run when the branch moves. Deriving it from the patch files means the map cannot drift from reality.

**The parsing rule that matters:** a patch *provides* a capability only if the flag appears on an **added** line (`+` prefix). Patch diffs include surrounding context lines that show neighbouring flags — a naive grep attributes those to the wrong patch. This was verified during planning: the naive version credited `0010-queue-cancel` with `audio_language_override`, which it merely sits below.

**Files:**
- Create: `scripts/capability_audit.py`
- Test: `tests/test_capability_audit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_capability_audit.py
from scripts.capability_audit import capabilities_added_by_patch

PATCH_WITH_CONTEXT = '''--- a/subgen.py
+++ b/subgen.py
@@ -580,6 +580,7 @@ def queue_status():
             "capabilities": {
                 "audio_language_override": True,
+                "queue_cancel": True,
             },
'''


def test_only_added_lines_count():
    # audio_language_override is CONTEXT here, not provided by this patch.
    assert capabilities_added_by_patch(PATCH_WITH_CONTEXT) == {"queue_cancel"}


def test_patch_with_no_capabilities_returns_empty():
    assert capabilities_added_by_patch("--- a/x\n+++ b/x\n+print('hi')\n") == set()


def test_non_boolean_capability_values_still_count():
    # concurrent_transcriptions is an int, not True.
    body = '--- a/s\n+++ b/s\n+            "concurrent_transcriptions": concurrent_transcriptions,\n'
    assert capabilities_added_by_patch(body) == {"concurrent_transcriptions"}


def test_removal_lines_are_ignored():
    body = '--- a/s\n+++ b/s\n-                "old_flag": True,\n'
    assert capabilities_added_by_patch(body) == set()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /mnt/c/Projects/subarr-subgen && python -m pytest tests/test_capability_audit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.capability_audit'`

- [ ] **Step 3: Write the minimal implementation**

```python
# scripts/capability_audit.py
"""#171 Phase 1: does every capability we depend on have a seam on
refactor/drop-stable-ts?

A patch conflicting is uninteresting -- context drift is routine sync work. A
capability whose SEAM is gone cannot be re-ported at any price, and that is the
veto. See docs/superpowers/specs/2026-08-04-171-drop-stable-ts-evidence-design.md
in the subarr repo.
"""

from __future__ import annotations

import re

# Matches a capability entry in the /queue capabilities dict. The value may be
# True, an int, or an expression -- concurrent_transcriptions is an int -- so the
# value side is deliberately not constrained.
_CAP_ENTRY = re.compile(r'"(?P<name>[a-z][a-z0-9_]*)"\s*:')


def capabilities_added_by_patch(patch_text: str) -> set[str]:
    """Capability names this patch ADDS.

    Only `+` lines count. Patch context lines show neighbouring flags, and
    attributing those to the patch credits the wrong one -- verified during
    planning, where a naive scan credited 0010-queue-cancel with
    audio_language_override, which it merely sits below.

    This matches any quoted snake_case key followed by a colon on an added
    line, not just entries inside the capabilities dict -- it over-matches by
    design, so callers must filter the result against the known capability
    set rather than trusting it directly.
    """
    found: set[str] = set()
    for line in patch_text.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for m in _CAP_ENTRY.finditer(line):
            found.add(m.group("name"))
    return found
```

⚠️ **`finditer`, not `search`** — and the two extra tests below exist to keep it that
way. An earlier draft of this plan used `_CAP_ENTRY.search(line)`, which returns only
the **first** quoted key on a line. Code review caught it against a real patch:
`patches/0029-async-config-switch.patch` already carries added lines with two keys
(`"state": "switching", "model": model_name or whisper_model,`), and `search` silently
returned only `state`.

That failure mode is the dangerous one for this tool. A missed capability does not
raise — in Task 2 it becomes a capability attributed to **no patch**, which reads as
*"upstream never had this"* when the truth is *"our parser missed it"*, and the only
output of this whole audit is a veto verdict a human acts on.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /mnt/c/Projects/subarr-subgen && python -m pytest tests/test_capability_audit.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Projects/subarr-subgen
git add scripts/capability_audit.py tests/test_capability_audit.py
git commit -m "feat(#171): parse capabilities added by each patch"
```

---

## Task 2: Filter the map to real advertised capabilities

**The problem this solves:** the regex in Task 1 matches any quoted dict key followed by a colon on an added line, so it also catches response fields that are not capabilities — `queued_count`, `processing_count`, `n_total`, `ok`, `config_switch`. Those are real output, just not part of the negotiated contract. The audit must only consider the 16 flags that actually appear under `capabilities` on a live `/queue`.

**Files:**
- Modify: `scripts/capability_audit.py`
- Modify: `tests/test_capability_audit.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_capability_audit.py`:

```python
from scripts.capability_audit import ADVERTISED_CAPABILITIES, build_capability_map


def test_advertised_list_is_the_sixteen_live_flags():
    assert len(ADVERTISED_CAPABILITIES) == 16
    assert "per_request_kwargs" in ADVERTISED_CAPABILITIES
    assert "asr_arena" in ADVERTISED_CAPABILITIES
    assert "runtime_config" in ADVERTISED_CAPABILITIES
    # Response fields must NOT be treated as capabilities.
    assert "queued_count" not in ADVERTISED_CAPABILITIES
    assert "ok" not in ADVERTISED_CAPABILITIES


def test_map_drops_non_capability_keys():
    patches = {
        "0007-queue.patch": '+                "queued_count": len(q),\n',
        "0010-queue-cancel.patch": '+                "queue_cancel": True,\n',
    }
    m = build_capability_map(patches)
    assert m == {"queue_cancel": ["0010-queue-cancel.patch"]}


def test_map_records_every_provider_for_a_capability():
    patches = {
        "a.patch": '+                "runtime_config": True,\n',
        "b.patch": '+                "runtime_config": True,\n',
    }
    assert build_capability_map(patches)["runtime_config"] == ["a.patch", "b.patch"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /mnt/c/Projects/subarr-subgen && python -m pytest tests/test_capability_audit.py -v`
Expected: FAIL with `ImportError: cannot import name 'ADVERTISED_CAPABILITIES'`

- [ ] **Step 3: Write the minimal implementation**

Append to `scripts/capability_audit.py`:

```python
# The 16 flags a live patched subgen advertises under /queue -> capabilities.
# Captured 2026-08-04 from the running 2026.07.3-r1 image. This is the contract
# subarr negotiates against; anything else on an added line is a response field,
# not a capability.
ADVERTISED_CAPABILITIES = frozenset(
    {
        "asr_arena",
        "asr_detected_language",
        "asr_vanilla_base",
        "async_config",
        "audio_language_override",
        "concurrent_transcriptions",
        "curated_language_prompts",
        "detect_language_track",
        "ignore_forced_subtitles",
        "per_request_kwargs",
        "per_request_task",
        "queue_cancel",
        "request_ignore_forced",
        "robust_language_detection",
        "runtime_config",
        "safe_decode_preset",
    }
)


def build_capability_map(patches: dict[str, str]) -> dict[str, list[str]]:
    """capability -> sorted list of patch filenames that add it.

    ``patches`` maps patch filename to its text. Capabilities not in
    ADVERTISED_CAPABILITIES are dropped: they are response fields, not contract.
    """
    out: dict[str, list[str]] = {}
    for name in sorted(patches):
        for cap in capabilities_added_by_patch(patches[name]):
            if cap in ADVERTISED_CAPABILITIES:
                out.setdefault(cap, []).append(name)
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /mnt/c/Projects/subarr-subgen && python -m pytest tests/test_capability_audit.py -v`
Expected: PASS, 9 passed

- [ ] **Step 4b: Reality-check the map against the REAL patches**

The tests above use synthetic patch text. That proves the logic, not that it works on
our actual patch stack. Run this (it commits nothing):

```bash
cd /mnt/c/Projects/subarr-subgen && python -c "
from pathlib import Path
from scripts.capability_audit import ADVERTISED_CAPABILITIES, build_capability_map
patches = {p.name: p.read_text(encoding='utf-8', errors='replace') for p in sorted(Path('patches').glob('*.patch'))}
m = build_capability_map(patches)
missing = sorted(ADVERTISED_CAPABILITIES - set(m))
print(f'{len(patches)} patches, {len(m)}/16 capabilities attributed')
for cap in sorted(m):
    print(f'  {cap}: {m[cap]}')
print('UNATTRIBUTED:', missing or 'none')
"
```

**Expected: 16/16 attributed, `UNATTRIBUTED: none`.**

⚠️ **Do not wave through a non-empty `UNATTRIBUTED` list.** An unattributed capability
does not raise — it silently reads downstream as *"upstream never had this"* when the
truth is *"our parser missed it"*, and this tool's only output is a veto verdict a
human will act on. If any capability is unattributed, stop and report it: either the
flag is genuinely absent from every patch (a real and interesting finding worth
recording), or the parser has a hole (a bug to fix before Task 3). Say which, with the
grep that proves it.

- [ ] **Step 4c: Make Step 4b permanent — guard the allowlist against drift**

Step 4b is a one-time manual check. `ADVERTISED_CAPABILITIES` is a hand-captured
snapshot, and nothing re-derives it. If a future patch renames or drops one of the 16
literals, `capabilities_added_by_patch` stops finding it, `build_capability_map`
silently omits it, and Task 5's `.get(cap, [])` returns `[]` — **indistinguishable from
"no patch ever provided this"**. Wire the check up as a test so it cannot rot:

```python
PATCHES_DIR = Path(__file__).resolve().parent.parent / "patches"


def test_every_advertised_capability_is_provided_by_a_real_patch():
    patch_files = sorted(PATCHES_DIR.glob("*.patch"))
    assert patch_files, f"no patch files found under {PATCHES_DIR}"

    provided: set[str] = set()
    for path in patch_files:
        provided |= capabilities_added_by_patch(
            path.read_text(encoding="utf-8", errors="replace")
        )

    missing = ADVERTISED_CAPABILITIES - provided
    assert not missing, (
        f"advertised capabilities with no patch provider: {sorted(missing)}"
    )
```

Two details that are the whole point of the test, not incidental:

- **Resolve `patches/` from `__file__`, never the cwd.** A cwd-relative glob run from
  elsewhere silently matches nothing, the union is empty, and the subset check passes
  vacuously. The `assert patch_files` line exists so that failure is loud.
- **Name the missing capabilities in the message.** Whoever trips this in six months
  needs to know *which* flag vanished.

⚠️ **Watch this test fail before trusting it.** Temporarily add
`"not_a_real_capability"` to the frozenset, re-run, and confirm you get
`advertised capabilities with no patch provider: ['not_a_real_capability']`. Then
remove it. A guard nobody has seen fail is not a guard.

Also record, next to the frozenset, what this guard does **not** cover: a brand-new
capability a patch adds that never gets added to the allowlist. That direction still
needs a human to recapture the list from a live image.

And note the **17th** audit row there too — `CUSTOM_REGROUP` advertises no flag at all
(patches `0001`/`0017` set it via `args['regroup'] = ...`, a dict assignment the regex
never matches by design), so it is handled by hand in Task 6 Step 4. Without that
comment, Task 5's author reads `ADVERTISED_CAPABILITIES` as the entire surface.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Projects/subarr-subgen
git add scripts/capability_audit.py tests/test_capability_audit.py
git commit -m "feat(#171): restrict the map to the 16 advertised capabilities"
```

---

## Task 3: Extract each patch's seams

**What a "seam" is:** the upstream symbol a patch attaches to. Concretely, the function name in a hunk header (`@@ -580,6 +580,7 @@ def queue_status():` → `queue_status`) and any upstream function the patch's added lines call. If none of a capability's seams exist on the branch, the capability has nowhere to live.

**Why hunk headers:** `git diff` puts the enclosing function in the hunk header, which is exactly "where this patch attaches" without needing to parse Python.

**Pre-verified 2026-08-06 — this approach actually works on our patches.** The whole
task rests on an assumption worth checking before building on it: that our patch files
carry function context in their hunk headers at all. If they did not, `seams_for_patch`
would return empty for every patch, every capability would fall to `GONE_CANDIDATE`,
and the audit would look catastrophic while measuring nothing.

Measured across `patches/*.patch`: **101 of 129 hunk headers carry a `def`/`class`
context**, and the symbols are genuine subgen ones — `queue_status` (18), `asr` (10,
as `async def asr(`), `transcribe_existing` (8), `batch` (7), `asr_task_worker` (7),
`gen_subtitles_queue` (5), `NewFileHandler` (5), `DeduplicatedQueue` (5),
`runtime_config` (4).

The remaining 28 anchor at module level (e.g. `subgen_version = '2026.06.4'`) and
correctly contribute no seam. That is by design, not a miss — but note the
consequence: a capability provided *only* by module-level hunks gets an empty seam set
and therefore `GONE_CANDIDATE` in Task 4, which is the intended escalate-to-a-human
path rather than a silent pass.

**Files:**
- Modify: `scripts/capability_audit.py`
- Modify: `tests/test_capability_audit.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_capability_audit.py`:

```python
from scripts.capability_audit import seams_for_patch

HUNKS = '''--- a/subgen.py
+++ b/subgen.py
@@ -580,6 +580,7 @@ def queue_status():
+    x = 1
@@ -700,3 +701,4 @@ async def asr(
+    y = 2
@@ -900,1 +902,1 @@ class NewFileHandler(FileSystemEventHandler):
+    z = 3
'''


def test_extracts_function_names_from_hunk_headers():
    assert seams_for_patch(HUNKS) == {"queue_status", "asr", "NewFileHandler"}


def test_hunk_header_without_context_is_skipped():
    # A hunk at file top has no enclosing symbol; it is not a seam.
    assert seams_for_patch("@@ -1,4 +1,8 @@\n+x = 1\n") == set()


def test_decorated_and_annotated_defs_are_handled():
    body = "@@ -1,2 +1,3 @@ def gen_subtitles_queue(file_path: str, t: str) -> None:\n+pass\n"
    assert seams_for_patch(body) == {"gen_subtitles_queue"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /mnt/c/Projects/subarr-subgen && python -m pytest tests/test_capability_audit.py -v`
Expected: FAIL with `ImportError: cannot import name 'seams_for_patch'`

- [ ] **Step 3: Write the minimal implementation**

Append to `scripts/capability_audit.py`:

```python
# `@@ -a,b +c,d @@ <context>` -- git puts the enclosing def/class in <context>,
# which is precisely "the symbol this hunk attaches to".
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@\s*(?P<ctx>.*)$")
_SYMBOL = re.compile(r"^(?:async\s+)?(?:def|class)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)")


def seams_for_patch(patch_text: str) -> set[str]:
    """Upstream symbols this patch attaches to, from its hunk headers.

    A hunk whose header carries no enclosing def/class (e.g. one at module top)
    contributes no seam -- it anchors on file position, not on a symbol.
    """
    seams: set[str] = set()
    for line in patch_text.splitlines():
        m = _HUNK.match(line)
        if not m:
            continue
        sym = _SYMBOL.match(m.group("ctx").strip())
        if sym:
            seams.add(sym.group("name"))
    return seams
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /mnt/c/Projects/subarr-subgen && python -m pytest tests/test_capability_audit.py -v`
Expected: PASS, 13 passed

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Projects/subarr-subgen
git add scripts/capability_audit.py tests/test_capability_audit.py
git commit -m "feat(#171): extract patch seams from hunk headers"
```

---

## ⛔ Tasks 4 and 5 below are SUPERSEDED — read this first

**The name-matching classifier specified below does not work.** It was replaced on
2026-08-06 after review, with the user's approval, by a **per-hunk applicability probe**.
The original text is kept because the reasoning matters, but do not implement it.

Three findings killed it, each verified directly against the repo:

1. **Its alarm is unreachable.** Of the upstream symbols our patches attach to, the
   number the branch removed is **zero**:
   `(all_seams ∩ base_symbols) − branch_symbols == ∅`. `GONE_CANDIDATE` — the verdict
   that fires the veto — cannot occur on this data. The tool would emit 16 green rows
   and a report reading *"no seam lost"* **by construction**, whatever upstream did.

2. **Name survival is the wrong measurement.** `args.update(kwargs)` goes **3 → 0**
   between base and branch, while every enclosing function name survives.
   `asr_task_worker` still exists, so `asr_arena` and `per_request_kwargs` both score
   PORTABLE — on a function whose `args` dict was deleted and replaced by `fw_kwargs`.

3. **The one genuinely dead capability is invisible to it.** On the branch, `regroup`
   survives only as a comment and inside `_STABLE_TS_KWARGS` — the **strip-list**, i.e.
   explicitly filtered out before reaching faster-whisper. Zero live uses. That is
   `CUSTOM_REGROUP`, excluded from the classifier by design because it is set via
   `args['regroup']` rather than a flag literal.

Two corrections made before this were real but did not rescue it. Partitioning seams
against the pinned base is **correct and retained in spirit** (it fixes a spurious
`runtime_config` veto caused by 4 of 27 seams — `queue_status`, `runtime_config`,
`detect_language_robust`, `detect_language_robust_task` — being functions *our own*
patches create, `queue_status` by `0007`). Transitive resolution was **rejected**: all
7 affected capabilities collapse to one identical answer resting partly on `webui`, a
proximity label whose own hunk fails on the branch.

**The replacement, implemented as the new Tasks 4 and 5:** split every patch into
individual hunks and ask `git apply --check` whether each still applies — against the
pinned base and against the branch. A hunk that **applies to base and fails on branch**
is a real, localized break. A trial run found **11 such hunks** the classifier called
PORTABLE, in `asr_task_worker`, `gen_subtitles`, and `detect_language_task`.
A hunk that fails on *base* too is **inconclusive**, not broken — it depends on earlier
patches in the stack — so the probe yields a **lower bound**, which is the honest
direction for a veto instrument to err.

`seams_for_patch` (Task 3) is **retained as a triage index**, not the verdict.
`CUSTOM_REGROUP` becomes a **hard-coded GONE row in the report body**, not a source
comment — it is the single real seam loss and the tool must not be able to omit it.

---

## Task 4 (SUPERSEDED): Classify each capability against the branch

**The three verdicts, and why the middle one is the default:**

- **NATIVE** — every seam is missing *because upstream now does the thing itself*. Cannot be detected mechanically; this verdict is only ever assigned by the manual pass in Task 6. The classifier never returns it.
- **PORTABLE** — at least one seam still exists on the branch. Re-porting is ordinary sync work.
- **GONE** — no seam exists. **Mechanical GONE is a candidate, not a verdict** (Task 6 confirms or overturns it).

**Files:**
- Modify: `scripts/capability_audit.py`
- Modify: `tests/test_capability_audit.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_capability_audit.py`:

```python
from scripts.capability_audit import Verdict, classify_capability


def test_capability_with_a_surviving_seam_is_portable():
    v = classify_capability({"queue_status", "gone_fn"}, branch_symbols={"queue_status"})
    assert v is Verdict.PORTABLE


def test_capability_with_no_surviving_seam_is_gone_candidate():
    v = classify_capability({"regroup_apply"}, branch_symbols={"queue_status"})
    assert v is Verdict.GONE_CANDIDATE


def test_capability_with_no_seams_at_all_is_gone_candidate():
    # A patch that anchors only on file position gives us nothing to check, so it
    # must be escalated to a human rather than assumed fine.
    assert classify_capability(set(), branch_symbols={"queue_status"}) is Verdict.GONE_CANDIDATE


def test_classifier_never_returns_native():
    # NATIVE requires reading upstream's code and is a human judgement (Task 6).
    for seams in ({"a"}, set(), {"queue_status"}):
        assert classify_capability(seams, branch_symbols={"queue_status"}) is not Verdict.NATIVE
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /mnt/c/Projects/subarr-subgen && python -m pytest tests/test_capability_audit.py -v`
Expected: FAIL with `ImportError: cannot import name 'Verdict'`

- [ ] **Step 3: Write the minimal implementation**

Append to `scripts/capability_audit.py`:

```python
from enum import StrEnum


class Verdict(StrEnum):
    NATIVE = "NATIVE"  # upstream does it itself; only ever assigned by a human
    PORTABLE = "PORTABLE"  # a seam survives; ordinary re-porting work
    GONE_CANDIDATE = "GONE_CANDIDATE"  # no seam found; needs the manual pass


def classify_capability(seams: set[str], branch_symbols: set[str]) -> Verdict:
    """Mechanical first cut.

    Deliberately cannot return NATIVE: distinguishing "upstream now does this" from
    "this is impossible" requires reading upstream's code, and a script that guessed
    would produce a confident wrong answer on the one question that matters.

    No seams at all also yields GONE_CANDIDATE rather than a pass -- absence of
    evidence is escalated to a human, not treated as evidence of absence.
    """
    if seams & branch_symbols:
        return Verdict.PORTABLE
    return Verdict.GONE_CANDIDATE
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /mnt/c/Projects/subarr-subgen && python -m pytest tests/test_capability_audit.py -v`
Expected: PASS, 17 passed

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Projects/subarr-subgen
git add scripts/capability_audit.py tests/test_capability_audit.py
git commit -m "feat(#171): classify capabilities, never guessing NATIVE"
```

---

## Task 5 (SUPERSEDED): Wire the CLI and generate the mechanical report

**Files:**
- Modify: `scripts/capability_audit.py`
- Create (generated): `docs/drop-stable-ts-capability-audit-2026-08-04.md`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_capability_audit.py`:

```python
from scripts.capability_audit import Verdict, extract_branch_symbols, render_report


def test_extract_branch_symbols_finds_defs_and_classes():
    src = "def alpha(x):\n    pass\n\nasync def beta():\n    pass\n\nclass Gamma:\n    pass\n"
    assert extract_branch_symbols(src) == {"alpha", "beta", "Gamma"}


def test_extract_branch_symbols_ignores_calls_and_strings():
    src = "alpha()\n# def not_a_def\ns = 'class Fake:'\n"
    assert extract_branch_symbols(src) == set()


def test_report_contains_every_capability_and_the_verdict_line():
    rows = [
        ("per_request_kwargs", ["0014.patch"], {"asr"}, Verdict.PORTABLE),
        ("safe_decode_preset", ["0013.patch"], {"gone"}, Verdict.GONE_CANDIDATE),
    ]
    out = render_report(rows, branch_sha="7997624", veto_caps=("per_request_kwargs",))
    assert "per_request_kwargs" in out and "safe_decode_preset" in out
    assert "7997624" in out
    # The verdict line must be present and unambiguous.
    assert "VETO: NOT TRIGGERED" in out


def test_report_veto_fires_when_a_veto_capability_is_gone():
    rows = [("per_request_kwargs", ["0014.patch"], set(), Verdict.GONE_CANDIDATE)]
    out = render_report(rows, branch_sha="7997624", veto_caps=("per_request_kwargs",))
    assert "VETO: TRIGGERED" in out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /mnt/c/Projects/subarr-subgen && python -m pytest tests/test_capability_audit.py -v`
Expected: FAIL with `ImportError: cannot import name 'extract_branch_symbols'`

- [ ] **Step 3: Write the minimal implementation**

Append to `scripts/capability_audit.py`:

```python
import subprocess
import sys
from pathlib import Path

# Losing any of these removes the mechanism behind the arena, the Tuning Lab and
# the #124 federated direction. That is a different decision from degraded output,
# so it stops Phase 2 rather than feeding into it.
VETO_CAPABILITIES = ("per_request_kwargs", "asr_arena", "runtime_config")

_DEF_OR_CLASS = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)", re.M)


def extract_branch_symbols(source: str) -> set[str]:
    """Every def/class name defined in a Python source file."""
    return {m.group("name") for m in _DEF_OR_CLASS.finditer(source)}


def render_report(rows, branch_sha: str, veto_caps=VETO_CAPABILITIES) -> str:
    """Markdown report. ``rows`` is (capability, patches, seams, verdict)."""
    gone = {cap for cap, _, _, v in rows if v is Verdict.GONE_CANDIDATE}
    fired = sorted(gone & set(veto_caps))

    lines = [
        "# drop-stable-ts capability survival audit",
        "",
        f"Branch SHA audited: `{branch_sha}`",
        "",
        "Mechanical first cut. `GONE_CANDIDATE` means no seam was found by script —",
        "it is **not** a verdict until the manual pass in Task 6 confirms it.",
        "",
        "| capability | verdict | providing patches | seams |",
        "| --- | --- | --- | --- |",
    ]
    for cap, patches, seams, verdict in sorted(rows):
        lines.append(
            f"| `{cap}` | **{verdict}** | {', '.join(patches) or '—'} | "
            f"{', '.join(sorted(seams)) or '—'} |"
        )
    lines += ["", "## Verdict", ""]
    if fired:
        lines.append(f"**VETO: TRIGGERED** — {', '.join(fired)} found GONE_CANDIDATE.")
        lines.append("Phase 2 does not run until the manual pass overturns this.")
    else:
        lines.append("**VETO: NOT TRIGGERED** — no veto capability is a GONE candidate.")
    return "\n".join(lines) + "\n"


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    branch_src = subprocess.run(
        ["git", "-C", str(repo / "upstream"), "show", "FETCH_HEAD:subgen.py"],
        capture_output=True, text=True,
    )
    if branch_src.returncode != 0:
        print(
            "error: cannot read FETCH_HEAD:subgen.py. Fetch the branch first:\n"
            "  git -C upstream fetch origin refactor/drop-stable-ts",
            file=sys.stderr,
        )
        return 2
    sha = subprocess.run(
        ["git", "-C", str(repo / "upstream"), "rev-parse", "--short", "FETCH_HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()

    branch_symbols = extract_branch_symbols(branch_src.stdout)
    patches = {p.name: p.read_text(encoding="utf-8") for p in sorted((repo / "patches").glob("*.patch"))}
    cap_map = build_capability_map(patches)

    rows = []
    for cap in sorted(ADVERTISED_CAPABILITIES):
        providers = cap_map.get(cap, [])
        seams: set[str] = set()
        for name in providers:
            seams |= seams_for_patch(patches[name])
        rows.append((cap, providers, seams, classify_capability(seams, branch_symbols)))

    print(render_report(rows, branch_sha=sha))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /mnt/c/Projects/subarr-subgen && python -m pytest tests/test_capability_audit.py -v`
Expected: PASS, 21 passed

- [ ] **Step 5: Generate the report against the real branch**

```bash
cd /mnt/c/Projects/subarr-subgen
git -C upstream fetch origin refactor/drop-stable-ts
python scripts/capability_audit.py > docs/drop-stable-ts-capability-audit-2026-08-04.md
cat docs/drop-stable-ts-capability-audit-2026-08-04.md
```

Expected: a 16-row table, a branch SHA (`7997624` unless the branch moved), and a `VETO:` line.

**Sanity check before continuing:** all 16 capabilities should have at least one
providing patch. This was verified in Task 2 Step 4b: 34 patches, **16/16 attributed,
none unattributed**.

⚠️ An earlier draft of this plan claimed `ignore_forced_subtitles` would show *no*
providing patch, being "inherited from upstream". That is wrong, and it would have
sent you hunting a non-existent bug. It conflates two different things: the **variable**
`ignore_forced_subtitles` is indeed upstream's (6 occurrences in the pinned `subgen.py`),
but the **capability flag** that advertises it is ours, added by
`0022-runtime-config-endpoint.patch:73`. The flag is the contract subarr negotiates
against, so it is correctly attributed to us.

If every capability comes back PORTABLE, be suspicious and confirm `extract_branch_symbols` actually parsed the branch file rather than an empty string — an empty symbol set would make everything GONE, and a symbol set containing everything would make everything PORTABLE.

⚠️ **A PORTABLE here is a name match, not a clean bill of health.** `classify_capability`
only asks whether a seam *name* still exists on the branch; it cannot see whether the
insertion point inside that function survived. Do not read this table as "these are
fine" — the three veto capabilities get a mandatory manual read in **Task 6 Step 0**
regardless of what this column says.

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/Projects/subarr-subgen
git add scripts/capability_audit.py tests/test_capability_audit.py docs/drop-stable-ts-capability-audit-2026-08-04.md
git commit -m "feat(#171): generate the mechanical capability audit report"
```

---

## Task 4 (REVISED): Split patches into individually appliable hunks

The pure half of the probe. No git here — splitting and reconstruction only, so it can
be tested without a checkout. Matches how the rest of this file is built: pure
functions tested, a thin `main()` does the IO.

**Files:** modify `scripts/capability_audit.py`, `tests/test_capability_audit.py`

Adds `Hunk` (a `NamedTuple` of `patch`, `file`, `header`, `body`), `hunks_of(patch_name,
patch_text) -> list[Hunk]`, and `hunk_as_patch(h) -> str` which reconstructs a
standalone patch `git apply` accepts.

Two details carry the weight:

- **Track the target file from `+++ b/<path>`.** `0021` touches `entrypoint.sh`;
  probing that against `subgen.py` is a category error. Real split: 128 `subgen.py`
  hunks + 1 `entrypoint.sh` = **129**, matching the independently established count.
- **Bound each body by the header's declared counts**, not by scanning for the next
  line that looks like a boundary. Both counts are optional (`@@ -5 +5 @@` means one
  line a side).

⚠️ **A backstop is required, and its test must be separate.** A hand-edited patch can
declare more lines than it supplies; unresolved counts then walk through the next
hunk's header and swallow it, losing that hunk. Break out of the body loop on any line
matching the hunk-header regex — safe because every real body line starts with `" "`,
`"+"`, `"-"`, or `"\"`, never `@@` at column 0.

⚠️ **Do not let the backstop hide a broken fixture.** The first draft of this task's
`TWO_HUNKS` fixture declared `@@ -10,3 +10,4 @@` over a body of 2 old / 3 new lines.
The count-bounding test passed — *on the backstop path*, not on the counts. Removing
the backstop showed `hunks_of` returning **1 hunk** with hunk 2's header eaten. Give
the backstop its own test with a deliberately malformed patch, and verify the count
test passes with the backstop commented out.

**Known limitation, deliberately not coded around:** the backstop only catches an
overrun that hits another `@@`. One that runs into a `--- a/<file>` / `+++ b/<file>`
pair is consumed as content (those start with `-`/`+`), mis-attributing later hunks to
the wrong file. Cannot occur here — all 129 hunks have exact counts — so it is recorded
in a comment rather than defended against.

---

## Task 5 (REVISED): Probe every hunk against base and branch, then report

**Files:** modify `scripts/capability_audit.py`; create (generated)
`docs/drop-stable-ts-capability-audit-2026-08-04.md`

**The probe.** Materialise two pristine trees — `HEAD:subgen.py` (pinned base) and
`FETCH_HEAD:subgen.py` (branch) — and run `git apply --check` on each reconstructed
single hunk against each. Three outcomes per hunk:

| base | branch | meaning |
|---|---|---|
| applies | applies | **INTACT** — the branch did not disturb this edit |
| applies | fails | **BROKEN_BY_BRANCH** — real, localized evidence |
| fails | — | **INCONCLUSIVE** — depends on earlier patches in the stack; judged by a human, never counted as broken |

⚠️ **INCONCLUSIVE is not a failure and must never be reported as one.** A hunk failing
on base means the probe cannot see it standalone, not that anything is wrong. Roughly
three quarters of hunks are expected here (patch `0009` alone was 6 of 8), because the
stack is sequential. This makes the probe a **lower bound** on breakage — the honest
direction for a veto instrument to err.

**Rolling up to capabilities.** For each of the 16, union the hunks of its providing
patches. Any `BROKEN_BY_BRANCH` hunk means that capability needs a real rewrite, not a
context re-port.

⚠️ **`build_capability_map` under-counts providers, one-directionally.** It maps a
capability to the patches adding its *flag literal*; implementation patches that never
touch the literal are invisible. `runtime_config` maps to `0022` alone, though `0027`
and `0029` also carry its seam. The bias always shrinks the evidence set, i.e. pushes
toward under-reporting breakage. State this in the report rather than pretending the
rollup is complete.

**The report** must carry, per capability: providing patches, hunk counts by outcome,
and the specific broken hunks with their headers. Plus:

- A hard-coded **`CUSTOM_REGROUP` — GONE** row. It advertises no flag and the tool
  cannot derive it; on the branch `regroup` appears only in a comment and in
  `_STABLE_TS_KWARGS`, the strip-list. It is the single genuine seam loss, and a report
  that can omit it is worthless.
- An explicit statement that INCONCLUSIVE hunks were not counted as breakage.
- The branch SHA.

**Sanity checks before accepting the output:** if *zero* hunks are BROKEN_BY_BRANCH,
suspect the branch tree was not materialised (the trial run found 11). If *every* hunk
is broken, suspect the base tree instead. Confirm `asr_task_worker`, `gen_subtitles`,
and `detect_language_task` appear among the breaks — those are known-good ground truth.

---

## Task 6: Manual second pass over every GONE candidate

**This is the task that produces the actual verdict.** The script can only tell you a *name* disappeared. A capability may survive renamed, relocated, or absorbed into upstream. Skipping this step converts "we did not find it" into "it does not exist", which is the exact error this whole plan is structured to avoid.

**Files:**
- Modify: `docs/drop-stable-ts-capability-audit-2026-08-04.md`

- [ ] **Step 0: Manually confirm the three VETO capabilities — whatever the script said**

⚠️ **Do this even when all three come back PORTABLE. Especially then.**

`classify_capability` returns PORTABLE when `seams & branch_symbols` is non-empty —
a **purely nominal** test. A name surviving does not mean the seam is usable: the
branch can keep `def asr(...)` while rewriting the body our patch inserts into, and
the script cannot tell those apart. So PORTABLE means *"a name matched"*, not
*"this can be re-ported"*.

Left alone, this plan escalates asymmetrically. `GONE_CANDIDATE` is explicitly
"absence of evidence, escalate to a human" — but PORTABLE is accepted on a name
match with nobody looking, and PORTABLE is the answer that lets Phase 2 spend GPU
time. The reassuring verdict must not be the unexamined one.

The whole audit collapses to one bit: does the veto fire. Three capabilities decide
it. Checking three by hand is cheap; being wrong about them is not.

For each of `per_request_kwargs`, `asr_arena`, `runtime_config`, open the actual
seam on the branch and confirm the *insertion point* still exists, not just the name:

```bash
cd /mnt/c/Projects/subarr-subgen
# What does our patch actually attach to? Read the hunk, not just its header.
grep -n -A15 "^@@" patches/<the providing patch>.patch | head -60
# Now read that same function AS IT EXISTS ON THE BRANCH, in full.
git -C upstream show FETCH_HEAD:subgen.py | sed -n '/^\(async \)\?def <seam>/,/^\(async \)\?def /p'
```

Record each one under the `## Manual pass` heading using the same shape as Step 2,
with **Mechanical said:** PORTABLE and a **Found:** line that names the surviving
insertion point and its line number. If the function survives in name only and the
structure our patch needs is gone, say so and treat it as GONE — that fires the veto
and is a legitimate result.

- [ ] **Step 1: For each GONE_CANDIDATE, search the branch for an equivalent seam**

For every capability the report marks `GONE_CANDIDATE`, run these three searches against the branch and record what you find:

```bash
cd /mnt/c/Projects/subarr-subgen
# 1. Did the seam survive under a different name? Search for its distinctive strings.
git -C upstream show FETCH_HEAD:subgen.py | grep -n "<a distinctive literal from the patch>"
# 2. Is there an equivalent dispatch point? e.g. for per-request kwargs, does any
#    request handler still merge caller-supplied options into the transcribe call?
git -C upstream show FETCH_HEAD:subgen.py | grep -nE "def (asr|batch|transcribe|detect)"
# 3. Did upstream implement it natively? Search for the env var or behaviour.
git -C upstream show FETCH_HEAD:subgen.py | grep -n "<the env var name>"
```

- [ ] **Step 2: Record the outcome in the report**

For each candidate, append a section to `docs/drop-stable-ts-capability-audit-2026-08-04.md` under a `## Manual pass` heading, using exactly this shape:

```markdown
### `<capability>` — <NATIVE|PORTABLE|GONE>

**Mechanical said:** GONE_CANDIDATE (seams checked: `<seams>`)

**Searched for:** <what you grepped and why that would have found it>

**Found:** <the equivalent seam and its line, or "nothing">

**Verdict:** <NATIVE|PORTABLE|GONE> — <one sentence of reasoning>
```

A capability is only **GONE** once this section exists and says so. Anything without a manual section is unresolved, not clean.

- [ ] **Step 3: Update the verdict line**

If the manual pass overturns every veto-capability candidate, change the report's verdict line to `**VETO: NOT TRIGGERED**` and state which candidates were overturned and why. If any veto capability is confirmed GONE, leave it TRIGGERED and say so plainly — that outcome stops Phase 2, and the spec treats it as a legitimate result rather than a failure.

- [ ] **Step 4: Add the known-GONE entry for CUSTOM_REGROUP**

`CUSTOM_REGROUP` advertises no capability flag (subarr never calls it; it is an image ENV default consumed by patches `0001` and `0017` setting `args['regroup']`). Its verdict is known in advance: **GONE**, because `regroup` is a stable-ts concept and the branch has zero occurrences of `stable_whisper`. Record it explicitly so the report is complete:

```markdown
### `CUSTOM_REGROUP` (no capability flag) — GONE

**Why it has no flag:** subarr never calls it. It is an image ENV default
("v2-strongpad", winner of the #168 arena) consumed by patches 0001 and 0017.

**Verdict:** GONE — `regroup` is stable-ts-only and the branch has 0 occurrences
of `stable_whisper`.

**Deliberately does NOT trigger the veto.** The #359 retimer was specified as the
"#171-immune" replacement lever and shipped in 2.4.0. Whether it suffices alone is
exactly what Phase 2's arm 2 vs arm 3 post-retime comparison measures.
```

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Projects/subarr-subgen
git add docs/drop-stable-ts-capability-audit-2026-08-04.md
git commit -m "docs(#171): manual pass over GONE candidates + audit verdict"
```

---

## Task 7: Report the verdict on the issue

**Files:** none (GitHub only)

- [ ] **Step 1: Post the verdict to subarr#171**

```bash
gh issue comment 171 --repo coaxk/subarr --body-file - <<'EOF'
## Phase 1 complete — capability survival audit

Branch audited: `refactor/drop-stable-ts` @ <SHA>, <N> commits ahead of main.

| verdict | count | capabilities |
|---|---|---|
| NATIVE | <n> | <list> |
| PORTABLE | <n> | <list> |
| GONE | <n> | <list> |

**<VETO: TRIGGERED / NOT TRIGGERED>** — <one sentence>

Full report with per-capability evidence: `docs/drop-stable-ts-capability-audit-2026-08-04.md` in coaxk/subarr-subgen.

<If not triggered:> Phase 2 (three-arm quality study) is cleared to run.
<If triggered:> Phase 2 does not run. <capability> has no seam on the new base, which is a mechanism loss rather than a quality regression — a different decision, needing its own brainstorm.
EOF
```

- [ ] **Step 2: Push the branch and open a PR in subarr-subgen**

```bash
cd /mnt/c/Projects/subarr-subgen
git push -u origin <branch-name>
gh pr create --title "feat(#171): Phase 1 capability survival audit" --body "Implements Phase 1 of the evidence plan in coaxk/subarr's docs/superpowers/specs/2026-08-04-171-drop-stable-ts-evidence-design.md. Read-only with respect to the patch stack: adds an audit script, its tests, and the generated report. Verdict is in the report and mirrored on coaxk/subarr#171."
```

---

## Self-review notes (for the reviewer, not a task)

**Spec coverage.** The spec's Phase 1 requires: capabilities as the unit (Task 2), the three-verdict classification (Task 4), the mechanical-pass-is-not-the-answer discipline (Task 4's refusal to emit NATIVE plus Task 6), the veto condition on `per_request_kwargs`/`asr_arena`/`runtime_config` (Task 5's `VETO_CAPABILITIES`), `CUSTOM_REGROUP` recorded as known-GONE-but-not-veto (Task 6 Step 4), and a committed report with an explicit verdict line (Tasks 5–6). Phase 2 is deliberately out of scope.

**Known limitation, stated rather than hidden.** Seam extraction reads hunk headers, so a patch that anchors only on file position contributes no seams and lands in GONE_CANDIDATE. That is intentional over-referral: it costs a manual check and cannot produce a false clean.
