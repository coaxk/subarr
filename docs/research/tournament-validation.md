# Tournament judge — Tier-B validation & hardening

*How subarr's Whisper-tuning tournament judge was validated against real subtitle
accuracy, what it can and can't be trusted to do, and what's left to reach
accuracy-grade per-language ranking. (#65)*

## Why this exists

The tournament runs the same source audio through several Whisper/subgen
configurations ("entrants") and ranks the resulting subtitles, so the best
configuration can be adopted. The hard question isn't *running* the configs —
it's **judging** the outputs without a ground-truth reference, since in
production there is no "correct" subtitle to compare against.

The thesis is *measure, don't guess*. So before trusting any verdict, we
validated the judge against real translation accuracy — and were honest about
where it holds and where it doesn't.

## The judges (reference-free)

Every judge is deterministic arithmetic over the SRT (and, where noted, a
silero VAD speech map) — no model in the verdict path, fully reproducible and
testable:

- **Readability** — CPS / CPL / line-count / duration / overlap vs
  Netflix/BBC-style norms. *Secondary, capped:* it can shave at most a few
  points; it must never floor an otherwise-good transcript.
- **Hallucination (`silence_text_ratio`)** — fraction of subtitle time sitting
  over VAD-detected *non-speech*. Text where there's no voice = fabrication.
- **Looping (`repeated_line_ratio`)** — duplicate-line fraction; catches
  stuck-decoder output.
- **Canned phrases** — the canonical non-speech hallucinations
  ("thanks for watching", "subtitles by amara.org", …).
- **Completeness (`uncovered_speech_ratio`)** — speech with *no* subtitle =
  dropped dialogue. The complement of the hallucination judge; added to fix a
  terseness bias (see below).
- **Consensus** — a cross-config *pseudo-reference*: the majority content-word
  vocabulary across entrants yields per-entrant precision / recall / f1, and a
  `clip_agreement` score (mean pairwise vocabulary overlap) that flags
  low-confidence clips for human review.

## Two bugs the validation surfaced

1. **The configs weren't actually different.** subgen parsed its
   `SUBGEN_KWARGS` env with `ast.literal_eval`, which can't parse JSON booleans,
   so it silently fell back to `{}` (the warning was swallowed before logging
   was configured). *Every* tournament entrant had been running identical empty
   kwargs — which is exactly why the first run looked like a tie. Fixed with a
   `json.loads` → `ast` fallback that logs loudly on failure.
2. **Readability was flooring good output.** The composite was
   `readability − QE`, so an accurate, speech-aligned transcript of *fast*
   dialogue (high CPS) scored ~0 — masking the QE signals that actually
   discriminate. Fixed: QE-primary, readability a capped secondary penalty.

## The validation arc

Configs were run through real subgen on real library audio, in escalating
rounds, in `translate` mode (the real multilingual use case):

| Round | Setup | Finding |
|---|---|---|
| 1 | n=1 clip / language | Clean per-language winners *appeared* to diverge — **a mirage.** |
| 2 | n=3 / language | Winners flipped per clip → round 1 was noise. The robust signal is *failure modes*, not winners. |
| 3 (scale) | n≈10 / language, 6 languages incl. CJK | Winner still unclaimable — top configs cluster within noise. CJK shows systematically lower cross-config agreement than European languages. |
| 4 (reference) | chrF vs official embedded professional EN subs (DE/ES/ZH) | **Spearman(judge, accuracy) = 0.33**; judge-winner == accuracy-best only 28%. Surfaced a **terseness bias** — the judge rewarded short output, i.e. *dropped* content. |
| post-fix | + completeness judge | Coverage term lifts Spearman **0.33 → 0.46**. |

Diagnostic correlations with true accuracy: consensus precision/f1 (~0.40) and
cue-count (~0.39) are the best predictors; readability (~0.09) and
hallucination (~0.00) are *failure-catchers*, not accuracy-rankers — as
designed.

## What the judge can and can't be trusted to do

- ✅ **Structural correctness.** Gibberish, hallucination, looping, canned
  phrases, dropped dialogue — caught reliably. This is validated base camp.
- ❌ **Per-language accuracy winner.** Winners are clip-noise; correlation with
  true accuracy is only moderate (ρ≈0.46). Per-language "adopt this config"
  claims are *not yet* defensible.
- 🎚️ **`clip_agreement` is a calibrated confidence dial.** European languages
  ~0.75–0.81 (trust); CJK ~0.57–0.64 (flag for human review).

So the honest product shape is **"configuration risks to avoid + a safe default
+ a low-confidence flag,"** not "adopt this per-language configuration":
`clean_film` reliably loops (avoid), `high_beam` is boom-or-bust, and the
`base` configuration is the safe accurate default.

## Methodology notes (kept honest)

- No ground truth exists in production → the judge measures well-formedness +
  agreement, not faithfulness. chrF-vs-professional-reference is a *calibrator*,
  not a runtime judge.
- English-only human testing was rejected: it validates mechanics, not
  translation faithfulness. Foreign-audio-vs-professional-reference is the only
  thing that measured real accuracy.
- *n* matters: n=1 lied, n=3 corrected, n=10 settled. Tested within-film;
  cross-film generality is untested.
- chrF against a single reference is noisy and favors terse output (professional
  subs condense) — yet terse still lost, so the completeness finding is robust.

## The summit: accuracy-grade ranking

Ranking *good* translations by faithfulness needs a model of **adequacy** —
does the English convey what the source said? That's a meaning problem, not a
math problem, so it's the one place a model earns its seat. The path (see
issue #123): a **reference-free QE model** (cross-lingual embeddings / COMET-QE /
LLM-judge), unlocked by one extra source-language transcribe pass per clip, and
**calibrated against the same chrF rig** (target ρ≥0.6). The chrF rig is kept as
a permanent regression harness — the terseness bias proved judges can carry
silent biases that only reference-validation reveals.

Only once a QE method clears that bar do per-language winner claims — and the
crowd-aggregated tuning loop they enable (#124) — become defensible.
