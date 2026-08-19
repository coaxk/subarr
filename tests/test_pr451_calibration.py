"""PR451 phase 4 — text-LID calibration corpus + calibrate script.

Covers (P4-S3):
  * manifest schema / per-row sha256 hash validation
  * stable, id-ordered splits with every category present in train/dev/heldout
  * deterministic report selection (two calibrate() runs -> identical)
  * six-language + category coverage and heldout acceptance in the report
  * translation-failure rows classified as designed (WARN likely_untranslated_source)
  * filename-mismatch provenance — the checker never uses the filename
  * zero malformed/short rows produce PASS/WARN

The corpus is GENERATED deterministically under a module-scoped tmp_path
fixture (gen.write_corpus(root)) — no committed artifacts. The classifier is the
BUNDLED py3langid model (build_classifier() with no external path/SHA pin), and
model_sha256 is computed from the bundled MODEL_FILE bytes at runtime.

The expensive full-corpus py3langid classification (~250s for 1080 rows over the
97-language model) is performed exactly ONCE in a module-scoped fixture; every
calibrate() call reuses those features via calibrate(..., precomputed=...), so
the determinism (two runs) and acceptance assertions stay fast and still exercise
the real report-building/selection/sweep code paths.

Real-backend tests use ``pytest.importorskip("py3langid")`` so the suite still
collects where the optional ``[text-lid]`` extra is absent.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import text_lid_calibrate as cal
from scripts import text_lid_calibration_gen as gen

POLICY = "pr451-v2"

REQUIRED_FIELDS = {
    "id",
    "path",
    "sha256",
    "language",
    "label",
    "task",
    "source_language",
    "target_language",
    "submission_origin",
    "webhook_event",
    "split",
    "text_kind",
}
LANGS = {"de", "en", "es", "fr", "it", "pt"}
CATEGORIES = set(gen.CATEGORY_COUNTS)
SPLITS = {"train", "dev", "heldout"}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """Deterministically generate the calibration corpus under a temp dir and
    return (root, manifest_path). build_corpus uses fixed random.Random seeds per
    language/category, so regeneration is byte-identical across runs."""
    root = tmp_path_factory.mktemp("cal")
    manifest = gen.write_corpus(root)
    return root, manifest


def _classifier():
    pytest.importorskip("py3langid")
    return cal.build_classifier()  # bundled py3langid MODEL_FILE, no path/SHA pin


@pytest.fixture(scope="module")
def feats(corpus):
    """Classify the FULL corpus once (the one expensive py3langid pass over 1080
    rows) and return (train_feats, dev_feats, held_feats). All tests reuse these
    via calibrate(..., precomputed=...) / filtering — never re-classifying."""
    root, _manifest = corpus
    clf = _classifier()
    rows = _rows(corpus)
    train = [r for r in rows if r["split"] == "train"]
    dev = [r for r in rows if r["split"] == "dev"]
    heldout = [r for r in rows if r["split"] == "heldout"]
    return (
        cal.classify_rows(clf, train, root),
        cal.classify_rows(clf, dev, root),
        cal.classify_rows(clf, heldout, root),
    )


def _rows(corpus) -> list[dict]:
    _root, manifest = corpus
    return [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]


def _filter_feats(all_feats, label: str | None = None, split: str | None = None):
    """Filter the precomputed full-corpus features by label/split and build the
    threshold-independent verdict map (mirrors the old _feats_and_fixed, but
    without re-classifying)."""
    out = [
        f
        for f in all_feats
        if (label is None or f["label"] == label) and (split is None or f["split"] == split)
    ]
    return out, {f["id"]: cal.precompute(f) for f in out}


def _bundled_model_sha() -> str:
    """SHA-256 of the bundled py3langid model bytes (data/model.plzma)."""
    pytest.importorskip("py3langid")
    import py3langid
    from py3langid.langid import MODEL_FILE

    return hashlib.sha256((Path(py3langid.__file__).parent / MODEL_FILE).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# manifest / schema / hashes
# ---------------------------------------------------------------------------


def test_manifest_schema_counts_and_hashes(corpus) -> None:
    root, _manifest = corpus
    rows = _rows(corpus)
    assert len(rows) == 1080
    for r in rows:
        assert set(r) == REQUIRED_FIELDS
        assert r["language"] in LANGS
        assert r["label"] in CATEGORIES
        assert r["split"] in SPLITS
        assert r["task"] == "translate"
        assert r["target_language"] == r["language"]
        # fixture exists and its sha256 matches the manifest (hash validation)
        body = (root / r["path"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == r["sha256"]
    # global category counts = 6 languages x per-language allocation
    assert Counter(r["label"] for r in rows) == {k: v * 6 for k, v in gen.CATEGORY_COUNTS.items()}
    # per-language 180 rows
    for lang in LANGS:
        per = [r for r in rows if r["language"] == lang]
        assert len(per) == 180
        assert Counter(r["label"] for r in per) == gen.CATEGORY_COUNTS


def test_stable_idordered_splits_with_full_category_coverage(corpus) -> None:
    rows = _rows(corpus)
    # globally id-ordered
    assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)
    assert len({r["id"] for r in rows}) == 1080  # unique ids
    for lang in LANGS:
        per = sorted((r for r in rows if r["language"] == lang), key=lambda r: r["id"])
        base = per[0]["id"]
        # id-ordered blocks are exactly 100 train / 40 dev / 40 heldout
        for r in per:
            off = r["id"] - base
            expect = "train" if off < 100 else ("dev" if off < 140 else "heldout")
            assert r["split"] == expect
        assert Counter(r["split"] for r in per) == {"train": 100, "dev": 40, "heldout": 40}
        # every split covers every category (proportional allocation)
        for split in SPLITS:
            assert {r["label"] for r in per if r["split"] == split} == CATEGORIES


# ---------------------------------------------------------------------------
# deterministic report selection + report coverage
# ---------------------------------------------------------------------------


def test_deterministic_report_selection(corpus, feats) -> None:
    _root, manifest = corpus
    r1 = cal.calibrate(manifest, _root, POLICY, precomputed=feats)
    r2 = cal.calibrate(manifest, _root, POLICY, precomputed=feats)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    # threshold/margin must be a valid sweep pair in [0,1]
    assert 0.0 <= r1["threshold"] <= 1.0
    assert 0.0 <= r1["margin"] <= 1.0


def test_report_schema_and_acceptance(corpus, feats) -> None:
    _root, manifest = corpus
    rep = cal.calibrate(manifest, _root, POLICY, precomputed=feats)
    model_sha = _bundled_model_sha()
    # schema
    assert rep["schema_version"] == "1.0.0"
    assert rep["model_sha256"] == model_sha  # runtime hash of the bundled MODEL_FILE
    assert len(rep["model_sha256"]) == 64  # and it is a real 64-hex sha256
    assert rep["policy_version"] == POLICY
    assert set(rep["metrics"]["by_language"]) == LANGS
    assert set(rep["metrics"]["by_category"]) == {"mixed", "short", "hard_negative", "translation_failure"}
    # heldout acceptance: clean recall>=95%, false-warning<=5%, mixed abstention>=95%
    o = rep["metrics"]["overall"]
    assert o["recall"] >= 0.95
    assert o["false_warning_rate"] <= 0.05
    assert o["abstention"] >= 0.95
    # hard_negative (mismatch) and translation_failure rows must all be warnings
    assert rep["metrics"]["by_category"]["hard_negative"]["pass"] == 0
    assert (
        rep["metrics"]["by_category"]["translation_failure"]["warn"]
        == rep["metrics"]["by_category"]["translation_failure"]["count"]
    )


# ---------------------------------------------------------------------------
# behavior of the fixed policy over the corpus
# ---------------------------------------------------------------------------


def test_translation_failure_rows_warn_as_designed(feats) -> None:
    feats, fixed = _filter_feats(feats[2], label="translation_failure", split="heldout")
    assert feats, "heldout must contain translation-failure rows"
    for f in feats:
        status, reason = cal.verdict(f, fixed[f["id"]], 0.70, 0.10)
        assert status == "WARN"
        assert reason == "likely_untranslated_source"


def test_filename_mismatch_provenance_never_used(corpus, feats) -> None:
    rows = _rows(corpus)
    # deterministic subset carries a filename token that conflicts with manifest language
    mismatched = [r for r in rows if not r["path"].split("/")[-1].startswith(r["language"])]
    assert mismatched, "corpus must contain filename-mismatch provenance rows"
    clean_mm = [r for r in mismatched if r["label"] == "clean"]
    assert clean_mm, "at least one clean row must have a misleading filename"
    all_feats = feats[0] + feats[1] + feats[2]
    clean_feats, fixed = _filter_feats(all_feats, label="clean")
    for f in clean_feats:
        # clean rows classify PASS from the manifest claim, never the filename
        status, reason = cal.verdict(f, fixed[f["id"]], 0.70, 0.10)
        assert status == "PASS"
        assert reason == "expected_language"


def test_zero_malformed_or_short_pass_warn(feats) -> None:
    all_feats = feats[0] + feats[1] + feats[2]
    m_feats, m_fixed = _filter_feats(all_feats, label="malformed")
    s_feats, s_fixed = _filter_feats(all_feats, label="short")
    feats_ = m_feats + s_feats
    fixed = {**m_fixed, **s_fixed}
    assert feats_
    for f in feats_:
        status, _ = cal.verdict(f, fixed[f["id"]], 0.70, 0.10)
        assert status == "INCONCLUSIVE"
