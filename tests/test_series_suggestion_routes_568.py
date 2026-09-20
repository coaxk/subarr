"""#568: the routes behind Review's series-rule suggestion card.

GET lists what to offer, dismiss silences one show, and apply writes the rule
(#226) and nothing else — the show's per-episode verdicts survive it, because a
correction on one odd episode must outlive accepting a rule for the show.
"""

from __future__ import annotations

import pytest

LIST = "/api/audio-lang/series-suggestions"
DISMISS = "/api/audio-lang/series-suggestions/dismiss"
APPLY = "/api/audio-lang/series-suggestions/apply"
SHOW = "TV/Alerts"


@pytest.fixture
def seeded(app_with_stub):
    store = app_with_stub.app.state.audio_lang
    for i in range(1, 5):
        store.upsert(canonical_path=f"{SHOW}/Season 1/Alerts - S01E0{i}.mkv", lang_code="fr")
    return app_with_stub


def test_the_list_offers_the_show(seeded):
    r = seeded.get(LIST)
    assert r.status_code == 200
    (item,) = r.json()["items"]
    assert item["series_prefix"] == SHOW + "/"
    assert item["lang_code"] == "fr"
    assert item["agreeing"] == 4
    assert item["title"] == "Alerts"
    assert item["language_name"] == "French"  # rendered by the card without a second fetch


def test_the_list_is_empty_with_nothing_to_offer(app_with_stub):
    assert app_with_stub.get(LIST).json() == {"items": []}


def test_dismiss_removes_it_from_the_list(seeded):
    assert seeded.post(DISMISS, json={"series_prefix": SHOW + "/"}).status_code == 200
    assert seeded.get(LIST).json()["items"] == []


def test_apply_writes_the_rule(seeded):
    r = seeded.post(APPLY, json={"series_prefix": SHOW + "/", "lang_code": "fr"})
    assert r.status_code == 200
    (rule,) = seeded.app.state.audio_lang.list_series_intents()
    assert rule["series_prefix"] == SHOW + "/"
    assert rule["lang_code"] == "fr"


def test_apply_records_that_the_rule_came_from_a_suggestion(seeded):
    """Provenance: a rule the user accepted from a card is not the same event as
    one they typed, and #568's own effect can only be measured if it is marked."""
    seeded.post(APPLY, json={"series_prefix": SHOW + "/", "lang_code": "fr"})
    (rule,) = seeded.app.state.audio_lang.list_series_intents()
    assert rule["source"] == "suggestion"


def test_apply_keeps_the_episode_verdicts(seeded):
    before = len(seeded.app.state.audio_lang.list_all())
    seeded.post(APPLY, json={"series_prefix": SHOW + "/", "lang_code": "fr"})
    assert len(seeded.app.state.audio_lang.list_all()) == before


def test_an_applied_show_drops_off_the_list(seeded):
    seeded.post(APPLY, json={"series_prefix": SHOW + "/", "lang_code": "fr"})
    assert seeded.get(LIST).json()["items"] == []


def test_apply_refuses_a_language_the_verdicts_do_not_agree_on(seeded):
    """The card renders one language; applying a different one would write a
    rule the evidence never supported."""
    r = seeded.post(APPLY, json={"series_prefix": SHOW + "/", "lang_code": "nl"})
    assert r.status_code == 409
    assert seeded.app.state.audio_lang.list_series_intents() == []


def test_apply_refuses_a_show_that_is_not_currently_offered(app_with_stub):
    r = app_with_stub.post(APPLY, json={"series_prefix": "TV/Never Verified/", "lang_code": "fr"})
    assert r.status_code == 409
    assert app_with_stub.app.state.audio_lang.list_series_intents() == []


def test_the_routes_are_not_double_prefixed(seeded):
    assert seeded.get("/api/audio-lang/audio-lang/series-suggestions").status_code == 404
