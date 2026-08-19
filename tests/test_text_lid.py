"""#451 Phase 3 — pure bounded text-LID policy and optional backend.

Tests the pure extraction/sampling, deterministic regions, pr451-v1 policy
precedence, canonical cache identity/keying, and the optional py3langid backend.
A fake/stub classifier is injected via monkeypatch so py3langid need not be
installed; real-backend assertions use `pytest.importorskip` (test_lid.py style).
"""

import hashlib
import json
from pathlib import Path

import pytest
from subarr import text_lid

MAX = text_lid.MAX_BYTES


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _srt(*texts: str) -> bytes:
    """Build a minimal SRT from cue texts (sanitizer input -> Cue.text)."""
    blocks = []
    for i, t in enumerate(texts, 1):
        ms = i * 2000
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, mss = divmod(rem, 1000)
        blocks.append(
            f"{i}\n{h:02d}:{m:02d}:{s:02d},{mss:03d} --> {h:02d}:{m:02d}:{s:02d},{mss + 1999:03d}\n{t}"
        )
    return ("\n\n".join(blocks) + "\n").encode("utf-8")


def _english_cues(n: int = 24) -> list[str]:
    return [
        f"Subtitle line {i}: the quick brown fox jumps over the lazy dog near the river." for i in range(n)
    ]


class FakeClassifier:
    def __init__(self, ranking):
        self.ranking = ranking  # list[(lang, prob)]

    def rank(self, text):
        return list(self.ranking)


def _install(monkeypatch, dist: dict) -> None:
    """Inject a fake classifier returning `dist` (normalized) for every region."""
    ranking = sorted(dist.items(), key=lambda kv: -kv[1])
    monkeypatch.setattr(text_lid, "get_classifier", lambda: FakeClassifier(ranking))


def _default_kw() -> dict:
    ident = text_lid.canonical_subtitle_identity("/media/TV/Show.mkv", "TV/Show/Show.en.srt", "en", 1)
    return dict(
        canonical_identity=ident,
        content_sha256="abc123",
        expected_languages=["en"],
        task="transcribe",
        source_language="en",
        target_language="en",
    )


def _run(*texts: str, **kw) -> text_lid.TextLanguageResult:
    base = _default_kw()
    base.update(kw)
    return text_lid.check_subtitle_text(_srt(*texts), **base)


# ---------------------------------------------------------------------------
# P3-S1 — extraction and byte bounds
# ---------------------------------------------------------------------------


def test_extract_keeps_all_cues_when_under_budget():
    data = _srt("Hello one", "Hello two", "Hello three")
    assert len(data) <= MAX
    assert text_lid.extract_visible_cues(data) == ["Hello one", "Hello two", "Hello three"]


def test_extract_sanitizes_markup_only_visible_text():
    data = _srt("<b>Hello</b> {\\an8} world", "{\\pos(0,0)}second")
    assert text_lid.extract_visible_cues(data) == ["Hello world", "second"]


def test_extract_drops_final_cue_when_byte_boundary_cuts_it():
    base = _srt("A" * 200)
    pad = MAX - len(base) + 40  # exceed the byte budget mid-way through cue 2
    data = base + ("\n\n2\n00:00:02,000 --> 00:00:04,000\n" + "CUTSENTINEL" + "x" * pad).encode()
    assert len(data) > MAX
    visible = text_lid.extract_visible_cues(data)
    assert visible == ["A" * 200]  # the cut cue 2 is discarded
    assert all("CUTSENTINEL" not in v for v in visible)


def test_extract_decodes_partial_multibyte_without_raising():
    # A multi-byte char split at the boundary decodes via errors='replace', and
    # the trailing cut cue is still dropped — never raises.
    data = _srt("café résumé") + "日本語".encode("utf-8") * 20000
    visible = text_lid.extract_visible_cues(data)
    assert isinstance(visible, list)


# ---------------------------------------------------------------------------
# P3-S2 — deterministic regions and budgets
# ---------------------------------------------------------------------------


def test_region_budgets_are_exact():
    cues = ["w" * 5000 for _ in range(30)]
    begin, middle, end = text_lid.build_regions(cues)
    assert (len(begin), len(middle), len(end)) == (10922, 10922, 10924)


def test_classification_regions_stable_order_and_max_four():
    cues = [f"sentence {i} with some words" for i in range(12)]
    r1 = text_lid.classification_regions(cues)
    r2 = text_lid.classification_regions(cues)
    assert r1 == r2  # deterministic
    names = [n for n, _ in r1]
    order = {"full": 0, "begin": 1, "middle": 2, "end": 3}
    assert names == sorted(names, key=order.__getitem__)
    assert len(r1) <= 4


def test_classification_regions_dedupes_identical_text():
    cues = ["same"] * 12  # begin/middle/end collapse to one distinct text
    regions = text_lid.classification_regions(cues)
    assert len(regions) < 4
    # order still stable
    order = {"full": 0, "begin": 1, "middle": 2, "end": 3}
    assert [n for n, _ in regions] == sorted([n for n, _ in regions], key=order.__getitem__)


def test_one_or_two_regions_never_pass(monkeypatch):
    _install(monkeypatch, {"en": 0.99, "de": 0.01})  # strongest possible signal
    # 3 identical cues -> only 2 distinct regions -> cannot PASS/WARN
    data = _srt("Hello there everyone", "Hello there everyone", "Hello there everyone")
    r = text_lid.check_subtitle_text(data, **_default_kw())
    assert r.status == text_lid.INCONCLUSIVE
    assert r.reason == "insufficient_regions"


# ---------------------------------------------------------------------------
# P3-S3 — backend availability / pull / checksum
# ---------------------------------------------------------------------------


def test_runtime_present_false_without_extra(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "py3langid", None)  # force absent extra
    assert text_lid.runtime_present() is False


def test_base_import_stays_ml_free():
    """#451 P6 — importing subarr (incl. text_lid) must NOT load py3langid/numpy.

    The base install is ML-free by contract (checker/backend and [text-lid] extra
    are optional). Loading text_lid at module scope must only pull the lazy
    imports inside runtime_present()/get_classifier() on call, never at import.
    Guards against an accidental top-level ``import py3langid`` / ``import numpy``.
    """
    import sys
    import subprocess

    script = (
        "import sys; import subarr; import subarr.text_lid as t; "
        "ml=[m for m in sys.modules if m=='py3langid' or m.startswith('numpy')]; "
        "assert not ml, ('ml leaked into base import: %r' % ml); "
        "print('ML-OK', t.runtime_present())"
    )
    # Run in a fresh interpreter so baseline modules reflect a cold base install.
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ML-OK" in out.stdout


def test_text_lid_extra_separate_and_base_ml_free_packaging():
    """#451 P6 — packaging contract.

    Asserts all three P6-S4 invariants from source of truth:
      1. `[text-lid]` is a DISTINCT optional extra from the audio `[lid]` extra in
         pyproject.toml, pinning py3langid==0.3.0 + numpy>=2,<3.
      2. The base (non-optional) dependency list and `[lid]`/`[text-lid]` extras do
         not smuggle py3langid into the base install — py3langid only ever appears
         under the `[text-lid]` extra.
      3. The Dockerfile's pin installs `.[vad,qe-onnx,lid,text-lid]` explicitly
         (bakes the text-LID runtime separately, distinct from the audio [lid]).
    """
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    with open(root / "pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)
    extras = pyproject["project"]["optional-dependencies"]

    # (1) distinct extras: [lid] (audio onnx) vs [text-lid] (text NB classifier)
    assert "lid" in extras and "text-lid" in extras
    assert set(extras["lid"]) != set(extras["text-lid"])
    assert "py3langid==0.3.0" in extras["text-lid"]
    assert any(d.startswith("numpy") for d in extras["text-lid"])

    # (2) base deps stay ML-free; py3langid only under [text-lid]
    base = pyproject["project"]["dependencies"]
    assert all(not d.startswith("py3langid") for d in base)
    for name, reqs in extras.items():
        has_py3langid = any(d.startswith("py3langid") for d in reqs)
        assert has_py3langid == (name == "text-lid"), f"py3langid misplaced in [{name}]"

    # (3) Dockerfile pins the text-lid runtime explicitly
    dockerfile = (root / "Dockerfile").read_text()
    assert ".[vad,qe-onnx,lid,text-lid]" in dockerfile


def test_get_classifier_none_when_model_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_MODEL_CACHE", str(tmp_path))
    # #451 P7: lazy acquisition is ATTEMPTED on the first-use path (no boot-time
    # fetch). With the fetcher offline, nothing usable is cached and the
    # classifier degrades to None -> UNAVAILABLE.
    monkeypatch.setattr(text_lid, "_default_fetch", lambda url: (_ for _ in ()).throw(OSError("offline")))
    text_lid._cached_classifier = None
    assert text_lid.get_classifier() is None
    assert not text_lid.model_target_path().exists()
    text_lid._cached_classifier = None


def test_pull_model_verifies_checksum_and_never_persists_bad(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_MODEL_CACHE", str(tmp_path))
    good = b"fake-model-bytes"
    monkeypatch.setattr(text_lid, "MODEL_SHA256", hashlib.sha256(good).hexdigest())
    res = text_lid.pull_model(_fetch=lambda url: good)
    assert res["status"] == "downloaded"
    monkeypatch.setattr(text_lid, "MODEL_SHA256", "0" * 64)
    with pytest.raises(ValueError):
        text_lid.pull_model(_fetch=lambda url: good)
    assert not text_lid.model_target_path().exists()


def test_pull_model_atomic_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBARR_MODEL_CACHE", str(tmp_path))
    good = b"model-bytes"
    monkeypatch.setattr(text_lid, "MODEL_SHA256", hashlib.sha256(good).hexdigest())
    text_lid.pull_model(_fetch=lambda url: good)
    target = text_lid.model_target_path()
    assert target.read_bytes() == good
    # second pull sees present+verified file; no fetch needed
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        return good

    text_lid.pull_model(_fetch=fetch)
    assert calls["n"] == 0


def test_model_target_path_is_sha256_named():
    assert text_lid.model_target_path().name == f"{text_lid.MODEL_SHA256}.pickle"


# ---------------------------------------------------------------------------
# P3-S4 — structured result shape
# ---------------------------------------------------------------------------


def test_result_has_exact_fields_and_no_confidence(monkeypatch):
    _install(monkeypatch, {"en": 0.85, "de": 0.15})
    r = text_lid.check_subtitle_text(_srt(*_english_cues()), **_default_kw())
    d = r.to_dict()
    assert set(d) == {
        "status",
        "languages",
        "probabilities",
        "evidence",
        "reason",
        "provenance",
        "checker_version",
        "policy_version",
    }
    assert "confidence" not in json.dumps(d)
    assert r.status in {
        text_lid.PASS,
        text_lid.WARN,
        text_lid.INCONCLUSIVE,
        text_lid.UNSUPPORTED,
        text_lid.UNAVAILABLE,
    }


# ---------------------------------------------------------------------------
# P3-S5 — policy precedence
# ---------------------------------------------------------------------------


def test_pass_when_expected_wins_threshold_and_margin(monkeypatch):
    _install(monkeypatch, {"en": 0.85, "de": 0.15})
    r = _run(*_english_cues())
    assert r.status == text_lid.PASS
    assert r.reason == "expected_language"
    assert r.probabilities["en"] == pytest.approx(0.85)
    assert r.probabilities["de"] == pytest.approx(0.15)


def test_warn_when_threshold_fails(monkeypatch):
    _install(monkeypatch, {"en": 0.65, "de": 0.20, "es": 0.15})
    r = _run(*_english_cues())
    assert r.status == text_lid.WARN
    assert r.reason == "ordinary_mismatch"


def test_inconclusive_on_exact_tie(monkeypatch):
    _install(monkeypatch, {"en": 0.40, "de": 0.40, "es": 0.20})
    r = _run(*_english_cues())
    assert r.status == text_lid.INCONCLUSIVE
    assert r.reason == "mixed_evidence"


def test_inconclusive_on_two_languages_in_two_regions(monkeypatch):
    # Not a tie, but two languages each >=0.30 in (all) regions -> mixed.
    _install(monkeypatch, {"en": 0.38, "de": 0.35, "es": 0.27})
    r = _run(*_english_cues())
    assert r.status == text_lid.INCONCLUSIVE
    assert r.reason == "mixed_evidence"


def test_translation_failure_shaped_warn(monkeypatch):
    # source (de) dominates but en < 0.30 so this is NOT mixed evidence.
    _install(monkeypatch, {"de": 0.55, "en": 0.25, "es": 0.20})
    r = text_lid.check_subtitle_text(
        _srt(*_english_cues()),
        **dict(_default_kw(), task="translate", source_language="de", target_language="en"),
    )
    assert r.status == text_lid.WARN
    assert r.reason == "likely_untranslated_source"


def test_ordinary_mismatch_not_shaped_as_translation_failure(monkeypatch):
    # A normal low-confidence mismatch (Spanish output for de->en translate) must
    # NOT be shaped as translation failure.
    _install(monkeypatch, {"es": 0.80, "en": 0.10, "de": 0.10})
    r = text_lid.check_subtitle_text(
        _srt(*_english_cues()),
        **dict(_default_kw(), task="translate", source_language="de", target_language="en"),
    )
    assert r.status == text_lid.WARN
    assert r.reason == "ordinary_mismatch"


def test_explicit_source_target_mismatch_warn(monkeypatch):
    # winner == source but too weak to trigger translation-failure shape.
    _install(monkeypatch, {"de": 0.30, "en": 0.25, "es": 0.25, "fr": 0.20})
    r = text_lid.check_subtitle_text(
        _srt(*_english_cues()),
        **dict(_default_kw(), task="translate", source_language="de", target_language="en"),
    )
    assert r.status == text_lid.WARN
    assert r.reason == "source_target_mismatch"


def test_inconclusive_when_malformed_or_empty():
    r = text_lid.check_subtitle_text(b"", **_default_kw())
    assert r.status == text_lid.INCONCLUSIVE
    assert r.reason == "malformed_or_empty"


def test_inconclusive_when_markup_only(monkeypatch):
    _install(monkeypatch, {"en": 0.99, "de": 0.01})
    data = _srt("{\\an8}", "<b></b>", "{\\pos(0,0)}")
    r = text_lid.check_subtitle_text(data, **_default_kw())
    assert r.status == text_lid.INCONCLUSIVE
    assert r.reason == "malformed_or_empty"


def test_inconclusive_when_too_short(monkeypatch):
    _install(monkeypatch, {"en": 0.99, "de": 0.01})
    r = _run("a", "b", "c")  # 4 distinct regions, < 80 alphabetic chars
    assert r.status == text_lid.INCONCLUSIVE
    assert r.reason == "too_short"


def test_inconclusive_unknown_task_provenance(monkeypatch):
    _install(monkeypatch, {"en": 0.85, "de": 0.15})
    r = text_lid.check_subtitle_text(_srt(*_english_cues()), **dict(_default_kw(), task=None))
    assert r.status == text_lid.INCONCLUSIVE
    assert r.reason == "unknown_task_provenance"


def test_inconclusive_unknown_language_provenance(monkeypatch):
    _install(monkeypatch, {"en": 0.85, "de": 0.15})
    r = text_lid.check_subtitle_text(
        _srt(*_english_cues()),
        **dict(
            _default_kw(),
            task="translate",
            expected_languages=[],
            target_language=None,
        ),
    )
    assert r.status == text_lid.INCONCLUSIVE
    assert r.reason == "unknown_language_provenance"


def test_unsupported_known_language_outside_six(monkeypatch):
    _install(monkeypatch, {"ko": 1.0})
    r = text_lid.check_subtitle_text(
        _srt(*_english_cues()),
        **dict(_default_kw(), expected_languages=["ko"], source_language="ko", target_language="ko"),
    )
    assert r.status == text_lid.UNSUPPORTED
    assert r.reason == "unsupported_language"


def test_transcribe_uses_declared_contract_not_source_as_output(monkeypatch):
    # Transcribing ko audio into English: expected is the declared contract
    # (en), source ko must NOT be treated as output.
    _install(monkeypatch, {"en": 0.85, "de": 0.15})
    r = text_lid.check_subtitle_text(
        _srt(*_english_cues()),
        **dict(_default_kw(), expected_languages=["en"], source_language="ko"),
    )
    assert r.status == text_lid.PASS  # en is the expected winner


# ---------------------------------------------------------------------------
# P3-S3 — backend error paths -> UNAVAILABLE
# ---------------------------------------------------------------------------


def test_unavailable_when_classifier_missing(monkeypatch):
    monkeypatch.setattr(text_lid, "get_classifier", lambda: None)
    r = text_lid.check_subtitle_text(_srt(*_english_cues()), **_default_kw())
    assert r.status == text_lid.UNAVAILABLE
    assert r.reason == "backend_unavailable"


def test_unavailable_on_inference_error(monkeypatch):
    class Boom:
        def rank(self, text):
            raise RuntimeError("inference exploded")

    monkeypatch.setattr(text_lid, "get_classifier", lambda: Boom())
    r = text_lid.check_subtitle_text(_srt(*_english_cues()), **_default_kw())
    assert r.status == text_lid.UNAVAILABLE
    assert r.reason == "inference_failed"


def test_unavailable_on_timeout(monkeypatch):
    class Timeout:
        def rank(self, text):
            raise TimeoutError("classify took too long")

    monkeypatch.setattr(text_lid, "get_classifier", lambda: Timeout())
    r = text_lid.check_subtitle_text(_srt(*_english_cues()), **_default_kw())
    assert r.status == text_lid.UNAVAILABLE
    assert r.reason == "inference_failed"


def test_unavailable_reaches_only_with_regions(monkeypatch):
    # Empty text is INCONCLUSIVE before the backend is consulted.
    monkeypatch.setattr(text_lid, "get_classifier", lambda: None)
    r = text_lid.check_subtitle_text(b"", **_default_kw())
    assert r.status == text_lid.INCONCLUSIVE


# ---------------------------------------------------------------------------
# P3-S6 — canonical identity and cache keying
# ---------------------------------------------------------------------------


def test_canonical_identity_normalizes_and_excludes_webhook():
    ident = text_lid.canonical_subtitle_identity("TV\\Show\\file.mkv", "TV/Show/./ep.srt", "English", 42)
    assert ident == {
        "video_path": "TV/Show/file.mkv",
        "subtitle_path": "TV/Show/ep.srt",
        "subtitle_language": "en",
        "ledger_id": 42,
    }
    assert "webhook" not in ident


def test_cache_key_stable_and_invalidation():
    ident = text_lid.canonical_subtitle_identity("/media/TV/S.mkv", "TV/S/S01E01.en.srt", "en", 7)
    kw = dict(
        canonical_identity=ident,
        content_sha256="abc123",
        expected_languages=["en"],
        task="translate",
        source_language="de",
        target_language="en",
        submission_origin="manual",
        webhook_event="translated",
        webhook_language="en",
        webhook_subtitle="locator",
        provenance_conflict=True,
    )
    k1 = text_lid.cache_key(**kw)
    assert k1 == text_lid.cache_key(**kw)
    assert len(k1) == 64

    # subtitle_path change -> different key (invalidation after retime/replace)
    ident2 = dict(ident)
    ident2["subtitle_path"] = "TV/S/S01E01.en.2.srt"
    kw2 = dict(kw, canonical_identity=ident2)
    assert text_lid.cache_key(**kw2) != k1

    # content_sha256 change -> different key (invalidation after upload/replace)
    kw3 = dict(kw, content_sha256="different")
    assert text_lid.cache_key(**kw3) != k1


def test_cache_key_includes_webhook_as_provenance_input():
    ident = text_lid.canonical_subtitle_identity("/m.mkv", "m.srt", "en", 3)
    base = dict(
        canonical_identity=ident,
        content_sha256="c",
        expected_languages=["en"],
        task="translate",
        source_language="de",
        target_language="en",
    )
    a = text_lid.cache_key(
        submission_origin=None,
        webhook_event=None,
        webhook_language=None,
        webhook_subtitle=None,
        provenance_conflict=None,
        **base,
    )
    b = text_lid.cache_key(
        submission_origin="manual",
        webhook_event="translated",
        webhook_language="en",
        webhook_subtitle="loc",
        provenance_conflict=True,
        **base,
    )
    assert a != b  # webhook evidence participates in the key


def test_cache_key_matches_exact_serialization():
    ident = text_lid.canonical_subtitle_identity("/v.mkv", "/s.srt", "en", 5)
    expected = json.dumps(
        [
            ident["ledger_id"],
            ident["video_path"],
            ident["subtitle_path"],
            ident["subtitle_language"],
            "sha",
            ["en"],
            {
                "task": "transcribe",
                "source": None,
                "target": None,
                "origin": "manual",
                "webhook_event": None,
                "webhook_language": None,
                "webhook_subtitle": None,
                "conflict": None,
            },
            "py3langid==0.3.0",
            text_lid.MODEL_SHA256,
            "pr451-v1",
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    key = text_lid.cache_key(
        canonical_identity=ident,
        content_sha256="sha",
        expected_languages=["en"],
        task="transcribe",
        submission_origin="manual",
    )
    assert key == hashlib.sha256(expected).hexdigest()


# ---------------------------------------------------------------------------
# optional backend real-path (skipped on base CI; test_lid.py pattern)
# ---------------------------------------------------------------------------


def test_real_classify_text_uses_rank(monkeypatch):
    pytest.importorskip("py3langid")
    from py3langid.langid import MODEL_FILE, LanguageIdentifier

    ident = LanguageIdentifier.from_pickled_model(MODEL_FILE, norm_probs=True)
    ident.set_languages(["de", "en", "es", "fr", "it", "pt"])
    monkeypatch.setattr(text_lid, "get_classifier", lambda: ident)
    probs = text_lid.classify_text(
        "This is an English sentence about the quick fox and the lazy dog near the river bank."
    )
    assert probs is not None
    assert all(k in text_lid.SUPPORTED_LANGUAGES for k in probs)
    assert abs(sum(probs.values()) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# P7 — single-path lazy acquisition on first backend use (absent cache)
# ---------------------------------------------------------------------------
# P7 closes the QA gap that pull_model() had no call site: get_classifier()
# now attempts acquisition on the guarded first-use path. The real pinned model
# (py3langid's bundled model.pklzma, whose sha256 == MODEL_SHA256 by contract) is
# used as the deterministic fetch payload; tests compute its sha256 at runtime
# rather than hardcoding the model hash.


def _bundled_model() -> bytes:
    """Bytes of the pinned py3langid bundled model (the canonical artifact of
    the pinned `[text-lid]` extra). Skips when the optional extra is absent."""
    pytest.importorskip("py3langid")
    from py3langid import langid as _langid

    # MODEL_FILE is relative to the py3langid package (e.g. 'data/model.plzma').
    return (Path(_langid.__file__).parent / _langid.MODEL_FILE).read_bytes()


def test_lazy_acquire_verified_payload_is_cached_then_classifies(tmp_path, monkeypatch):
    """Absent cache: acquisition is attempted, the checksum-valid payload is
    atomically cached, and classification then succeeds (first call does one
    fetch, no more)."""
    payload = _bundled_model()
    monkeypatch.setenv("SUBARR_MODEL_CACHE", str(tmp_path))
    monkeypatch.setattr(text_lid, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        return payload

    monkeypatch.setattr(text_lid, "_default_fetch", fetch)
    text_lid._cached_classifier = None
    try:
        r = text_lid.check_subtitle_text(_srt(*_english_cues()), **_default_kw())
        assert r.status == text_lid.PASS
        # checksum-valid payload atomically placed at the sha-named cache path
        assert text_lid.model_target_path().is_file()
        assert text_lid.model_target_path().read_bytes() == payload
        assert calls["n"] == 1  # one fetch, then cached
    finally:
        text_lid._cached_classifier = None


def test_lazy_acquire_download_failure_unavailable_no_cache(tmp_path, monkeypatch):
    """Download failure leaves no usable cache and produces UNAVAILABLE. Passes
    with or without the [text-lid] extra installed (offline fetch -> None)."""
    monkeypatch.setenv("SUBARR_MODEL_CACHE", str(tmp_path))
    monkeypatch.setattr(
        text_lid, "_default_fetch", lambda url: (_ for _ in ()).throw(OSError("network down"))
    )
    text_lid._cached_classifier = None
    try:
        r = text_lid.check_subtitle_text(_srt(*_english_cues()), **_default_kw())
        assert r.status == text_lid.UNAVAILABLE
        assert r.reason == "backend_unavailable"
        assert not text_lid.model_target_path().exists()
    finally:
        text_lid._cached_classifier = None


def test_lazy_acquire_checksum_mismatch_unavailable_no_cache(tmp_path, monkeypatch):
    """Checksum mismatch never persists a usable cache and yields UNAVAILABLE."""
    monkeypatch.setenv("SUBARR_MODEL_CACHE", str(tmp_path))
    monkeypatch.setattr(text_lid, "MODEL_SHA256", "0" * 64)
    monkeypatch.setattr(text_lid, "_default_fetch", lambda url: b"wrong-model-bytes")
    text_lid._cached_classifier = None
    try:
        r = text_lid.check_subtitle_text(_srt(*_english_cues()), **_default_kw())
        assert r.status == text_lid.UNAVAILABLE
        assert r.reason == "backend_unavailable"
        assert not text_lid.model_target_path().exists()
    finally:
        text_lid._cached_classifier = None


def test_valid_cache_classifies_without_invoking_fetcher(tmp_path, monkeypatch):
    """A verified cache present yields classification WITHOUT invoking the
    fetcher (pull_model short-circuits on a present, sha-valid artifact)."""
    payload = _bundled_model()
    monkeypatch.setenv("SUBARR_MODEL_CACHE", str(tmp_path))
    monkeypatch.setattr(text_lid, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    target = text_lid.model_target_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)  # pre-place the verified artifact
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        raise AssertionError("fetcher must not run when the verified cache is present")

    monkeypatch.setattr(text_lid, "_default_fetch", fetch)
    text_lid._cached_classifier = None
    try:
        r = text_lid.check_subtitle_text(_srt(*_english_cues()), **_default_kw())
        assert r.status == text_lid.PASS
        assert calls["n"] == 0  # fetcher never invoked
    finally:
        text_lid._cached_classifier = None
