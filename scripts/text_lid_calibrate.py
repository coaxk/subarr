"""Calibrate the ``pr451-v1`` text-LID policy thresholds on a labeled corpus.

Consumes the manifest built by ``scripts/text_lid_calibration_gen.py`` and
selects a (threshold, margin) pair that maximizes *dev* clean recall subject to
false-warning <=5% and mixed abstention >=95%, then emits an un-refit heldout
report to ``results/heldout.json``. Exact JSON schema per DD-pr-451.

Run:
    python -m scripts.text_lid_calibrate \\
        --manifest artifacts/calibration/pr451-text-lid/manifest.jsonl \\
        --model-sha256 8c99809ff6de3d129e447306d30ceae4713735230dced7e8d4d46df89e6968ce \\
        --policy-version pr451-v1

The pipeline REUSES the checker's canonical region builder and aggregation
(``text_lid.extract_visible_cues`` / ``classification_regions`` / ``_aggregate`` /
``_is_mixed`` / ``_translation_failure_shape`` / ``_explicit_source_target_mismatch``)
and a REAL py3langid ``LanguageIdentifier`` built via
``from_pickled_model(path, norm_probs=True)`` restricted to the six languages.
It never reimplements region logic. Each fixture's regions are classified ONCE;
the threshold/margin sweep is pure arithmetic over the precomputed features.

Exit codes: 0 success; 1 a validation/availability error (bad --help exits 0 via
argparse; missing py3langid/model, hash mismatch, or no qualifying pair exit 1).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

from subarr import text_lid
from subarr.langs import normalize_lang
from subarr.text_lid import (
    INCONCLUSIVE,
    MIN_ALPHABETIC_CHARS,
    MIN_REGIONS,
    PASS,
    SUPPORTED_LANGUAGES,
    UNAVAILABLE,
    UNSUPPORTED,
    WARN,
    _aggregate,
    _argmax_lex,
    _explicit_source_target_mismatch,
    _is_mixed,
    _translation_failure_shape,
    classification_regions,
    extract_visible_cues,
)

SCHEMA_VERSION = "1.0.0"

# Sweep grid in 0.01 increments (basis points to avoid float drift).
THRESHOLD_MIN_BP, THRESHOLD_MAX_BP, THRESHOLD_STEP_BP = 30, 99, 1
MARGIN_MIN_BP, MARGIN_MAX_BP, MARGIN_STEP_BP = 1, 99, 1

# Acceptance thresholds (heldout).
HOLDOUT_CLEAN_RECALL = 0.95
HOLDOUT_FALSE_WARNING = 0.05
HOLDOUT_MIXED_ABSTENTION = 0.95


class CalibrationError(Exception):
    """Raised for unavailable backend, hash mismatch, or no qualifying pair."""


def load_manifest(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def manifest_sha256(path: str | Path) -> str:
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def _arange_bp(min_bp: int, max_bp: int, step_bp: int):
    """Yield thresholds in 0.01 increments as floats: [min_bp..max_bp]/100."""
    for bp in range(min_bp, max_bp + 1, step_bp):
        yield round(bp / 100, 2)


def resolve_model_path(model_sha256: str, model_path: str | None = None) -> Path:
    """Resolve a model file whose SHA-256 matches ``model_sha256``. Prefers an
    explicit path, then the checker's pinned cache path, then the py3langid
    bundled model. Raises CalibrationError when none matches (or py3langid is
    absent)."""
    if model_path is not None:
        cands = [Path(model_path)]
    else:
        cands = []
        if text_lid.model_target_path().is_file():
            cands.append(text_lid.model_target_path())
        # The bundled py3langid model is only a candidate when the extra is
        # installed; absence is resolved as a clear error below.
        if importlib.util.find_spec("py3langid") is not None:
            import py3langid

            bundled = Path(py3langid.__file__).parent / "data" / "model.plzma"
            cands.append(bundled)
    for cand in cands:
        if cand.is_file() and hashlib.sha256(cand.read_bytes()).hexdigest() == model_sha256:
            return cand
    raise CalibrationError(
        f"text-LID model unavailable: expected SHA-256 {model_sha256} but no matching model "
        "file was found (pip install '.[text-lid]' provides py3langid's bundled model, or "
        "point --model-path at a verified artifact)."
    )


def build_classifier(model_path: str | Path, model_sha256: str):
    """Return a real py3langid LanguageIdentifier restricted to the six
    languages, or raise CalibrationError on absence/failure."""
    try:
        from py3langid.langid import LanguageIdentifier
    except Exception as exc:
        raise CalibrationError(
            "py3langid is not importable — install the optional '[text-lid]' extra "
            "(pip install '.[text-lid]') before calibrating."
        ) from exc
    try:
        ident = LanguageIdentifier.from_pickled_model(str(model_path), norm_probs=True)
        ident.set_languages(sorted(SUPPORTED_LANGUAGES))
        return ident
    except Exception as exc:
        raise CalibrationError(f"failed to build the text-LID classifier: {exc}") from exc


def rank(classifier, text: str) -> dict[str, float] | None:
    """Normalized {language: probability} over the six languages for one region
    (mirrors text_lid.classify_text but accepts an injected classifier)."""
    try:
        ranked = classifier.rank(text)
    except Exception:  # noqa: BLE001 - inference failure -> skip region
        return None
    probs: dict[str, float] = {}
    for lang, p in ranked:
        norm = normalize_lang(lang)
        if norm in SUPPORTED_LANGUAGES:
            probs[norm] = float(p)
    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}
    return probs


def _expected(row: dict, task: str) -> set[str]:
    if task == "translate":
        t = normalize_lang(row.get("target_language"))
        return {t} if t else set()
    t = normalize_lang(row.get("target_language"))
    if t:
        return {t}
    s = normalize_lang(row.get("source_language"))
    return {s} if s else set()


def classify_rows(classifier, rows: list[dict], base_dir: str | Path) -> list[dict]:
    """Verify each fixture hash and compute its region features ONCE."""
    base = Path(base_dir)
    feats: list[dict] = []
    for row in rows:
        path = base / row["path"]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise CalibrationError(
                f"SHA-256 mismatch for {row['path']} (manifest {row['sha256']}); "
                "regenerate the corpus with scripts/text_lid_calibration_gen.py"
            )
        visible = extract_visible_cues(data)
        nonempty = [t for t in visible if t]
        distinct = classification_regions(nonempty)
        region_probs: list[tuple[str, dict[str, float]]] = []
        for name, rtext in distinct:
            p = rank(classifier, rtext)
            if p is not None:
                region_probs.append((name, p))
        agg = _aggregate(region_probs)
        task = (row.get("task") or "").strip().lower()
        feats.append(
            {
                "id": row["id"],
                "language": row["language"],
                "label": row["label"],
                "split": row["split"],
                "task": task,
                "source": row.get("source_language"),
                "target": row.get("target_language"),
                "nonempty": len(nonempty),
                "n_distinct": len(distinct),
                "alpha": sum(1 for ch in "".join(nonempty) if ch.isalpha()),
                "region_probs": region_probs,
                "agg": agg,
                "winner": _argmax_lex(agg),
                "expected": _expected(row, task),
            }
        )
    return feats


def precompute(feat: dict) -> tuple[str | None, str | None]:
    """Threshold-independent verdict branches -> (status, reason) or (None, None)
    when the verdict depends on (threshold, margin). Mirrors the pr451-v1
    precedence in check_subtitle_text."""
    if feat["nonempty"] == 0:
        return (INCONCLUSIVE, "malformed_or_empty")
    if feat["n_distinct"] < MIN_REGIONS:
        return (INCONCLUSIVE, "insufficient_regions")
    if feat["alpha"] < MIN_ALPHABETIC_CHARS:
        return (INCONCLUSIVE, "too_short")
    task = feat["task"]
    if task not in ("transcribe", "translate"):
        return (INCONCLUSIVE, "unknown_task_provenance")
    expected = feat["expected"]
    if not expected:
        return (INCONCLUSIVE, "unknown_language_provenance")
    if any(e not in SUPPORTED_LANGUAGES for e in expected):
        return (UNSUPPORTED, "unsupported_language")
    if not feat["region_probs"]:
        return (UNAVAILABLE, "inference_failed")
    if _is_mixed(feat["agg"], feat["region_probs"]):
        return (INCONCLUSIVE, "mixed_evidence")
    if _translation_failure_shape(feat["region_probs"], feat["source"], task):
        return (WARN, "likely_untranslated_source")
    if _explicit_source_target_mismatch(feat["winner"], feat["source"], feat["target"], task):
        return (WARN, "source_target_mismatch")
    return (None, None)


def verdict(feat: dict, fixed: tuple[str | None, str | None], threshold: float, margin: float):
    """Full verdict for one feature under a (threshold, margin) pair."""
    status, reason = fixed
    if status is not None:
        return status, reason
    agg = feat["agg"]
    expected = feat["expected"]
    p_expected = max(agg.get(e, 0.0) for e in expected)
    p_next = max((agg.get(l, 0.0) for l in SUPPORTED_LANGUAGES if l not in expected), default=0.0)
    if p_expected >= threshold and (p_expected - p_next) >= margin:
        return (PASS, "expected_language")
    return (WARN, "ordinary_mismatch")


def _by_category(feats: list[dict], fixed, threshold: float, margin: float) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for cat in ("mixed", "short", "hard_negative", "translation_failure"):
        label = "mismatch" if cat == "hard_negative" else cat
        rows = [f for f in feats if f["label"] == label]
        counts = {"count": len(rows), "pass": 0, "warn": 0, "inconclusive": 0}
        for f in rows:
            status, _ = verdict(f, fixed[f["id"]], threshold, margin)
            counts[
                {"PASS": "pass", "WARN": "warn", "INCONCLUSIVE": "inconclusive"}.get(status, "inconclusive")
            ] += 1
        out[cat] = counts
    return out


def _summary(feats: list[dict], fixed, threshold: float, margin: float) -> dict:
    """Dev metric triple used by the sweep: clean recall, false-warning rate,
    mixed abstention."""
    clean = [f for f in feats if f["label"] == "clean"]
    mixed = [f for f in feats if f["label"] == "mixed"]
    if not clean or not mixed:
        raise CalibrationError("dev split must contain both clean and mixed rows")
    clean_pass = 0
    clean_warn = 0
    for f in clean:
        status, _ = verdict(f, fixed[f["id"]], threshold, margin)
        clean_pass += status == PASS
        clean_warn += status == WARN
    mixed_abstain = 0
    for f in mixed:
        status, _ = verdict(f, fixed[f["id"]], threshold, margin)
        mixed_abstain += status == INCONCLUSIVE
    return {
        "recall": clean_pass / len(clean),
        "false_warning_rate": clean_warn / len(clean),
        "abstention": mixed_abstain / len(mixed),
    }


def select_params(train_feats: list[dict], dev_feats: list[dict]):
    """Sweep (threshold, margin) in 0.01 increments over TRAIN; among pairs whose
    DEV metrics meet false-warning <=5% and mixed abstention >=95%, pick the
    lexicographically first (threshold, margin) maximizing dev clean recall."""
    fixed = {f["id"]: precompute(f) for f in train_feats + dev_feats}
    best: tuple[float, float] | None = None
    best_recall = -1.0
    for threshold in _arange_bp(THRESHOLD_MIN_BP, THRESHOLD_MAX_BP, THRESHOLD_STEP_BP):
        for margin in _arange_bp(MARGIN_MIN_BP, MARGIN_MAX_BP, MARGIN_STEP_BP):
            dev = _summary(dev_feats, fixed, threshold, margin)
            if (
                dev["false_warning_rate"] <= HOLDOUT_FALSE_WARNING
                and dev["abstention"] >= HOLDOUT_MIXED_ABSTENTION
                and dev["recall"] > best_recall
            ):
                best_recall = dev["recall"]
                best = (threshold, margin)
    if best is None:
        raise CalibrationError(
            "no (threshold, margin) pair satisfied dev false-warning <=5% and mixed "
            "abstention >=95% — the corpus may be too hard/easy; see README."
        )
    return best, fixed


def calibrate(manifest_path, base_dir, model_sha256, policy_version, *, classifier=None, model_path=None):
    """Full calibration pipeline -> report dict. `classifier` injectable for tests."""
    rows = load_manifest(manifest_path)
    if classifier is None:
        mpath = resolve_model_path(model_sha256, model_path)
        classifier = build_classifier(mpath, model_sha256)
    train = [r for r in rows if r["split"] == "train"]
    dev = [r for r in rows if r["split"] == "dev"]
    heldout = [r for r in rows if r["split"] == "heldout"]
    if not (train and dev and heldout):
        raise CalibrationError("manifest must contain train, dev, and heldout rows")
    train_feats = classify_rows(classifier, train, base_dir)
    dev_feats = classify_rows(classifier, dev, base_dir)
    held_feats = classify_rows(classifier, heldout, base_dir)
    (threshold, margin), fixed = select_params(train_feats, dev_feats)
    held_fixed = {f["id"]: precompute(f) for f in held_feats}
    held_fixed.update(fixed)  # fixed is per-id, disjoint across splits
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": manifest_sha256(manifest_path),
        "model_sha256": model_sha256,
        "policy_version": policy_version,
        "threshold": threshold,
        "margin": margin,
        "metrics": {
            "overall": _summary(held_feats, held_fixed, threshold, margin),
            "by_language": {
                lang: _summary(
                    [f for f in held_feats if f["language"] == lang], held_fixed, threshold, margin
                )
                for lang in sorted(SUPPORTED_LANGUAGES)
            },
            "by_category": _by_category(held_feats, held_fixed, threshold, margin),
        },
    }


def _verify_acceptance(report: dict) -> None:
    o = report["metrics"]["overall"]
    ok = (
        o["recall"] >= HOLDOUT_CLEAN_RECALL
        and o["false_warning_rate"] <= HOLDOUT_FALSE_WARNING
        and o["abstention"] >= HOLDOUT_MIXED_ABSTENTION
    )
    short = report["metrics"]["by_category"]["short"]
    short_clean = short["pass"] == 0 and short["warn"] == 0
    if not ok or not short_clean:
        print(
            f"WARNING: heldout acceptance not met: recall={o['recall']:.3f} "
            f"fwr={o['false_warning_rate']:.3f} abstention={o['abstention']:.3f} "
            f"short pass/warn={short['pass']}/{short['warn']}",
            file=sys.stderr,
        )


def write_heldout(report: dict, out_path: str | Path) -> None:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.text_lid_calibrate",
        description="Calibrate the pr451-v1 text-LID thresholds and emit a heldout report.",
    )
    parser.add_argument("--manifest", required=True, help="path to manifest.jsonl")
    parser.add_argument(
        "--model-sha256",
        required=True,
        help="pinned model SHA-256 (must match the resolved model file)",
    )
    parser.add_argument("--model-path", default=None, help="optional explicit model file path")
    parser.add_argument("--policy-version", default="pr451-v1")
    parser.add_argument(
        "--out",
        default=None,
        help="output path for heldout.json (default <manifest dir>/results/heldout.json)",
    )
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    base_dir = manifest_path.parent
    out_path = Path(args.out) if args.out else base_dir / "results" / "heldout.json"

    try:
        report = calibrate(
            manifest_path,
            base_dir,
            args.model_sha256,
            args.policy_version,
            model_path=args.model_path,
        )
    except CalibrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _verify_acceptance(report)
    write_heldout(report, out_path)
    o = report["metrics"]["overall"]
    print(
        f"selected threshold={report['threshold']:.2f} margin={report['margin']:.2f} "
        f"| heldout recall={o['recall']:.3f} false_warning_rate={o['false_warning_rate']:.3f} "
        f"abstention={o['abstention']:.3f} | wrote {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
