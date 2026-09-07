"""#499: aftercare must evaluate the sidecar SUBGEN wrote, not an arbitrary
sibling .srt.

The selector preferred `<stem>.en.srt` and otherwise took the first result of
`parent.glob(f"{stem}*.srt")`. Two problems:

  1. Subgen can be configured to embed its own name and the model into the
     filename (SHOW_IN_SUBNAME_SUBGEN / SHOW_IN_SUBNAME_MODEL), producing
     `<stem>.subgen.large-v3.eng.srt`. That does not match `<stem>.en.srt`, so
     selection fell through to the generic fallback.
  2. The fallback is glob order, which is filesystem-dependent. With any other
     sibling .srt present, which file got evaluated was effectively arbitrary.

Reported by IdahoRC, who measured it: the current subgen output had 1,317 cues
while aftercare had scored a different 763-cue file.
"""

from __future__ import annotations

STEM = "Movie (2024)"


def _choose(names):
    from subarr.completion_watcher import choose_srt_sidecar

    return choose_srt_sidecar(STEM, names)


def test_exact_en_srt_still_wins():
    """Unchanged for the common case, which is what most installs produce."""
    assert _choose([f"{STEM}.en.srt", f"{STEM}.subgen.large-v3.eng.srt"]) == f"{STEM}.en.srt"


def test_subgen_english_sidecar_beats_an_unrelated_sibling():
    """The reported case: no plain .en.srt, and a stale sibling sorts first."""
    names = [f"{STEM}.aaa-stale.srt", f"{STEM}.subgen.large-v3.eng.srt"]
    assert _choose(names) == f"{STEM}.subgen.large-v3.eng.srt"


def test_subgen_english_preferred_over_subgen_other_language():
    """A subgen sidecar in another language must not be scored as the English
    output just because it carries the subgen marker."""
    names = [f"{STEM}.subgen.large-v3.fre.srt", f"{STEM}.subgen.large-v3.eng.srt"]
    assert _choose(names) == f"{STEM}.subgen.large-v3.eng.srt"


def test_two_letter_en_tag_also_recognised():
    """Subgen can emit either ISO form depending on SUBTITLE_LANGUAGE_NAMING_TYPE."""
    names = [f"{STEM}.zzz.srt", f"{STEM}.subgen.large-v3.en.srt"]
    assert _choose(names) == f"{STEM}.subgen.large-v3.en.srt"


def test_subgen_marker_without_language_still_beats_a_stranger():
    """Better a subgen sidecar of unknown language than an unrelated file."""
    names = [f"{STEM}.aaa.srt", f"{STEM}.subgen.srt"]
    assert _choose(names) == f"{STEM}.subgen.srt"


def test_fallback_is_deterministic_not_filesystem_order():
    """With nothing distinguishable, the choice must not depend on the order the
    filesystem happened to return. Same list, two orders, one answer."""
    a = [f"{STEM}.zzz.srt", f"{STEM}.aaa.srt", f"{STEM}.mmm.srt"]
    assert _choose(a) == _choose(list(reversed(a)))
    assert _choose(a) == f"{STEM}.aaa.srt"


def test_unrelated_stems_are_ignored():
    """Only siblings sharing this video's stem are candidates."""
    names = ["Another Movie.en.srt", f"{STEM}.subgen.large-v3.eng.srt"]
    assert _choose(names) == f"{STEM}.subgen.large-v3.eng.srt"


def test_no_candidates_returns_none():
    assert _choose([]) is None
    assert _choose(["Something Else.srt"]) is None


def test_forced_sidecar_is_not_chosen_as_the_full_transcript():
    """subarr writes .forced sidecars itself (#364). A forced segment file is a
    handful of cues and must never be scored as the full transcript."""
    names = [f"{STEM}.en.forced.srt", f"{STEM}.subgen.large-v3.eng.srt"]
    assert _choose(names) == f"{STEM}.subgen.large-v3.eng.srt"
