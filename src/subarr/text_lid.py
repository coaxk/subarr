"""#451 — bounded subtitle TEXT language sanity checker (warning-only, fail-soft).

Compares visible SRT text against explicit job provenance and produces one
structured advisory result. It never gates completion, upload, provider
selection, OCR, or HI policy — the caller (aftercare integration) swallows any
failure and records the result.

This is the TEXT counterpart to `lid.py` (which classifies *audio* waveforms via
silero-lang95 ONNX). We build a separate checker here: optional lazy py3langid,
pinned + checksum-verified model artifact, bounded deterministic sampling, and a
pure `pr451-v1` policy. Base installation imports neither `py3langid` nor
`numpy`; every backend dependency is imported inside a function.

No full subtitle text is persisted or logged — extraction retains only sanitized,
bounded evidence samples.

```
visible = extract_visible_cues(text_bytes)          # sanitized cue texts
regions = classification_regions(visible)           # dedup full/begin/middle/end
result  = check_subtitle_text(text_bytes, ...)      # policy verdict
key     = cache_key(...)                            # exact cache serialization
```
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import posixpath
import threading
from dataclasses import dataclass
from pathlib import Path

from .langs import normalize_lang
from .subtitle_readability import parse_srt
from .subtitle_sanitize import sanitize_cue_text

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (policy `pr451-v1`)
# ---------------------------------------------------------------------------

SUPPORTED_LANGUAGES = frozenset({"de", "en", "es", "fr", "it", "pt"})
POLICY_VERSION = "pr451-v1"
CHECKER_VERSION = "1.0.0"

# Pinned optional dependency (the `[text-lid]` extra ships exactly this).
PY3LANGID_PIN = "py3langid==0.3.0"

# Bounded extraction: never read/decode more than this many bytes of a subtitle.
MAX_BYTES = 32768

# Deterministic sampling: contiguous beginning/middle/end character budgets.
_REGION_BUDGETS = (10922, 10922, 10924)
# "full-text" classification is bounded by the sum of the three region budgets.
_FULL_BUDGET = 32768
# Minimum distinct regions (after dedup) before any PASS/WARN may be emitted.
MIN_REGIONS = 3
# Subtitles with fewer visible alphabetic characters are INCONCLUSIVE.
MIN_ALPHABETIC_CHARS = 80

# Policy thresholds (`pr451-v1`).
THRESHOLD = 0.70  # p_expected >= 0.70
MARGIN = 0.10  # p_expected - p_next >= 0.10
TIE_EPS = 1e-9  # ties within this are ties
MIXED_REGION_PROB = 0.30  # mixed evidence: a language >= 0.30 in >= 2 regions
MIN_SOURCE_REGION_PROB = 0.35  # translation-failure: source >= 0.35 in >= 2 regions
SOURCE_RESIDUE_FRACTION = 0.20  # translation-failure: >= 20% source-residue regions

# Bounded evidence: each region's excerpt in a result is capped here.
_EVIDENCE_SAMPLE_CHARS = 48

# Model artifact (mirrors lid.py's pin + verify + atomic-write pattern).
_MODEL_CACHE_ENV = "SUBARR_MODEL_CACHE"
_MODEL_VERSION = "1.0.0"
# The `[text-lid]` optional dependency (py3langid) is installed SEPARATELY from
# the audio `[lid]` extra. The artifact is acquired LAZILY on the first
# subtitle-text checker backend use: get_classifier() -> pull_model() verifies
# the SHA-256 and writes it atomically into the cache (a present but mismatched
# file is re-fetched and overwritten). ANY failure (missing extra, download,
# checksum, cache dir, classifier init, timeout, inference) degrades to
# UNAVAILABLE; there is NO boot-time fetch and the model is never a hard-required
# dependency. Verified against the bundled py3langid==0.3.0 model
# (py3langid/data/model.plzma) on 2026-08-19 during PR451 phase 4 calibration;
# tests monkeypatch MODEL_SHA256 (same pattern as tests/test_lid.py).
MODEL_URL = "https://github.com/coaxk/subarr-text-lid-model/releases/download/1.0.0/model.pickle"
MODEL_SHA256 = "8c99809ff6de3d129e447306d30ceae4713735230dced7e8d4d46df89e6968ce"

# Thread-safety: py3langid classify()/rank() thread-safety is undocumented, so we
# guard both construction and inference with one lock (research note). Inference
# is run from a ThreadPoolExecutor(max_workers=2) by the aftercare integration.
_CLASSIFIER_LOCK = threading.Lock()
_cached_classifier = None

# Statuses (exactly these five).
PASS, WARN, INCONCLUSIVE, UNSUPPORTED, UNAVAILABLE = (
    "PASS",
    "WARN",
    "INCONCLUSIVE",
    "UNSUPPORTED",
    "UNAVAILABLE",
)

# ---------------------------------------------------------------------------
# Structured result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextLanguageResult:
    """A bounded, advisory verdict. Probabilities, never "confidence"."""

    status: str
    languages: tuple[str, ...]
    evidence: tuple[dict, ...]
    reason: str
    provenance: dict
    checker_version: str
    policy_version: str
    probabilities: dict[str, float] | None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "languages": list(self.languages),
            "probabilities": dict(self.probabilities) if self.probabilities is not None else None,
            "evidence": [dict(e) for e in self.evidence],
            "reason": self.reason,
            "provenance": dict(self.provenance),
            "checker_version": self.checker_version,
            "policy_version": self.policy_version,
        }


def _provenance_dict(
    task,
    source_language,
    target_language,
    submission_origin,
    webhook_event,
    webhook_language,
    webhook_subtitle,
    provenance_conflict,
) -> dict:
    """Provenance state surfaced on a result. Language fields normalized."""
    return {
        "task": task,
        "source": normalize_lang(source_language),
        "target": normalize_lang(target_language),
        "origin": submission_origin,
        "webhook_event": webhook_event,
        "webhook_language": normalize_lang(webhook_language),
        "webhook_subtitle": webhook_subtitle,
        "conflict": provenance_conflict,
    }


def _result(
    status: str,
    reason: str,
    provenance: dict,
    *,
    winner: str | None = None,
    probabilities: dict[str, float] | None = None,
    evidence: tuple[dict, ...] = (),
) -> TextLanguageResult:
    languages = (winner,) if winner else ()
    return TextLanguageResult(
        status=status,
        languages=languages,
        evidence=evidence,
        reason=reason,
        provenance=provenance,
        checker_version=CHECKER_VERSION,
        policy_version=POLICY_VERSION,
        probabilities=probabilities,
    )


# ---------------------------------------------------------------------------
# P3-S1 / P3-S2 — bounded extraction and deterministic sampling
# ---------------------------------------------------------------------------


def extract_visible_cues(text_bytes: bytes) -> list[str]:
    """Decode at most `MAX_BYTES` bytes as UTF-8 (errors='replace'), parse only
    complete cues via the canonical `parse_srt`, discard cues cut by the byte
    boundary, and return sanitized visible text per cue. Retains no full
    subtitle text and never raises."""
    data = text_bytes[:MAX_BYTES]
    truncated = len(text_bytes) > MAX_BYTES
    text = data.decode("utf-8", errors="replace")
    cues = parse_srt(text)
    if truncated and cues:
        # The final cue may have been split by the byte boundary; drop it so only
        # complete cues are inspected.
        cues = cues[:-1]
    return [sanitize_cue_text(c.text) for c in cues]


def build_regions(visible: list[str]) -> list[str]:
    """Deterministic contiguous beginning/middle/end regions over sanitized
    non-empty cues, split at [0,ceil(n/3)), [ceil(n/3),ceil(2n/3)), [ceil(2n/3),n)
    with character budgets 10922/10922/10924. Returns [begin, middle, end] texts
    (an empty string when a region has no cues)."""
    nonempty = [t for t in visible if t]
    n = len(nonempty)
    cuts = [0, math.ceil(n / 3), math.ceil(2 * n / 3), n]
    out = []
    for i in range(3):
        chunk = nonempty[cuts[i] : cuts[i + 1]]
        out.append(" ".join(chunk)[: _REGION_BUDGETS[i]])
    return out


def classification_regions(visible: list[str]) -> list[tuple[str, str]]:
    """Distinct regions for classification in stable full/begin/middle/end order.
    Identical region texts are classified once (stable dedup). Empty regions are
    dropped. At most four entries => at most four classifier calls."""
    begin, middle, end = build_regions(visible)
    full = " ".join(t for t in visible if t)[:_FULL_BUDGET]
    distinct: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, text in (("full", full), ("begin", begin), ("middle", middle), ("end", end)):
        if not text or text in seen:
            continue
        seen.add(text)
        distinct.append((name, text))
    return distinct


def _alphabetic_count(texts: list[str]) -> int:
    return sum(1 for ch in "".join(texts) if ch.isalpha())


# ---------------------------------------------------------------------------
# P3-S3 — lazy optional backend (mirrors lid.py's availability/pull/atomic-cache)
# ---------------------------------------------------------------------------


def runtime_present() -> bool:
    """True iff the optional `py3langid` extra is importable. Never raises."""
    try:
        import py3langid  # noqa: F401
    except Exception:  # noqa: BLE001 - absent extra degrades to UNAVAILABLE
        return False
    return True


def model_cache_dir() -> Path:
    """`SUBARR_MODEL_CACHE/text-lid/1.0.0`. Defaults beside the DB (mirrors
    lid.py's persistence convention) when SUBARR_MODEL_CACHE is unset."""
    base = os.environ.get(_MODEL_CACHE_ENV)
    if not base:
        db = os.environ.get("SUBARR_DB_PATH", "/data/subarr.db")
        base = str(Path(db).parent / "models")
    return Path(base) / "text-lid" / _MODEL_VERSION


def model_target_path() -> Path:
    """Cache location: <cache>/text-lid/1.0.0/<sha256>.pickle."""
    return model_cache_dir() / f"{MODEL_SHA256}.pickle"


def _default_fetch(url: str) -> bytes:
    import httpx

    r = httpx.get(url, follow_redirects=True, timeout=120.0)
    r.raise_for_status()
    return r.content


def pull_model(force: bool = False, *, _fetch=None) -> dict:
    """Download the pinned model, verify SHA256, and write it atomically to the
    cache. Idempotent; never persists a checksum-mismatched file. `_fetch`
    injectable for tests."""
    if not MODEL_SHA256:
        raise RuntimeError("text-lid model checksum is not pinned; refusing to pull")
    target = model_target_path()
    if target.is_file() and not force:
        if hashlib.sha256(target.read_bytes()).hexdigest() == MODEL_SHA256:
            return {"status": "present", "path": str(target)}
    data = (_fetch or _default_fetch)(MODEL_URL)
    digest = hashlib.sha256(data).hexdigest()
    if digest != MODEL_SHA256:
        raise ValueError(f"text-lid model checksum mismatch: got {digest}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return {"status": "downloaded", "path": str(target), "sha256": digest}


def model_available() -> bool:
    """True iff py3langid AND the pinned, verified model file are present."""
    return runtime_present() and model_target_path().is_file()


def get_classifier():
    """Return a cached py3langid LanguageIdentifier restricted to
    SUPPORTED_LANGUAGES, or None when the extra/model is unavailable.

    Single-path LAZY acquisition: on first use, if the optional runtime is
    importable, `pull_model()` is attempted inside the lock (atomic
    checksum-verified cache placement; a present-but-unverified artifact is
    re-fetched and overwritten). Missing optional dependency, download,
    checksum, cache-directory, classifier-init, timeout, and inference
    failures all convert to None -> UNAVAILABLE. Never raises; never fetches
    at boot; the model is never a hard-required dependency.
    """
    global _cached_classifier
    if _cached_classifier is not None:
        return _cached_classifier
    if not runtime_present():
        return None
    with _CLASSIFIER_LOCK:
        if _cached_classifier is not None:
            return _cached_classifier
        try:
            # Idempotent: returns "present" when the verified artifact is
            # already cached (no fetch), downloads + atomically verifies/writes
            # when absent or checksum-mismatched.
            pull_model()
        except Exception:  # noqa: BLE001 - offline/checksum/cache failure -> UNAVAILABLE
            log.warning("text-lid: model acquisition failed; degrading to UNAVAILABLE", exc_info=True)
            return None
        try:
            from py3langid.langid import LanguageIdentifier

            ident = LanguageIdentifier.from_pickled_model(str(model_target_path()), norm_probs=True)
            ident.set_languages(sorted(SUPPORTED_LANGUAGES))
            _cached_classifier = ident
        except Exception:  # noqa: BLE001 - degrade to None -> UNAVAILABLE
            log.warning("text-lid: classifier init failed", exc_info=True)
            _cached_classifier = None
    return _cached_classifier


def classify_text(text: str) -> dict[str, float] | None:
    """Classify one region -> normalized {language: probability} over
    SUPPORTED_LANGUAGES (sum ~= 1.0), or None on missing extra/model or any
    inference failure. Thread-safe via the classifier lock. Never raises."""
    ident = get_classifier()
    if ident is None:
        return None
    try:
        with _CLASSIFIER_LOCK:
            ranked = ident.rank(text)  # [(lang, prob)] over the restricted set
        probs: dict[str, float] = {}
        for lang, p in ranked:
            norm = normalize_lang(lang)
            if norm in SUPPORTED_LANGUAGES:
                probs[norm] = float(p)
        total = sum(probs.values())
        if total > 0:
            probs = {k: v / total for k, v in probs.items()}
        return probs
    except Exception:  # noqa: BLE001 - inference failure degrades to UNAVAILABLE
        log.warning("text-lid: inference failed for a region", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Aggregation and evidence
# ---------------------------------------------------------------------------


def _aggregate(region_probs: list[tuple[str, dict[str, float]]]) -> dict[str, float]:
    """Arithmetic mean of normalized probabilities across distinct regions."""
    if not region_probs:
        return {}
    langs: set[str] = set()
    for _, rp in region_probs:
        langs.update(rp)
    n = len(region_probs)
    out: dict[str, float] = {}
    for lang in langs:
        out[lang] = sum(rp.get(lang, 0.0) for _, rp in region_probs) / n
    return out


def _argmax_lex(probs: dict[str, float]) -> str | None:
    """Language with the highest probability; ties break to the lexicographically
    smallest language."""
    best: str | None = None
    best_key: tuple[float, str] | None = None
    for lang, p in probs.items():
        key = (-p, lang)
        if best_key is None or key < best_key:
            best_key, best = key, lang
    return best


def _build_evidence(
    distinct: list[tuple[str, str]], region_probs: list[tuple[str, dict[str, float]]]
) -> tuple[dict, ...]:
    """Bounded per-region evidence: region name, a short excerpt, and (when
    classified) winner + probabilities. No full subtitle text."""
    probs_by_name = {name: rp for name, rp in region_probs}
    out = []
    for name, text in distinct:
        entry = {"region": name, "sample": text[:_EVIDENCE_SAMPLE_CHARS]}
        rp = probs_by_name.get(name)
        if rp is not None:
            ranked = sorted(rp.items(), key=lambda kv: (-kv[1], kv[0]))
            entry["winner"] = ranked[0][0] if ranked else None
            entry["probabilities"] = {k: round(v, 6) for k, v in rp.items()}
        out.append(entry)
    return tuple(out)


# ---------------------------------------------------------------------------
# Policy helpers
# ---------------------------------------------------------------------------


def _resolve_expected(task, source_language, target_language, expected_languages) -> set[str]:
    """Declared output (contract-target) languages. Prefers explicit
    expected_languages; otherwise derives from the declared contract WITHOUT
    treating source as output when a target is declared."""
    given = {normalize_lang(l) for l in (expected_languages or [])}
    given = {l for l in given if l}
    if given:
        return given
    if task == "translate":
        t = normalize_lang(target_language)
        return {t} if t else set()
    # transcribe: declared contract target, else the (source) output language.
    t = normalize_lang(target_language)
    if t:
        return {t}
    s = normalize_lang(source_language)
    return {s} if s else set()


def _is_mixed(agg: dict[str, float], region_probs: list[tuple[str, dict[str, float]]]) -> bool:
    """Step (e): tied winner, or at least two languages each >= MIXED_REGION_PROB
    in at least two regions."""
    ranked = sorted(agg.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ranked) >= 2 and abs(ranked[0][1] - ranked[1][1]) <= TIE_EPS:
        return True
    langs: set[str] = set()
    for _, rp in region_probs:
        langs.update(rp)
    qualifying = 0
    for lang in langs:
        regions_over = sum(1 for _, rp in region_probs if rp.get(lang, 0.0) >= MIXED_REGION_PROB)
        if regions_over >= 2:
            qualifying += 1
            if qualifying >= 2:
                return True
    return False


def _translation_failure_shape(
    region_probs: list[tuple[str, dict[str, float]]], source_language, task
) -> bool:
    """Step (f): target-claimed translation-failure-shaped evidence. Source
    language >= MIN_SOURCE_REGION_PROB in >= 2 regions AND >=
    SOURCE_RESIDUE_FRACTION of regions are source-residue."""
    if task != "translate":
        return False
    src = normalize_lang(source_language)
    if not src or src not in SUPPORTED_LANGUAGES:
        return False
    n = len(region_probs)
    if n == 0:
        return False
    strong = sum(1 for _, rp in region_probs if rp.get(src, 0.0) >= MIN_SOURCE_REGION_PROB)
    residue = sum(1 for _, rp in region_probs if _argmax_lex(rp) == src)
    return strong >= 2 and (residue / n) >= SOURCE_RESIDUE_FRACTION


def _explicit_source_target_mismatch(winner: str | None, source_language, target_language, task) -> bool:
    """Step (g): a declared translation whose detected winner is the (different)
    source language — explicit source/target mismatch."""
    if task != "translate" or winner is None:
        return False
    src = normalize_lang(source_language)
    tgt = normalize_lang(target_language)
    if not src or not tgt or src == tgt:
        return False
    return winner == src


# ---------------------------------------------------------------------------
# P3-S5 — check_subtitle_text (pr451-v1 precedence)
# ---------------------------------------------------------------------------


def check_subtitle_text(
    text_bytes: bytes,
    *,
    canonical_identity: dict,
    content_sha256: str,
    expected_languages,
    task: str | None = None,
    source_language: str | None = None,
    target_language: str | None = None,
    submission_origin: str | None = None,
    webhook_event: str | None = None,
    webhook_language: str | None = None,
    webhook_subtitle: str | None = None,
    provenance_conflict=None,
) -> TextLanguageResult:
    """Run the bounded text-LID sanity check and emit a structured verdict.
    Never raises; never gates anything. Probabilities only — no "confidence".

    `canonical_identity` and `content_sha256` are REQUIRED keyword arguments
    retained intentionally for **cache-key stability and invalidation**: they
    feed `cache_key()` so a change to `subtitle_path` or `content_sha256`
    (retime/replacement/upload) yields a fresh key. Policy evaluation does not
    otherwise read them — the verdict is derived from task/source/target/
    expected-language provenance and the sampled text. Keeping the signature
    identical lets the aftercare caller pass the same identity+hash object to
    both `check_subtitle_text` and `cache_key`.
    """
    provenance = _provenance_dict(
        task,
        source_language,
        target_language,
        submission_origin,
        webhook_event,
        webhook_language,
        webhook_subtitle,
        provenance_conflict,
    )

    # --- sampling (P3-S1/S2): zero regions / too few regions -> INCONCLUSIVE ---
    visible = extract_visible_cues(text_bytes)
    nonempty = [t for t in visible if t]
    if not nonempty:
        return _result(INCONCLUSIVE, "malformed_or_empty", provenance)

    distinct = classification_regions(nonempty)
    if len(distinct) < MIN_REGIONS:
        return _result(INCONCLUSIVE, "insufficient_regions", provenance)

    # --- (a) backend unavailable -> UNAVAILABLE ------------------------------
    if get_classifier() is None:
        return _result(UNAVAILABLE, "backend_unavailable", provenance)

    # --- (b) short / markup-only / malformed -> INCONCLUSIVE -----------------
    if _alphabetic_count(nonempty) < MIN_ALPHABETIC_CHARS:
        return _result(INCONCLUSIVE, "too_short", provenance)

    # --- (c) unknown task/language provenance -> INCONCLUSIVE ----------------
    task_norm = (task or "").strip().lower()
    if task_norm not in ("transcribe", "translate"):
        return _result(INCONCLUSIVE, "unknown_task_provenance", provenance)
    expected = _resolve_expected(task_norm, source_language, target_language, expected_languages)
    if not expected:
        return _result(INCONCLUSIVE, "unknown_language_provenance", provenance)

    # --- (d) known language outside the six -> UNSUPPORTED -------------------
    if any(e not in SUPPORTED_LANGUAGES for e in expected):
        return _result(UNSUPPORTED, "unsupported_language", provenance)

    # --- classify distinct regions (<=4 calls) -------------------------------
    region_probs = [(name, p) for name, text in distinct if (p := classify_text(text)) is not None]
    if not region_probs:
        return _result(UNAVAILABLE, "inference_failed", provenance)

    agg = _aggregate(region_probs)
    winner = _argmax_lex(agg)
    evidence = _build_evidence(distinct, region_probs)

    # --- (e) mixed evidence -> INCONCLUSIVE ----------------------------------
    if _is_mixed(agg, region_probs):
        return _result(
            INCONCLUSIVE,
            "mixed_evidence",
            provenance,
            winner=winner,
            probabilities=agg,
            evidence=evidence,
        )

    # --- (f) translation-failure shape -> WARN -------------------------------
    if _translation_failure_shape(region_probs, source_language, task_norm):
        return _result(
            WARN,
            "likely_untranslated_source",
            provenance,
            winner=winner,
            probabilities=agg,
            evidence=evidence,
        )

    # --- (g) explicit source/target mismatch -> WARN -------------------------
    if _explicit_source_target_mismatch(winner, source_language, target_language, task_norm):
        return _result(
            WARN,
            "source_target_mismatch",
            provenance,
            winner=winner,
            probabilities=agg,
            evidence=evidence,
        )

    # --- (h) sufficient expected winner -> PASS ------------------------------
    p_expected = max(agg.get(e, 0.0) for e in expected)
    p_next = max((agg.get(l, 0.0) for l in SUPPORTED_LANGUAGES if l not in expected), default=0.0)
    if p_expected >= THRESHOLD and (p_expected - p_next) >= MARGIN:
        return _result(
            PASS,
            "expected_language",
            provenance,
            winner=winner,
            probabilities=agg,
            evidence=evidence,
        )

    # --- (i) otherwise ordinary mismatch -> WARN -----------------------------
    return _result(
        WARN,
        "ordinary_mismatch",
        provenance,
        winner=winner,
        probabilities=agg,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# P3-S6 — canonical identity and exact cache keying
# ---------------------------------------------------------------------------


def _canonical_posix(p) -> str:
    """Normalize a path to a canonical POSIX string (forward slashes, lexical
    collapse of ./ and .., no leading './'). Deterministic; no filesystem access."""
    s = str(p).replace("\\", "/")
    s = posixpath.normpath(s)
    return "" if s == "." else s


def canonical_subtitle_identity(video_path, subtitle_path, subtitle_language, ledger_id) -> dict:
    """Immutable identity object used to key the advisory cache. Webhook
    evidence is deliberately excluded; it is included as provenance input only."""
    return {
        "video_path": _canonical_posix(video_path),
        "subtitle_path": _canonical_posix(subtitle_path),
        "subtitle_language": normalize_lang(subtitle_language),
        "ledger_id": int(ledger_id),
    }


def cache_key(
    *,
    canonical_identity: dict,
    content_sha256: str,
    expected_languages,
    task: str | None = None,
    source_language: str | None = None,
    target_language: str | None = None,
    submission_origin: str | None = None,
    webhook_event: str | None = None,
    webhook_language: str | None = None,
    webhook_subtitle: str | None = None,
    provenance_conflict=None,
) -> str:
    """Exact cache serialization + SHA-256 key. Webhook evidence is included as
    provenance input but not in the immutable identity. A change to
    `subtitle_path` or `content_sha256` yields a different key (invalidation
    after retime/replacement/upload)."""
    payload = [
        canonical_identity["ledger_id"],
        canonical_identity["video_path"],
        canonical_identity["subtitle_path"],
        canonical_identity["subtitle_language"],
        content_sha256,
        sorted({normalize_lang(l) for l in (expected_languages or []) if l}),
        {
            "task": task,
            "source": source_language,
            "target": target_language,
            "origin": submission_origin,
            "webhook_event": webhook_event,
            "webhook_language": webhook_language,
            "webhook_subtitle": webhook_subtitle,
            "conflict": provenance_conflict,
        },
        PY3LANGID_PIN,
        MODEL_SHA256,
        POLICY_VERSION,
    ]
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()
