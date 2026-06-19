# subarr — project instructions

## Pre-merge review policy (MANDATORY before every merge)

Every PR gets a review scaled to its blast radius **before** it is merged. No merge until the
tier-appropriate review passes and every real finding is fixed or filed as an issue.

| Tier | Scope | Required review |
|------|-------|-----------------|
| **0 — Trivial** | docs, comments, version bumps, config one-liners, mechanical renames, bundle-only churn | Read the full diff (spec + quality). No subagents. |
| **1 — Substantive** | new logic, multi-file changes, behaviour changes, any bugfix with real logic | **Multi-lens adversarial review** — 2–3 reviewer subagents, lenses chosen for the change (correctness, edge/boundary cases, + the change's domain risk). Triage every finding (verify against the code — do NOT rubber-stamp), fix reals or file before merge. |
| **2 — High-stakes** | auth/session, concurrency, data-model/migrations, path/filesystem/security, write-back to an arr, secrets/telemetry, money | Tier-1 **plus a security & failure-mode lens.** Reserve an **ultra** (`/code-review ultra <PR#>`) pass for the milestone/release boundary, not every PR. |

Notes:
- Reviewers are read-only; the controller triages and applies fixes (keeps lenses isolated).
- "Verify, don't rubber-stamp": reviewers sometimes contradict each other or flag non-issues —
  confirm each finding against the actual code before acting (this is how #279's review found 2 real
  bugs and correctly dismissed several non-issues).
- Scale the lenses to the change. A concurrency change gets a races lens; a path change gets a
  traversal/security lens; a parser gets a malformed-input lens.

## Comprehensive review program

A one-time deep sweep of the major/critical subsystems is tracked in memory
(`reference_subarr-review-program`). Each area gets a scoped multi-lens review like #279, producing a
triaged findings list. Auth is reserved for a single final ultra pass once the non-ultra areas are clear.

## Solo-repo merge

Branch protection is bypassed with `gh pr merge <NN> --squash --admin --delete-branch` (solo repo).
The pre-merge review above is the real gate, not branch protection.
