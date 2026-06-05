"""#123 — reference-free QE / adequacy judge for the tournament.

Validated in the Tier-B spike: LaBSE cross-lingual cosine(source_text,
english_hypothesis) correlates **rho = 0.727** with true translation accuracy
(chrF vs professional reference) — vs the structural judge's 0.41. This is the
accuracy-grade signal that lets the tournament rank GOOD translations by
faithfulness, not just catch structural defects (hallucination/looping/etc).

Split mirrors vad.py:
  - cosine_similarity / qe_adequacy: PURE scoring, fully unit-tested (embedder
    injectable).
  - the LaBSE embedder I/O sits behind qe_available(); opt-in dep, live-verified.

Opt-in like the silero VAD extra. When the embedder is unavailable, qe_adequacy
returns None and the tournament composite falls back to the structural judge
(no penalty). The default backend is sentence-transformers/LaBSE; an onnx-lean
packaging (drop the torch weight) is a tracked follow-up.
"""
from __future__ import annotations

import logging
import os

# Quiet the HuggingFace tokenizers fork-parallelism warning in this threaded
# server context. (Cosmetic — not load-bearing; the LaBSE forward pass runs
# fine in a worker thread, verified.)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

log = logging.getLogger(__name__)

# LaBSE model id; overridable for a pinned local copy / onnx swap.
_MODEL_ENV = "SUBARR_QE_MODEL"
QE_MODEL = "sentence-transformers/LaBSE"


def cosine_similarity(a, b) -> float:
    """Cosine of two equal-length vectors. 0.0 if either is a zero vector."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    # float(): LaBSE feeds numpy float32 vectors → the quotient is a numpy
    # float32, which is NOT JSON-serializable and crashed arena_store.save
    # (the sweep then sat in "running" forever). Always return a builtin float.
    return float(dot / (na * nb))


def qe_available() -> bool:
    """True iff the QE embedder backend is importable. Never raises — callers
    fall back to the structural judge when False."""
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        return False
    return True


_embedder_cache = None


def _default_embed(texts):
    """Embed texts with LaBSE (sentence-transformers). Lazy + process-cached.
    Live-verified I/O, not unit-tested (the model is the boundary)."""
    global _embedder_cache
    from sentence_transformers import SentenceTransformer
    if _embedder_cache is None:
        model = os.environ.get(_MODEL_ENV, QE_MODEL)
        # Prefer a CACHED, offline load: sentence-transformers 5.x otherwise
        # makes HF hub validation calls on load that can HANG on some setups
        # (observed: judge thread deadlocked, CPU idle). Fall back to an online
        # load only if the model isn't cached yet (pre-pull is the real fix).
        try:
            _embedder_cache = SentenceTransformer(model, local_files_only=True)
        except Exception:
            _embedder_cache = SentenceTransformer(model)
    return _embedder_cache.encode(list(texts), normalize_embeddings=True)


def qe_adequacy(source: str, hyp: str, *, embedder=None) -> float | None:
    """Reference-free adequacy: cosine(embed(source), embed(hypothesis)) — how
    faithfully the English hypothesis conveys the source-language text.

    Returns None (caller falls back to the structural judge) when either text
    is empty, or the embedder is unavailable, or embedding fails. `embedder` is
    injectable for tests (a callable: list[str] -> list[vector])."""
    if not (source and source.strip()) or not (hyp and hyp.strip()):
        return None
    if embedder is None:
        if not qe_available():
            return None
        embedder = _default_embed
    try:
        a, b = embedder([source, hyp])
    except Exception:
        log.warning("QE embed failed; falling back to structural judge", exc_info=True)
        return None
    return cosine_similarity(list(a), list(b))
