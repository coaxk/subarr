"""#364 slice 1 — forced-segment detection: pure core + thin I/O helpers.

subarr owns the detection intelligence; subgen stays a thin primitive. This
module is deliberately split into a PURE core (utterance classification, span
merge, mostly-foreign bail, the gate predicate, the SRT emitter — all unit-
tested with no subgen and no audio) and thin I/O wrappers (VAD adapter, ffmpeg
clip) that mirror the existing subarr subprocess/VAD patterns.

All granularity/bias values are named, tunable ForcedSegmentParams — never magic
numbers. Slice 1 is English-primary (primary_lang='en'); slice 3 generalises the
gate to the file's real audio language.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

# A speech utterance is (start_s, end_s); a LID call returns (lang|None, confidence 0..1).
Utterance = tuple[float, float]
LidFn = Callable[[Utterance], "tuple[str | None, float]"]

ENGLISH_TAGS = {"en", "eng"}


@dataclass(frozen=True)
class Span:
    """A merged foreign span in absolute file time (milliseconds)."""

    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(frozen=True)
class ForcedSegmentParams:
    # Detection granularity + bias. Opt-in feature, so thresholds bias toward
    # OVER-flagging: a false positive costs a few GPU seconds + maybe a spurious
    # cue; a false negative loses the scene the user turned this on for.
    primary_lang: str = "en"  # slice 1: English-primary; slice 3 makes this the real audio lang
    min_span_s: float = 2.5  # min-duration output floor (spoken-LID needs ~2-3s to be sure)
    merge_gap_s: float = 1.5  # merge foreign spans separated by <= this so one convo is one cue-set
    conf_floor: float = 0.5  # below this, LID is "uncertain"
    over_flag_low_confidence: bool = True  # uncertain -> treat as foreign (completeness bias)
    mostly_foreign_fraction: float = 0.5  # > this fraction of speech foreign => bail (not a forced case)
    # Overlap tiling for a long continuous utterance that straddles a language
    # switch (spec item 3): tile windows with ~50% stride so a boundary is never
    # hidden at a window edge. 0 disables tiling (slice-1 default keeps it simple
    # — VAD utterances are already pause-bounded; tiling is opt-in tuning).
    max_utterance_s: float = 0.0  # (declared for slice-2 overlap tiling; unused in slice 1)
    overlap_stride_s: float = 15.0  # (declared for slice-2 overlap tiling; unused in slice 1)
    # Slice 2 — local windowed LID (silero-lang95). silero is unreliable on short
    # (<~10s) or non-speech audio, so we classify ~lid_window_s windows of speech,
    # not raw utterances, and only trust CONFIDENT non-English verdicts. These are
    # inert on the slice-1 subgen path (which still uses classify_utterances).
    lid_window_s: float = 15.0  # target window for grouping utterances (spike-verified reliable floor)
    lid_min_confidence: float = 0.5  # softmax floor for a foreign verdict (rejects silero noise)
    lid_max_english_prob: float = 0.25  # reject "foreign" if English is still this plausible


def _is_english(lang: str | None, params: ForcedSegmentParams) -> bool:
    return bool(lang) and lang.lower() in {params.primary_lang.lower(), "eng"} | ENGLISH_TAGS


def classify_utterances(
    utterances: list[Utterance], lid: LidFn, params: ForcedSegmentParams
) -> list[tuple[Utterance, bool]]:
    """Label each utterance foreign (True) / not (False). Foreign iff a
    non-English language was detected at any confidence, OR (over-flag bias) the
    LID was uncertain (confidence < conf_floor). Confident English is not
    foreign."""
    out: list[tuple[Utterance, bool]] = []
    for utt in utterances:
        lang, conf = lid(utt)
        if lang is not None and not _is_english(lang, params):
            foreign = True
        elif params.over_flag_low_confidence and conf < params.conf_floor:
            foreign = True  # uncertain -> suspect (bias to completeness)
        else:
            foreign = False
        out.append((utt, foreign))
    return out


def foreign_fraction(classified: list[tuple[Utterance, bool]]) -> float:
    """Foreign speech seconds / total speech seconds. 0.0 when there is no speech."""
    total = sum(e - s for (s, e), _ in classified)
    if total <= 0:
        return 0.0
    foreign = sum(e - s for (s, e), is_f in classified if is_f)
    return foreign / total


def is_mostly_foreign(classified: list[tuple[Utterance, bool]], params: ForcedSegmentParams) -> bool:
    """True when more than mostly_foreign_fraction of speech is foreign — this is
    a full-transcription / mistagged-audio situation, NOT a forced-segment case.
    The orchestrator bails (emits nothing) and records the result."""
    return foreign_fraction(classified) > params.mostly_foreign_fraction


def merge_foreign_spans(classified: list[tuple[Utterance, bool]], params: ForcedSegmentParams) -> list[Span]:
    """Foreign utterances -> merged Spans (absolute ms). Consecutive foreign
    spans within merge_gap_s fuse; merged spans shorter than min_span_s are
    dropped by the output floor."""
    foreign = sorted([(s, e) for (s, e), is_f in classified if is_f])
    if not foreign:
        return []
    merged: list[list[float]] = [list(foreign[0])]
    for s, e in foreign[1:]:
        if s - merged[-1][1] <= params.merge_gap_s:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out: list[Span] = []
    for s, e in merged:
        if (e - s) >= params.min_span_s:
            out.append(Span(start_ms=int(round(s * 1000)), end_ms=int(round(e * 1000))))
    return out


def assemble_foreign_spans(
    utterances: list[Utterance], lid: LidFn, params: ForcedSegmentParams
) -> list[Span]:
    """Full pure pipeline: classify then merge. Convenience entry point."""
    return merge_foreign_spans(classify_utterances(utterances, lid, params), params)


# --- Slice 2: windowed local-LID helpers (pure) -----------------------------
# A window is (start_s, end_s, [utterance_index, ...]).
Window = "tuple[float, float, list[int]]"


def assemble_windows(utterances: list[Utterance], window_s: float) -> list[tuple[float, float, list[int]]]:
    """Group pause-bounded speech utterances into ~window_s windows for LID.
    Greedy: a window starts at an utterance and absorbs following utterances
    while (utt.end - window_start) <= window_s; each utterance lands in exactly
    one window (no straddling). A single utterance longer than window_s is its
    own window — silero accepts variable length, and slice 2 does not tile
    (overlap tiling is out of scope, see spec S8)."""
    windows: list[tuple[float, float, list[int]]] = []
    i = 0
    n = len(utterances)
    while i < n:
        w_start = utterances[i][0]
        idxs = [i]
        j = i + 1
        while j < n and (utterances[j][1] - w_start) <= window_s:
            idxs.append(j)
            j += 1
        w_end = utterances[idxs[-1]][1]
        windows.append((w_start, w_end, idxs))
        i = j
    return windows


def window_is_foreign(
    top_lang: str | None, top_prob: float, english_prob: float, params: ForcedSegmentParams
) -> bool:
    """The slice-2 gate: a window is foreign iff the top language is non-primary
    AND confident (top_prob >= lid_min_confidence) AND English is implausible
    (english_prob <= lid_max_english_prob). Everything else is treated as primary
    — silero's low-confidence output is noise, so we do NOT over-flag on it."""
    if top_lang is None or _is_english(top_lang, params):
        return False
    if top_prob < params.lid_min_confidence:
        return False
    if english_prob > params.lid_max_english_prob:
        return False
    return True


def expand_window_verdicts(
    utterances: list[Utterance],
    windows: list[tuple[float, float, list[int]]],
    window_foreign: list[bool],
) -> list[tuple[Utterance, bool]]:
    """Assign each utterance its containing window's foreign flag, producing the
    same classified list slice-1's merge_foreign_spans / is_mostly_foreign
    consume. Utterances are returned in original order."""
    flag_by_idx: dict[int, bool] = {}
    for (_s, _e, idxs), is_foreign in zip(windows, window_foreign):
        for k in idxs:
            flag_by_idx[k] = is_foreign
    return [(utterances[k], flag_by_idx.get(k, False)) for k in range(len(utterances))]


_FORCED_TOKEN = "forced"


def forced_sidecar_name(stem: str, lang: str) -> str:
    """[#475] The filename subarr WRITES for a forced sidecar.

    `<stem>.<lang>.forced.srt`. The language code sits immediately before
    `forced` because that is the only order Bazarr parses -- verified against a
    live Bazarr 1.6.0, matching Bazarr's own issue #1516. Plex accepts either
    order, so this form satisfies both consumers.
    """
    return f"{stem}.{lang}.{_FORCED_TOKEN}.srt"


def is_forced_sidecar_for(filename: str, stem: str, lang: str) -> bool:
    """[#475] Does `filename` look like a forced sidecar for `stem` in `lang`?

    Deliberately LIBERAL about order, unlike the writer. Installs that ran #364
    before this fix have `.forced.<lang>.srt` on disk, and if the no-clobber
    gate stopped recognising those it would write a second sidecar in the new
    convention beside the old one -- producing exactly the mixed naming #475
    was opened to escape.

    Write one form, read either.
    """
    name = filename.rsplit("/", 1)[-1]
    if not name.lower().endswith(".srt"):
        return False
    base = name[: -len(".srt")]
    if not base.startswith(stem):
        return False
    parts = [p.lower() for p in base[len(stem) :].split(".") if p]
    if _FORCED_TOKEN not in parts:
        return False
    # Accept 2- and 3-letter codes: subgen emits either depending on
    # SUBTITLE_LANGUAGE_NAMING_TYPE (ISO_639_1 vs ISO_639_2_B).
    want = {lang.lower()}
    if lang.lower() == "en":
        want.add("eng")
    return any(p in want for p in parts)


def build_forced_srt(cues: list[tuple[int, int, str]]) -> str:
    """(start_ms, end_ms, text) cues -> a forced SRT string, 1..N re-indexed,
    absolute-timed. Reuses the shared Cue + render_srt so the wire format matches
    every other subarr-emitted .srt. The `.forced` marker lives in the FILENAME
    (<basename>.<lang>.forced.srt) — cue content is plain.

    [#475] That order is load-bearing and was previously wrong. This docstring
    claimed `.forced.en.srt` is "which Bazarr/Plex recognise". Measured against
    a live Bazarr 1.6.0 it is not: Bazarr sees `.en.forced.srt` and flags it
    forced, and does NOT see `.forced.en.srt` at all. Plex accepts either.
    Bazarr's own issue #1516 states the same expected form. Blank/whitespace lines are stripped so an empty cue never renders."""
    from .subtitle_readability import Cue
    from .subtitle_retime import render_srt

    built: list[Cue] = []
    for start_ms, end_ms, text in cues:
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if not lines:
            continue  # an entirely-blank cue never emits a malformed empty block
        built.append(Cue(index=0, start_ms=int(start_ms), end_ms=int(end_ms), lines=lines))
    return render_srt(built)


# Runtime floor for the gate — a trivially short clip cannot hide a foreign scene
# worth a sidecar. Named, not magic.
GATE_MIN_RUNTIME_S = 120.0


def qualifies_for_forced_segment(
    *,
    audio_langs: list[str] | None,
    embedded_en: str | None,
    lang_class: str | None,
    has_forced_sidecar: bool,
    duration_s: float | None,
    params: ForcedSegmentParams,
    min_runtime_s: float = GATE_MIN_RUNTIME_S,
) -> tuple[bool, str]:
    """Cheap pre-audio filters. Returns (qualifies, reason). A file qualifies iff:
      - its audio is English-tagged (slice 1's primary-language assumption),
      - it is NOT a #357 multilingual file (lang_class != 'multi'),
      - it has NO existing forced English sub (embedded EN(forced) or a
        .forced.en.srt sidecar) — don't redo work, don't clobber,
      - it clears the runtime floor.
    A full (non-forced) English sub does NOT disqualify — that is a different want."""
    langs = {(lang or "").lower() for lang in (audio_langs or [])}
    if not (langs & ({params.primary_lang} | ENGLISH_TAGS)):
        return False, "not_english_audio"
    if (lang_class or "single") == "multi":
        return False, "multilingual"
    if embedded_en == "EN(forced)" or has_forced_sidecar:
        return False, "existing_forced"
    if duration_s is not None and duration_s < min_runtime_s:
        return False, "too_short"
    return True, "ok"


def _vad_speech_ranges(path: str, track: int) -> "list[Utterance] | None":
    """Indirection point so tests can inject speech boundaries without a real
    silero model. Delegates to the shipped VAD (vad.detect_speech_ranges returns
    normalized, gap-merged, min-speech-filtered ranges, or None when VAD is
    unavailable — no model pulled / onnxruntime missing)."""
    from . import vad

    return vad.detect_speech_ranges(path, track=track)


def detect_utterances(fs_path: str, track: int = 0) -> list[Utterance]:
    """VAD-segment the audio into speech utterances (start_s, end_s). Returns []
    when VAD is unavailable — the orchestrator treats an empty utterance list as
    'cannot detect' and records nothing rather than guessing."""
    ranges = _vad_speech_ranges(fs_path, track)
    return list(ranges) if ranges else []


def clip_audio(fs_path: str, start_s: float, end_s: float, out_path: str, track: int = 0) -> None:
    """Extract [start_s, end_s] of audio stream `track` -> 16 kHz mono wav at
    out_path (audio-only keeps the subgen upload tiny). Mirrors the arena
    sampler's ffmpeg invocation (arena_sampler._cut_clip): -ss/-t seek+length,
    -map 0:a:N, -ar 16000 -ac 1, check=True with stderr captured. A generous
    timeout means a hung ffmpeg surfaces as a clip failure instead of blocking
    forever (subarr's event-loop history). Raises subprocess.CalledProcessError
    (or subprocess.TimeoutExpired) on ffmpeg failure — the orchestrator catches
    per-clip so one bad clip never aborts the file."""
    length = max(0.0, end_s - start_s)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(start_s),
        "-i",
        fs_path,
        "-t",
        str(length),
        "-map",
        f"0:a:{track}",
        "-ar",
        "16000",
        "-ac",
        "1",
        out_path,
    ]
    subprocess.run(cmd, check=True, stderr=subprocess.PIPE, timeout=120)
