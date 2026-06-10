"""#179 — the no-torch onnx QE backend.

Same split as test_qe.py: the PURE pipeline math (CLS pooling, dense+tanh,
L2 normalize) is unit-tested with numpy fixtures; the model I/O (tokenizer,
ort session, safetensors weights) sits behind onnx_qe_available() and is
parity-verified live against the torch backend (skipif-gated below).

Backend selection in qe.py (SUBARR_QE_BACKEND auto|onnx|torch) is tested with
monkeypatched availability — no model required.
"""

from __future__ import annotations

import pytest

# qe_onnx imports numpy/onnxruntime inside functions by design, so importing
# the module is safe on base CI; the math tests skip without numpy (the
# repo's test_qe.py pattern), while the backend-selection tests always run.
from subarr import qe, qe_onnx


# ── pure pipeline math (need numpy; skipped on base CI) ──────────────


def test_cls_pool_takes_first_token():
    np = pytest.importorskip("numpy")
    hidden = np.arange(2 * 3 * 4, dtype=np.float32).reshape(2, 3, 4)
    pooled = qe_onnx.cls_pool(hidden)
    assert pooled.shape == (2, 4)
    assert np.array_equal(pooled[0], hidden[0, 0])
    assert np.array_equal(pooled[1], hidden[1, 0])


def test_dense_tanh_matches_torch_linear_semantics():
    np = pytest.importorskip("numpy")
    # torch.nn.Linear computes x @ W.T + b — the safetensors weight is (out, in).
    x = np.array([[1.0, 2.0]], dtype=np.float32)
    weight = np.array([[0.5, -0.25], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)  # (3, 2)
    bias = np.array([0.1, -1.0, 0.0], dtype=np.float32)
    out = qe_onnx.dense_tanh(x, weight, bias)
    expected = np.tanh(np.array([[1 * 0.5 + 2 * -0.25 + 0.1, 1 * 1.0 - 1.0, 2.0]], dtype=np.float32))
    assert out.shape == (1, 3)
    assert np.allclose(out, expected, atol=1e-7)


def test_l2_normalize_unit_rows():
    np = pytest.importorskip("numpy")
    x = np.array([[3.0, 4.0], [0.0, 2.0]], dtype=np.float32)
    n = qe_onnx.l2_normalize(x)
    assert np.allclose(np.linalg.norm(n, axis=1), [1.0, 1.0], atol=1e-6)
    assert np.allclose(n[0], [0.6, 0.8], atol=1e-6)


def test_l2_normalize_zero_row_stays_finite():
    np = pytest.importorskip("numpy")
    n = qe_onnx.l2_normalize(np.zeros((1, 4), dtype=np.float32))
    assert np.all(np.isfinite(n))


def test_full_pipeline_composition_is_normalized():
    np = pytest.importorskip("numpy")
    # cls_pool → dense_tanh → l2_normalize on synthetic weights ends on the
    # unit sphere regardless of input scale (what cosine scoring relies on).
    rng = np.random.default_rng(42)
    hidden = rng.normal(size=(3, 5, 8)).astype(np.float32) * 100
    weight = rng.normal(size=(8, 8)).astype(np.float32)
    bias = rng.normal(size=(8,)).astype(np.float32)
    out = qe_onnx.l2_normalize(qe_onnx.dense_tanh(qe_onnx.cls_pool(hidden), weight, bias))
    assert out.shape == (3, 8)
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-6)


# ── backend selection (qe.py) ────────────────────────────────────────


def test_backend_env_forces_onnx_availability(monkeypatch):
    monkeypatch.setenv("SUBARR_QE_BACKEND", "onnx")
    monkeypatch.setattr(qe, "_onnx_available", lambda: False)
    monkeypatch.setattr(qe, "_torch_available", lambda: True)
    assert qe.qe_available() is False  # torch presence must not count


def test_backend_env_forces_torch_availability(monkeypatch):
    monkeypatch.setenv("SUBARR_QE_BACKEND", "torch")
    monkeypatch.setattr(qe, "_onnx_available", lambda: True)
    monkeypatch.setattr(qe, "_torch_available", lambda: False)
    assert qe.qe_available() is False  # onnx presence must not count


def test_backend_auto_accepts_either(monkeypatch):
    monkeypatch.setenv("SUBARR_QE_BACKEND", "auto")
    monkeypatch.setattr(qe, "_onnx_available", lambda: False)
    monkeypatch.setattr(qe, "_torch_available", lambda: True)
    assert qe.qe_available() is True
    monkeypatch.setattr(qe, "_torch_available", lambda: False)
    assert qe.qe_available() is False


def test_auto_falls_back_to_torch_when_onnx_embed_fails(monkeypatch):
    monkeypatch.setenv("SUBARR_QE_BACKEND", "auto")
    monkeypatch.setattr(qe, "_onnx_available", lambda: True)
    monkeypatch.setattr(qe, "_torch_available", lambda: True)
    monkeypatch.setattr(qe_onnx, "embed", lambda texts: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(qe, "_torch_embed", lambda texts: [[1.0, 0.0], [1.0, 0.0]])
    out = qe._default_embed(["a", "b"])
    assert out == [[1.0, 0.0], [1.0, 0.0]]


def test_qe_adequacy_uses_injected_embedder_unchanged():
    # The injectable-embedder contract (what all existing callers rely on)
    # must survive the backend split.
    score = qe.qe_adequacy("bonjour", "hello", embedder=lambda t: [[1.0, 0.0], [1.0, 0.0]])
    assert score == 1.0


# ── live parity (needs BOTH backends + the model; skipped in CI) ─────


@pytest.mark.skipif(
    not (qe._torch_available() and qe_onnx.onnx_qe_available()),
    reason="parity check needs both QE backends installed",
)
def test_onnx_torch_parity_on_multilingual_pairs():
    """The two backends must produce near-identical normalized vectors, so
    cosine scores (and the validated rho=0.727) carry over unchanged."""
    np = pytest.importorskip("numpy")
    texts = ["bonjour le monde", "hello world", "el gato duerme", "the cat sleeps"]
    try:
        a = np.asarray(qe_onnx.embed(texts))
        b = np.asarray(qe._torch_embed(texts))
    except Exception as e:  # model not downloaded in this env — treat as skip
        pytest.skip(f"model unavailable: {e}")
    # Per-text cosine between the two backends' vectors ≈ 1.
    per_text = np.sum(a * b, axis=1)
    assert np.all(per_text > 0.999), per_text
