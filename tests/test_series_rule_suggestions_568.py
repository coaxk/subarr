"""#568: suggest a series rule when several per-episode verdicts in one show agree.

A series rule (#226) covers future and replaced files with no per-file work, and
it needs none of the SxxExx matching #563's carry-over depends on. On 2026-09-18,
119 of one library's 427 per-episode verdicts pointed at files that were already
gone, while whole shows (Alerts 53, Devil's Throat 12) held dozens of agreeing
verdicts that one rule would have covered.

A show is only suggested when its verdicts AGREE. A show whose verdicts disagree
is a mixed-language show (#140) where the per-episode verdicts are the right
answer, and it must never be suggested.
"""

from __future__ import annotations

import pytest

SHOW = "TV/Alerts"
MIN = 3  # the shipped threshold


@pytest.fixture
def store(subarr_env, tmp_path):
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "al.db"
    run_migrations(db)
    return AudioLangStore(db)


def _episodes(store, show=SHOW, lang="fr", n=3, season=1, start=1):
    for i in range(start, start + n):
        store.upsert(
            canonical_path=f"{show}/Season {season}/{show.split('/')[-1]} - S0{season}E{i:02d}.mkv",
            lang_code=lang,
        )


def _prefixes(suggestions):
    return [s["series_prefix"] for s in suggestions]


# ── the threshold ────────────────────────────────────────────────────


def test_three_agreeing_verdicts_suggest_the_show(store):
    _episodes(store, n=3)
    (s,) = store.suggest_series_rules()
    assert s["series_prefix"] == SHOW + "/"
    assert s["lang_code"] == "fr"
    assert s["agreeing"] == 3


def test_two_agreeing_verdicts_are_below_the_threshold(store):
    _episodes(store, n=2)
    assert store.suggest_series_rules() == []


def test_the_threshold_is_a_parameter(store):
    _episodes(store, n=2)
    assert _prefixes(store.suggest_series_rules(min_agreeing=2)) == [SHOW + "/"]


def test_the_default_threshold_is_three(store):
    import inspect

    sig = inspect.signature(store.suggest_series_rules)
    assert sig.parameters["min_agreeing"].default == MIN


# ── agreement ────────────────────────────────────────────────────────


def test_one_disagreeing_verdict_silences_the_whole_show(store):
    _episodes(store, n=4)
    store.upsert(canonical_path=f"{SHOW}/Season 1/Alerts - S01E09.mkv", lang_code="nl")
    assert store.suggest_series_rules() == []


def test_a_multilingual_verdict_silences_the_show(store):
    _episodes(store, n=3)
    store.upsert(
        canonical_path=f"{SHOW}/Season 1/Alerts - S01E09.mkv",
        lang_code="fr",
        lang_class="multi",
        lang_codes=["fr", "nl"],
    )
    assert store.suggest_series_rules() == []


def test_verdicts_across_seasons_count_as_one_show(store):
    _episodes(store, n=2, season=1)
    _episodes(store, n=1, season=2)
    (s,) = store.suggest_series_rules()
    assert s["agreeing"] == 3


def test_two_shows_are_counted_separately(store):
    _episodes(store, show="TV/Alerts", lang="fr", n=3)
    _episodes(store, show="TV/Spin", lang="fr", n=2)
    assert _prefixes(store.suggest_series_rules()) == ["TV/Alerts/"]


def test_one_verdict_per_movie_folder_is_never_enough(store):
    """Each film is its own folder, so four films are four folders of one — the
    threshold alone keeps them out. (A folder holding several agreeing files,
    e.g. two editions of one film, IS offered: a rule there is correct.)"""
    for i in range(4):
        store.upsert(canonical_path=f"Movies/Film {i} (2019)/Film {i} (2019).mkv", lang_code="fr")
    assert store.suggest_series_rules() == []


def test_files_loose_at_a_library_root_are_never_suggested(store):
    """`series_folder` returns None for them: a rule on 'TV/' would declare the
    language of an entire library from a handful of stray files."""
    for i in range(4):
        store.upsert(canonical_path=f"TV/loose-{i}.mkv", lang_code="fr")
    assert store.suggest_series_rules() == []


# ── exclusions ───────────────────────────────────────────────────────


def test_a_show_that_already_has_a_rule_is_not_suggested(store):
    _episodes(store, n=4)
    store.set_series_intent(series_prefix=SHOW, lang_code="fr")
    assert store.suggest_series_rules() == []


def test_a_rule_on_a_parent_prefix_also_covers_the_show(store):
    _episodes(store, n=4)
    store.set_series_intent(series_prefix="TV", lang_code="fr")
    assert store.suggest_series_rules() == []


def test_a_rule_on_an_unrelated_show_does_not_cover_this_one(store):
    _episodes(store, n=4)
    store.set_series_intent(series_prefix="TV/Spin", lang_code="fr")
    assert _prefixes(store.suggest_series_rules()) == [SHOW + "/"]


def test_a_rule_on_a_lookalike_prefix_does_not_cover_this_show(store):
    """'TV/Alert/' must not swallow 'TV/Alerts/' — prefix matching is by folder."""
    _episodes(store, n=4)
    store.set_series_intent(series_prefix="TV/Alert", lang_code="nl")
    assert _prefixes(store.suggest_series_rules()) == [SHOW + "/"]


def test_a_dismissed_suggestion_does_not_come_back(store):
    _episodes(store, n=4)
    store.dismiss_series_suggestion(SHOW + "/")
    assert store.suggest_series_rules() == []


def test_dismissing_one_show_leaves_the_others(store):
    _episodes(store, show="TV/Alerts", n=3)
    _episodes(store, show="TV/Spin", n=3)
    store.dismiss_series_suggestion("TV/Alerts/")
    assert _prefixes(store.suggest_series_rules()) == ["TV/Spin/"]


def test_a_dismiss_without_a_trailing_slash_still_matches(store):
    _episodes(store, n=4)
    store.dismiss_series_suggestion(SHOW)
    assert store.suggest_series_rules() == []


def test_a_show_dismissed_as_multilingual_is_not_suggested(store):
    """#140: the user has already said this show is genuinely multilingual."""
    _episodes(store, n=4)
    store.dismiss_mixed(SHOW)
    assert store.suggest_series_rules() == []


def test_undismissing_a_suggestion_brings_it_back(store):
    _episodes(store, n=4)
    store.dismiss_series_suggestion(SHOW)
    assert store.undismiss_series_suggestion(SHOW) is True
    assert _prefixes(store.suggest_series_rules()) == [SHOW + "/"]


def test_undismissing_a_show_that_was_never_dismissed_reports_false(store):
    assert store.undismiss_series_suggestion(SHOW) is False


# ── what the card needs to render ────────────────────────────────────


def test_a_suggestion_carries_the_show_title_and_sample_episodes(store):
    _episodes(store, n=4)
    (s,) = store.suggest_series_rules()
    assert s["title"] == "Alerts"
    assert len(s["sample_paths"]) <= 3
    assert all(p.startswith(SHOW + "/") for p in s["sample_paths"])


def test_suggestions_are_ordered_by_how_much_they_cover(store):
    _episodes(store, show="TV/Alerts", n=5)
    _episodes(store, show="TV/Spin", n=3)
    assert _prefixes(store.suggest_series_rules()) == ["TV/Alerts/", "TV/Spin/"]


def test_applying_a_suggestion_leaves_the_episode_verdicts_alone(store):
    """The rule covers new and replaced files; a correction on one odd episode
    must survive it (per-file verdicts already win over a rule)."""
    _episodes(store, n=4)
    before = len(store.list_all())
    store.set_series_intent(series_prefix=SHOW + "/", lang_code="fr", source="suggestion")
    assert len(store.list_all()) == before
    assert store.suggest_series_rules() == []
