"""#364 slice 2 — silero-lang95 spoken language identification.

Mirrors vad.py exactly: a pinned-URL + SHA256 model pulled on opt-in and
verified before it is written, run on bare onnxruntime + numpy (NO torch). The
95-language label map is committed (data/lid_lang_dict_95.json), not downloaded.
Never raises on a missing/broken model — callers fall back to the subgen LID.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_MODEL_ENV = "SUBARR_LID_MODEL_PATH"
# Immutable revision (pin the repo commit sha at integration, not a moving
# branch). SHA256 is the real guarantee — verified before the file is written.
MODEL_URL = "https://huggingface.co/deepghs/silero-lang95-onnx/resolve/main/lang_classifier_95.onnx"
MODEL_SHA256 = "12a91f706ad6ca5a5eba4e84a96739d077e56f121ec71db86a752f62e218a227"

_LABELS_FILE = Path(__file__).with_name("data") / "lid_lang_dict_95.json"


@lru_cache(maxsize=1)
def load_labels() -> list[str]:
    """Index-ordered ISO codes (lowercased), parsed from the committed label
    map ('<idx>' -> '<iso>, <Name>'). load_labels()[30] == 'en'."""
    raw = json.loads(_LABELS_FILE.read_text(encoding="utf-8"))
    n = len(raw)
    labels = [""] * n
    for k, v in raw.items():
        labels[int(k)] = str(v).split(",")[0].strip().lower()
    return labels


def model_target_path() -> Path:
    """Read/write location for the pulled model. Beside the DB (persisted
    volume) by default; overridable via SUBARR_LID_MODEL_PATH / SUBARR_LID_DIR."""
    override = os.environ.get(_MODEL_ENV)
    if override:
        return Path(override)
    base = os.environ.get("SUBARR_LID_DIR")
    if not base:
        db = os.environ.get("SUBARR_DB_PATH", "/data/subarr.db")
        base = str(Path(db).parent / "lid")
    return Path(base) / "lang_classifier_95.onnx"


def _model_path() -> str | None:
    p = model_target_path()
    return str(p) if p.is_file() else None


def runtime_present() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def _default_fetch(url: str) -> bytes:
    import httpx

    r = httpx.get(url, follow_redirects=True, timeout=120.0)
    r.raise_for_status()
    return r.content


def pull_model(force: bool = False, *, _fetch=None) -> dict:
    """Download the pinned model, verify SHA256, write atomically. Idempotent;
    never persists a checksum-mismatched file. `_fetch` injectable for tests."""
    if not MODEL_SHA256:
        raise RuntimeError("silero-lang95 checksum is not pinned; refusing to pull")
    target = model_target_path()
    if target.is_file() and not force:
        if hashlib.sha256(target.read_bytes()).hexdigest() == MODEL_SHA256:
            return {"status": "present", "path": str(target)}
    data = (_fetch or _default_fetch)(MODEL_URL)
    digest = hashlib.sha256(data).hexdigest()
    if digest != MODEL_SHA256:
        raise ValueError(f"silero-lang95 checksum mismatch: got {digest}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return {"status": "downloaded", "path": str(target), "sha256": digest}


def lid_available() -> bool:
    """True iff onnxruntime AND the model file are present. Never raises."""
    return runtime_present() and _model_path() is not None


def ensure_available() -> bool:
    """Best-effort make the model available (pull if runtime present + missing).
    Returns lid_available() afterwards; logs and returns False on failure so the
    caller falls back to subgen LID."""
    if not runtime_present():
        return False
    try:
        pull_model()
    except Exception as e:  # noqa: BLE001 - offline / fetch failure degrades to subgen
        log.warning("forced-segment: silero-lang95 pull failed (%s); using subgen LID", e)
    return lid_available()


def _verdict_from_logits(logits, labels: list[str]) -> "tuple[str, float, float]":
    """Pure: 95-way logits -> (top_lang_iso, top_prob, english_prob) via softmax."""
    import numpy as np

    v = np.asarray(logits, dtype=np.float64)
    e = np.exp(v - v.max())
    probs = e / e.sum()
    top = int(probs.argmax())
    en = labels.index("en")
    return labels[top], float(probs[top]), float(probs[en])


# silero-lang95 ONNX contract: input 'input' [batch, samples] float32 (16 kHz
# mono waveform); output 'output' = 95-way logits. A second 58-way group head is
# ignored — request only 'output' so it never runs.
_SR = 16000


def classify_samples(samples) -> "tuple[str, float, float] | None":
    """Classify a 16 kHz mono float32 waveform -> (top_lang, top_prob,
    english_prob), or None when unavailable / on any inference error."""
    if not lid_available():
        return None
    try:
        import numpy as np
        import onnxruntime as ort

        sess = ort.InferenceSession(_model_path(), providers=["CPUExecutionProvider"])
        x = np.asarray(samples, dtype=np.float32).reshape(1, -1)
        (logits,) = sess.run(["output"], {"input": x})
        return _verdict_from_logits(logits[0], load_labels())
    except Exception:  # noqa: BLE001 - degrade to None -> caller falls back
        log.warning("silero-lang95 inference failed", exc_info=True)
        return None
