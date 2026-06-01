# Whisper output tuning

Recipes for tuning subgen's per-language `SUBGEN_KWARGS_LANG_<CODE>`
blocks. Edit your subgen `compose.yaml`, restart the container, then
re-run a single file to A/B the result.

> Surface the rendered values in subarr at
> [Settings → Subgen → "Per-language tuning"](/settings#integration:subgen).
> That panel reads /api/mode straight from the compose file so what
> you edit becomes visible in the UI without a subarr restart.

## The 6 most-felt failure modes

These are the symptoms users report in transcribed subs and the
specific kwargs that move the needle on each.

### 1. "Subs disappear too fast"

The model produces a perfectly timed end-of-segment but the user is
still reading. Reading speed is ~150 ms/word; an 8-word line needs at
least 1.2 s on screen.

**Fix**: `CUSTOM_REGROUP` post-process. The default `cm_sl=84_sl=42`
splits aggressively. Use `cm_sl=84_sl=64++sg=.5_sl=42++++++1` — `sg=.5`
forces a minimum gap of 500ms between consecutive lines, giving the
reader breathing room.

### 2. "Opening line is gibberish / repeats"

Whisper's first ~5 seconds are the worst-quality. The model has zero
context and the VAD filter sometimes lets noise through. Common
manifestations:
- Repeated words ("the the the…")
- Hallucinated dialogue not in the audio
- Cut-off mid-sentence

**Fix A**: `condition_on_previous_text: false` (already in the global
defaults). Prevents the cold-start gibberish from polluting later
segments.

**Fix B**: Per-language `initial_prompt` seeding. For French
historical/period drama (e.g. _Flics_, _Cette nuit-là_):

```yaml
SUBGEN_KWARGS_LANG_FR: '{
  "initial_prompt": "Une série française. Les dialogues comportent des noms propres et de l'argot parisien.",
  "patience": 1.2,
  "repetition_penalty": 1.10
}'
```

For Japanese anime / live action:

```yaml
SUBGEN_KWARGS_LANG_JA: '{
  "initial_prompt": "日本語の対話。固有名詞と敬語を含む。",
  "patience": 1.3,
  "length_penalty": 1.3,
  "repetition_penalty": 1.10
}'
```

Korean:

```yaml
SUBGEN_KWARGS_LANG_KO: '{
  "initial_prompt": "한국어 대화. 존댓말과 고유명사를 포함합니다.",
  "patience": 1.2,
  "repetition_penalty": 1.08
}'
```

### 3. "Whole phrases repeat after a long pause"

Classic Whisper loop trap. The model gets stuck on the last
high-confidence phrase during silence and re-emits it.

**Fix**: bump `repetition_penalty` from 1.05 → 1.10 globally OR
per-language. Stronger penalty curbs the loop without affecting
legitimately-repeated dialogue ("Yes. Yes. I see.").

```yaml
SUBGEN_KWARGS: '{... "repetition_penalty": 1.10 ...}'
```

Combine with `no_repeat_ngram_size: 4` (up from 3) for very loop-prone
content. n-gram=4 blocks 4-word repeats; n-gram=3 also blocks "yes I
see" type repeats which is too aggressive for dialogue.

### 4. "Sub stays on screen way too long"

Whisper merged two adjacent segments into one long line. Common when
VAD's `min_silence_duration_ms` is too high — short pauses get treated
as "still talking."

**Fix**: drop `min_silence_duration_ms` from 500 → 350 in
`vad_parameters`. Forces a new segment at shorter pauses.

```yaml
SUBGEN_KWARGS: '{
  ... "vad_parameters": {
    "threshold": 0.5,
    "min_speech_duration_ms": 250,
    "min_silence_duration_ms": 350,
    "speech_pad_ms": 600
  } ...
}'
```

### 5. "Random English in a foreign-language show"

Whisper saw a flicker of English-sounding phonemes and decided that
segment was English. Common with music, sound effects, or speakers
with strong accents.

**Fix**: pin the language via `force_language` at the subarr layer
(use the v4.3 `audio_language_override` API surface we added in
#224) OR set the `SKIP_IF_AUDIO_LANGUAGES` env var so vanilla audio
metadata is trusted when it disagrees with Whisper's auto-detection.

### 6. "Quality improves over time"

Whisper's hidden state warms up as more audio comes in. The first 30s
is genuinely lower quality than the rest. Manifests as: opening
hallucinations + missing dialogue + odd timing.

**Fix**: there is no kwarg that perfectly fixes this — it's a Whisper
limitation. Mitigations:
- Use `initial_prompt` to give the model context-from-text instead of
  context-from-audio (#2 above).
- Use `large-v3` (already default in subarr-subgen) instead of `large-v2`
  or smaller. The v3 model warms up substantially faster.
- Accept that the first ~10-15 lines of long-form transcripts will
  always be marginal. Post-edit if precision matters.

## Recommended global baseline (2026-05-31)

After the Flics overnight run, the curated baseline that handles the
typical English/French/Japanese homelab mix:

```yaml
SUBGEN_KWARGS: '{
  "no_speech_threshold": 0.6,
  "vad_filter": true,
  "vad_parameters": {
    "threshold": 0.5,
    "min_speech_duration_ms": 250,
    "min_silence_duration_ms": 350,
    "speech_pad_ms": 600
  },
  "condition_on_previous_text": false,
  "beam_size": 5,
  "patience": 1.0,
  "length_penalty": 1.0,
  "repetition_penalty": 1.10,
  "no_repeat_ngram_size": 3,
  "compression_ratio_threshold": 2.4,
  "log_prob_threshold": -0.8,
  "temperature": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
}'

CUSTOM_REGROUP: cm_sl=84_sl=64++sg=.5_sl=42++++++1
```

Changes from the v1.1.0 ship default:
- `min_silence_duration_ms`: 500 → 350 (fixes #4)
- `repetition_penalty`: 1.05 → 1.10 (fixes #3)
- `CUSTOM_REGROUP`: added `sg=.5` (fixes #1)

## A/B testing workflow

1. Open Settings → Subgen → "Per-language tuning" in subarr to see
   what's currently loaded.
2. Edit your subgen `compose.yaml` with the proposed change.
3. `docker restart subgen` (or `subgen-next` if you're on dev).
4. Re-run a single file from Coverage. Watch logs — `[v4.3 PATCH]
   audio_language_override=…` line confirms the per-language block
   resolved correctly.
5. Compare the output `.srt` to the previous version. Use `vimdiff`
   or any diff tool.
6. If the new version is materially better, commit the compose change.
   If not, revert.

## When to land tuning changes in the subgen image itself

If a tuning recipe holds up across:
- 3+ different sources for the same language
- A few weeks of in-the-wild use

…promote it from "user-managed compose env" to "default in
subarr-subgen". That means a new patch in the quilt stack
(`patches/0010-tune-<language>.patch` or similar) bumping the
hardcoded defaults inside `subgen.py`. New `subarr-subgen` revs get
the better defaults out of the box; existing users keep their compose
overrides which take precedence.

Per-language kwargs are explicitly designed to be overridable, so
landing improvements at the patch layer is non-breaking for any user
who has already tuned for their setup.
