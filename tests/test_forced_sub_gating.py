"""Issue #79 — forced-only embedded-EN coverage gating on the subgen
`ignore_forced_subtitles` runtime capability.

A file whose ONLY English embedded subtitle is a *forced* track has only
partial coverage (forced subs cover foreign dialogue in an otherwise-English
film, not a full transcript). subgen will only FILL such a gap when its
runtime IGNORE_FORCED_SUBTITLES is on (reported via
GET /queue → capabilities.ignore_forced_subtitles == True). Otherwise subgen
STILL skips the file, so presenting it as a normal actionable gap is a lie.

Gating contract:
  - caps True  → forced-only EN stays an actionable partial-coverage gap.
  - caps False → forced-only EN becomes a DISTINCT non-actionable state
                 (forced_only_subgen_will_skip=True) with its own reason —
                 visibly different from a normal gap, NOT silently hidden.
  - full EN / EN(SDH) → satisfied regardless of caps.
  - a normal missing-sub gap → unaffected by caps.
"""

from __future__ import annotations


def _item(embedded_en=None):
    from subarr.coverage_engine import CoverageItem

    return CoverageItem(
        media_type="movie",
        title="A Foreign Film",
        canonical_path="Movies/A Foreign Film",
        embedded_en=embedded_en,
        audio_langs=["spa"],
    )


# ── new field exists + default ───────────────────────────────────────────────
def test_coverage_item_has_forced_skip_field_default_false():
    it = _item()
    assert it.forced_only_subgen_will_skip is False
    assert "forced_only_subgen_will_skip" in it.to_dict()
    assert it.to_dict()["forced_only_subgen_will_skip"] is False


# ── caps True → actionable partial-coverage gap (current behavior) ───────────
def test_forced_only_en_with_caps_on_is_actionable_gap():
    from subarr.coverage_engine import _score

    it = _item("EN(forced)")
    _score(it, {}, ignore_forced_subtitles=True)
    assert it.forced_only_subgen_will_skip is False
    # still scored as partial coverage (the existing −500 reason)
    assert any("partial coverage" in r for r in it.score_reasons)
    assert not any("subgen will skip" in r.lower() for r in it.score_reasons)


# ── caps False → distinct non-actionable state (NOT a normal gap) ────────────
def test_forced_only_en_with_caps_off_is_distinct_non_actionable():
    from subarr.coverage_engine import _score

    it = _item("EN(forced)")
    _score(it, {}, ignore_forced_subtitles=False)
    assert it.forced_only_subgen_will_skip is True
    # the row carries a clear, distinct reason mentioning subgen will skip +
    # the env knob to flip — so the UI can render it differently.
    joined = " ".join(it.score_reasons).lower()
    assert "subgen will skip" in joined
    assert "ignore_forced_subtitles" in joined
    # not silently hidden — the row is still scored/surfaced.
    assert it.embedded_en == "EN(forced)"


# ── default caps (omitted) behaves like caps-off — backward compatible ───────
def test_forced_only_en_defaults_to_caps_off():
    from subarr.coverage_engine import _score

    it = _item("EN(forced)")
    _score(it, {})  # no caps arg → default False
    assert it.forced_only_subgen_will_skip is True


# ── full EN / SDH satisfied regardless of caps ───────────────────────────────
def test_full_en_satisfied_regardless_of_caps():
    from subarr.coverage_engine import _score

    for caps in (True, False):
        it = _item("EN")
        _score(it, {}, ignore_forced_subtitles=caps)
        assert it.forced_only_subgen_will_skip is False
        assert any("Bazarr missed it" in r for r in it.score_reasons)


def test_sdh_satisfied_regardless_of_caps():
    from subarr.coverage_engine import _score

    for caps in (True, False):
        it = _item("EN(SDH)")
        _score(it, {}, ignore_forced_subtitles=caps)
        assert it.forced_only_subgen_will_skip is False


# ── normal missing-sub gap unaffected by caps ────────────────────────────────
def test_normal_missing_sub_gap_unaffected():
    from subarr.coverage_engine import _score

    for caps in (True, False):
        it = _item(None)  # no embedded EN at all → a normal gap
        _score(it, {}, ignore_forced_subtitles=caps)
        assert it.forced_only_subgen_will_skip is False
        assert not any("subgen will skip" in r.lower() for r in it.score_reasons)


# ── commentary track is NOT gated by the forced cap ──────────────────────────
def test_commentary_track_not_gated_by_forced_cap():
    from subarr.coverage_engine import _score

    # commentary is partial coverage but the cap only governs FORCED subs;
    # it must never be marked forced_only_subgen_will_skip.
    for caps in (True, False):
        it = _item("EN(commentary)")
        _score(it, {}, ignore_forced_subtitles=caps)
        assert it.forced_only_subgen_will_skip is False
        assert any("partial coverage" in r for r in it.score_reasons)


# ── caps parse: ignore_forced_subtitles read from /queue capabilities ────────
def test_subgen_caps_parses_ignore_forced_subtitles_default_false():
    from subarr.subgen_client import SubgenCapabilities

    caps = SubgenCapabilities.unreachable()
    assert caps.ignore_forced_subtitles is False
    d = caps.to_dict()
    assert d["ignore_forced_subtitles"] is False


def test_build_coverage_threads_caps_into_score():
    """build_coverage accepts subgen_caps and threads ignore_forced_subtitles
    into scoring (signature contract — full integration covered elsewhere)."""
    import inspect
    from subarr.coverage_engine import build_coverage

    sig = inspect.signature(build_coverage)
    assert "subgen_caps" in sig.parameters
