"""#123 — reference-free QE/adequacy judge.

Validated in the Tier-B spike: LaBSE cross-lingual cosine(source, hypothesis)
correlates rho=0.727 with true translation accuracy (chrF vs pro reference) vs
the structural judge's 0.41. This is the accuracy-grade signal.

Split mirrors vad.py: the PURE scoring (cosine, adequacy with an injected
embedder) is unit-tested here; the LaBSE model I/O sits behind an availability
gate and is live-verified, not unit-tested.
"""
from __future__ import annotations


def _qe():
    from subarr import qe
    return qe


def test_cosine_identical_is_one():
    qe = _qe()
    assert abs(qe.cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) - 1.0) < 1e-9


def test_cosine_orthogonal_is_zero():
    qe = _qe()
    assert abs(qe.cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9


def test_cosine_handles_zero_vector():
    qe = _qe()
    assert qe.cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_qe_adequacy_high_when_source_and_hyp_align():
    qe = _qe()
    # fake embedder: aligned strings → near-parallel vectors
    vecs = {"src": [1.0, 0.0], "good": [0.99, 0.14], "bad": [0.0, 1.0]}
    emb = lambda texts: [vecs[t] for t in texts]
    hi = qe.qe_adequacy("src", "good", embedder=emb)
    lo = qe.qe_adequacy("src", "bad", embedder=emb)
    assert hi > 0.9 and lo < 0.2
    assert hi > lo


def test_qe_adequacy_none_when_unavailable(monkeypatch):
    qe = _qe()
    monkeypatch.setattr(qe, "qe_available", lambda: False)
    # no embedder passed + backend unavailable → graceful None (caller falls back)
    assert qe.qe_adequacy("source", "hyp") is None


def test_qe_adequacy_none_on_empty_text():
    qe = _qe()
    emb = lambda texts: [[1.0, 0.0] for _ in texts]
    assert qe.qe_adequacy("", "hyp", embedder=emb) is None
    assert qe.qe_adequacy("source", "", embedder=emb) is None


def test_qe_available_returns_bool_without_raising():
    qe = _qe()
    assert isinstance(qe.qe_available(), bool)
