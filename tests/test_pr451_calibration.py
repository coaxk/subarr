"""PR451 phase 4 — text-LID calibration corpus + calibrate script.

Covers (P4-S3):
  * manifest schema / per-row sha256 hash validation
  * stable, id-ordered splits with every category present in train/dev/heldout
  * deterministic report selection (two calibrate() runs -> identical)
  * six-language + category coverage and heldout acceptance in the report
  * translation-failure rows classified as designed (WARN likely_untranslated_source)
  * filename-mismatch provenance — the checker never uses the filename
  * zero malformed/short rows produce PASS/WARN

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

BASE = _ROOT / "artifacts/calibration/pr451-text-lid"
MANIFEST = BASE / "manifest.jsonl"
MODEL_SHA = "8c99809ff6de3d129e447306d30ceae4713735230dced7e8d4d46df89e6968ce"
POLICY = "pr451-v1"

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


def _rows() -> list[dict]:
    return [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]


def _classifier():
    pytest.importorskip("py3langid")
    return cal.build_classifier(cal.resolve_model_path(MODEL_SHA), MODEL_SHA)


def _feats_and_fixed(clf, label: str | None = None, split: str | None = None):
    rows = _rows()
    if label is not None:
        rows = [r for r in rows if r["label"] == label]
    if split is not None:
        rows = [r for r in rows if r["split"] == split]
    feats = cal.classify_rows(clf, rows, BASE)
    fixed = {f["id"]: cal.precompute(f) for f in feats}
    return feats, fixed


# ---------------------------------------------------------------------------
# manifest / schema / hashes
# ---------------------------------------------------------------------------


def test_manifest_schema_counts_and_hashes() -> None:
    rows = _rows()
    assert len(rows) == 1080
    for r in rows:
        assert set(r) == REQUIRED_FIELDS
        assert r["language"] in LANGS
        assert r["label"] in CATEGORIES
        assert r["split"] in SPLITS
        assert r["task"] == "translate"
        assert r["target_language"] == r["language"]
        # fixture exists and its sha256 matches the manifest (hash validation)
        body = (BASE / r["path"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == r["sha256"]
    # global category counts = 6 languages x per-language allocation
    assert Counter(r["label"] for r in rows) == {k: v * 6 for k, v in gen.CATEGORY_COUNTS.items()}
    # per-language 180 rows
    for lang in LANGS:
        per = [r for r in rows if r["language"] == lang]
        assert len(per) == 180
        assert Counter(r["label"] for r in per) == gen.CATEGORY_COUNTS


def test_stable_idordered_splits_with_full_category_coverage() -> None:
    rows = _rows()
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


def test_deterministic_report_selection() -> None:
    clf = _classifier()
    r1 = cal.calibrate(MANIFEST, BASE, MODEL_SHA, POLICY, classifier=clf)
    r2 = cal.calibrate(MANIFEST, BASE, MODEL_SHA, POLICY, classifier=clf)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    # threshold/margin must be a valid sweep pair in [0,1]
    assert 0.0 <= r1["threshold"] <= 1.0
    assert 0.0 <= r1["margin"] <= 1.0


def test_report_schema_and_acceptance() -> None:
    clf = _classifier()
    rep = cal.calibrate(MANIFEST, BASE, MODEL_SHA, POLICY, classifier=clf)
    # schema
    assert rep["schema_version"] == "1.0.0"
    assert rep["model_sha256"] == MODEL_SHA
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


def test_translation_failure_rows_warn_as_designed() -> None:
    clf = _classifier()
    feats, fixed = _feats_and_fixed(clf, label="translation_failure", split="heldout")
    assert feats, "heldout must contain translation-failure rows"
    for f in feats:
        status, reason = cal.verdict(f, fixed[f["id"]], 0.70, 0.10)
        assert status == "WARN"
        assert reason == "likely_untranslated_source"


def test_filename_mismatch_provenance_never_used() -> None:
    rows = _rows()
    # deterministic subset carries a filename token that conflicts with manifest language
    mismatched = [r for r in rows if not r["path"].split("/")[-1].startswith(r["language"])]
    assert mismatched, "corpus must contain filename-mismatch provenance rows"
    clean_mm = [r for r in mismatched if r["label"] == "clean"]
    assert clean_mm, "at least one clean row must have a misleading filename"
    clf = _classifier()
    feats, fixed = _feats_and_fixed(clf, label="clean")
    for f in feats:
        # clean rows classify PASS from the manifest claim, never the filename
        status, reason = cal.verdict(f, fixed[f["id"]], 0.70, 0.10)
        assert status == "PASS"
        assert reason == "expected_language"


def test_zero_malformed_or_short_pass_warn() -> None:
    clf = _classifier()
    feats, fixed = _feats_and_fixed(clf, label="malformed")
    feats_s, fixed_s = _feats_and_fixed(clf, label="short")
    feats = feats + feats_s
    fixed = {**fixed, **fixed_s}
    assert feats
    for f in feats:
        status, _ = cal.verdict(f, fixed[f["id"]], 0.70, 0.10)
        assert status == "INCONCLUSIVE"
