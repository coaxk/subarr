"""#505: the auto-queue filter never consulted `forced_only_subgen_will_skip`.

The flag was already computed during scoring, serialised into the coverage
payload, rendered in the Coverage UI as `forced_skip`, and covered by
`test_forced_sub_gating.py` with a dozen assertions. Nothing consumed it where
it mattered: `auto_queue._filter_reason()` checked the IMAGE sibling
(`image_only_subgen_will_skip`) and not this one.

So a forced-only row was marked "subgen will skip this", shown as such, and then
queued anyway. subgen refused it, the next scheduled walk queued it again, and
the loop never ended because both sides were behaving as designed. The image
case had already been solved this exact way for #458; its comment describes the
symptom precisely: "Queueing it would turn a silent skip into a queue full of
instant rejections."

⚠️ The tests that existed asserted the FLAG and never asserted that anything
consumed it, which is why a dozen green assertions sat beside a dead guard.
These tests assert the consumption.
"""

from __future__ import annotations


def _item(embedded_en=None):
    from subarr.coverage_engine import CoverageItem

    return CoverageItem(
        media_type="episode",
        title="Some Show",
        canonical_path="TV/Some Show",
        episode_number="1x1",
        embedded_en=embedded_en,
        audio_langs=["eng"],
    )


def _rules(**kw):
    """Rules with the unrelated gates opened, so these tests exercise the
    subgen-will-refuse flags rather than the score/monitored gates. Defaults are
    min_score=200 and require_monitored=True, which reject a bare item before it
    ever reaches the flag checks."""
    from subarr.schedule_store import AutoQueueRules

    kw.setdefault("min_score", 0)
    kw.setdefault("require_monitored", False)
    return AutoQueueRules(**kw)


def test_auto_queue_skips_a_forced_only_row_subgen_would_refuse():
    """The bug. A forced-only row must not be queued when the connected subgen
    would refuse it."""
    from subarr.auto_queue import _filter_reason

    it = _item("EN(forced)")
    it.forced_only_subgen_will_skip = True
    reason = _filter_reason(it, _rules())
    assert reason is not None, "forced-only row was queued despite subgen refusing it"
    assert "forced" in reason.lower()


def test_auto_queue_allows_forced_only_once_subgen_can_fill_it():
    """The flag is set from subgen's RUNTIME capability, so turning on
    IGNORE_FORCED_SUBTITLES must make the row actionable again with no change
    here. Guards against fixing the loop by permanently excluding these files."""
    from subarr.auto_queue import _filter_reason

    it = _item("EN(forced)")
    it.forced_only_subgen_will_skip = False
    reason = _filter_reason(it, _rules())
    assert reason is None, f"forced row blocked even though subgen would fill it: {reason}"


def test_forced_and_image_are_independent_gates():
    """Neither flag may shadow the other: each must block on its own."""
    from subarr.auto_queue import _filter_reason

    forced = _item("EN(forced)")
    forced.forced_only_subgen_will_skip = True
    forced.image_only_subgen_will_skip = False
    assert "forced" in (_filter_reason(forced, _rules()) or "").lower()

    image = _item("EN(image)")
    image.forced_only_subgen_will_skip = False
    image.image_only_subgen_will_skip = True
    assert "image" in (_filter_reason(image, _rules()) or "").lower()


def test_full_english_still_skipped_by_the_ordinary_rule():
    """Unchanged: a real embedded English track is skipped by skip_embedded_en,
    which this fix must not disturb."""
    from subarr.auto_queue import _filter_reason

    it = _item("EN")
    reason = _filter_reason(it, _rules())
    assert reason is not None and "embedded english" in reason.lower()


def test_a_clean_row_is_still_queueable():
    """The filter must not have become a blanket refusal."""
    from subarr.auto_queue import _filter_reason

    it = _item(None)
    assert _filter_reason(it, _rules()) is None
