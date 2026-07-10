import numpy as np
import pytest
from subarr import lid


def test_labels_loaded_english_index_30():
    labels = lid.load_labels()
    assert len(labels) == 95
    assert labels[30] == "en"  # code only, lowercased


def test_softmax_topk_picks_max_and_english_prob():
    labels = lid.load_labels()
    logits = np.full(95, -5.0, dtype=np.float32)
    logits[30] = 5.0  # English dominant
    top_lang, top_prob, en_prob = lid._verdict_from_logits(logits, labels)
    assert top_lang == "en"
    assert top_prob > 0.99
    assert en_prob == pytest.approx(top_prob)


def test_verdict_reports_english_prob_even_when_foreign_wins():
    labels = lid.load_labels()
    logits = np.full(95, -5.0, dtype=np.float32)
    de = labels.index("de")
    logits[de] = 4.0
    logits[30] = 2.0  # English second
    top_lang, top_prob, en_prob = lid._verdict_from_logits(logits, labels)
    assert top_lang == "de"
    assert 0.0 < en_prob < top_prob


def test_pull_model_verifies_checksum(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_LID_MODEL_PATH", str(tmp_path / "m.onnx"))
    good = b"fake-onnx-bytes"
    import hashlib

    monkeypatch.setattr(lid, "MODEL_SHA256", hashlib.sha256(good).hexdigest())
    res = lid.pull_model(_fetch=lambda url: good)
    assert res["status"] == "downloaded"
    monkeypatch.setattr(lid, "MODEL_SHA256", "0" * 64)
    (tmp_path / "m.onnx").unlink()
    with pytest.raises(ValueError):
        lid.pull_model(_fetch=lambda url: good)
    assert not (tmp_path / "m.onnx").exists()
