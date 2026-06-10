"""#179 — onnx-lean LaBSE backend for the QE judge (no torch).

Reimplements sentence-transformers/LaBSE's 4-module pipeline with
onnxruntime + numpy, verbatim from the model repo's own configs:

    0. Transformer  -> onnx/model.onnx (the official export in the HF repo)
    1. Pooling      -> CLS token        (1_Pooling: pooling_mode_cls_token)
    2. Dense        -> 768->768 + tanh  (2_Dense: bias=true, Tanh)
    3. Normalize    -> L2

Deps: onnxruntime + numpy (already shipped via the [vad] extra) plus
tokenizers + safetensors + huggingface_hub — all light pure wheels. NO torch
(~2GB of wheels), NO sentence-transformers/transformers. That makes the QE
judge bakeable into the image like silero VAD: the [qe-onnx] extra adds ~MBs
of deps; the ~1.9GB model file itself is NOT baked — it's pulled once via
huggingface_hub into the standard HF cache (HF_HOME), the same volume the
torch backend populates, offline-first thereafter.

Split mirrors vad.py / qe.py:
  - cls_pool / dense_tanh / l2_normalize: PURE math, fully unit-tested.
  - the tokenizer/session/weights I/O sits behind onnx_qe_available();
    live-verified against the torch backend (cosine parity), not unit-tested.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# Same repo id the torch backend uses (qe.py reads the same env override).
_MODEL_ENV = "SUBARR_QE_MODEL"
QE_MODEL = "sentence-transformers/LaBSE"

# From sentence_bert_config.json in the model repo.
MAX_SEQ_LENGTH = 256

# Files the pipeline needs, relative to the HF repo root.
_ONNX_FILE = "onnx/model.onnx"
_TOKENIZER_FILE = "tokenizer.json"
_DENSE_FILE = "2_Dense/model.safetensors"


def onnx_qe_available() -> bool:
    """True iff the no-torch backend's deps are importable. Never raises.
    (The model file itself is pulled lazily on first embed, like the torch
    backend — availability is about the runtime, not the download.)"""
    try:
        import huggingface_hub  # noqa: F401
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import safetensors  # noqa: F401
        import tokenizers  # noqa: F401
    except Exception:
        return False
    return True


# ── pure pipeline math (unit-tested) ─────────────────────────────────


def cls_pool(hidden):
    """Module 1: CLS pooling — the first token's hidden state per sequence.
    hidden: (batch, seq, dim) -> (batch, dim)."""
    return hidden[:, 0, :]


def dense_tanh(x, weight, bias):
    """Module 2: torch.nn.Linear semantics (x @ W.T + b) followed by Tanh.
    weight: (out, in) as stored in the safetensors state dict."""
    import numpy as np

    return np.tanh(x @ weight.T + bias)


def l2_normalize(x, eps: float = 1e-12):
    """Module 3: row-wise L2 normalization."""
    import numpy as np

    norms = np.linalg.norm(x, axis=1, keepdims=True)
    return x / np.maximum(norms, eps)


# ── model I/O (live-verified boundary) ───────────────────────────────

_session_cache = None  # (tokenizer, ort_session, input_names, W, b)


def _fetch(repo: str, filename: str) -> str:
    """Resolve a model file from the HF cache, downloading only on a miss.
    Offline-first for the same reason as qe.py's torch path: hub validation
    calls on every load can hang some setups."""
    from huggingface_hub import hf_hub_download

    try:
        return hf_hub_download(repo_id=repo, filename=filename, local_files_only=True)
    except Exception:
        log.info("QE onnx: %s/%s not cached — downloading (one-time)", repo, filename)
        return hf_hub_download(repo_id=repo, filename=filename)


def _load():
    """Build (tokenizer, session, input_names, dense W, dense b). Process-cached."""
    global _session_cache
    if _session_cache is not None:
        return _session_cache

    import numpy as np
    import onnxruntime as ort
    from safetensors.numpy import load_file
    from tokenizers import Tokenizer

    repo = os.environ.get(_MODEL_ENV, QE_MODEL)
    tok = Tokenizer.from_file(_fetch(repo, _TOKENIZER_FILE))
    tok.enable_truncation(MAX_SEQ_LENGTH)
    pad_id = tok.token_to_id("[PAD]") or 0
    tok.enable_padding(pad_id=pad_id, pad_token="[PAD]")

    sess = ort.InferenceSession(_fetch(repo, _ONNX_FILE), providers=["CPUExecutionProvider"])
    input_names = [i.name for i in sess.get_inputs()]

    state = load_file(_fetch(repo, _DENSE_FILE))
    # The Dense state dict is {"linear.weight": (768,768), "linear.bias": (768,)}.
    # Discover by ndim so a key rename upstream doesn't silently break us.
    weight = next(v for v in state.values() if v.ndim == 2).astype(np.float32)
    bias = next(v for v in state.values() if v.ndim == 1).astype(np.float32)

    _session_cache = (tok, sess, input_names, weight, bias)
    return _session_cache


def embed(texts):
    """Embed texts -> L2-normalized (batch, 768) float32, matching the torch
    backend's `encode(..., normalize_embeddings=True)` output."""
    import numpy as np

    tok, sess, input_names, weight, bias = _load()
    encs = tok.encode_batch([str(t) for t in texts])
    feeds_all = {
        "input_ids": np.asarray([e.ids for e in encs], dtype=np.int64),
        "attention_mask": np.asarray([e.attention_mask for e in encs], dtype=np.int64),
        "token_type_ids": np.asarray([e.type_ids for e in encs], dtype=np.int64),
    }
    # Feed only what this export declares (optimum BERT exports take all
    # three, but don't assume).
    feeds = {k: v for k, v in feeds_all.items() if k in input_names}
    hidden = sess.run(None, feeds)[0]  # last_hidden_state: (batch, seq, dim)
    return l2_normalize(dense_tanh(cls_pool(hidden), weight, bias))
