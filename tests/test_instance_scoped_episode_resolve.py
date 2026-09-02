"""#485: an episode id must be resolved against the library's OWN Sonarr.

Sonarr episode ids are unique only WITHIN an instance. Three call sites resolved
one against `bundle.sonarr`, which the bundle itself documents as the instance-0
alias:

    # Read/write aliases for instance 0. ... Multi-instance code uses
    # client_for()/clients_for() instead.

So on a multi-instance install, clicking a row in one library resolved the id in
another library's Sonarr and targeted a completely unrelated file. Reported by
AztecGuyGDL: clicking `@anime/The Legend of Vox Machina S03E02` produced
`@tv/The Chosen S02E04`.

⚠️ Severity is higher than the symptom suggests. On the manual path the
already-subtitled guard happened to block it, so the user saw a confusing
refusal. On the SCHEDULER path there is no such guard and nobody watching: it
would have transcribed the wrong episode and written a .srt beside the wrong
show.

⚠️ The correct answer was already available and was being thrown away.
coverage_engine builds its episode-file maps PER INSTANCE (`#161 Phase 2 ...
from ONE` client) and puts the right path on the row as `file_canonical_path`.
These sites then re-derived it, wrongly.

The `anime_stack_full` fixture already models this exact collision: episode id
1011 exists in BOTH Sonarrs, as ShowTV in the default instance and Naruto in the
anime instance. coverage_engine has a passing test for it. The queue path never
got the same treatment.
"""

from __future__ import annotations

import asyncio


def _resolve(bundle, **kw):
    from subarr.arr_resolve import resolve_episode_target

    return asyncio.run(resolve_episode_target(bundle, **kw))


class TestScopedToTheOwningLibrary:
    def test_an_anime_row_resolves_in_the_anime_sonarr(self, anime_stack_full):
        # THE BUG. Episode 1011 exists in both instances. Without scoping this
        # returns the default instance's ShowTV file.
        canonical, series_id = _resolve(
            anime_stack_full,
            sonarr_episode_id=1011,
            canonical_hint="@anime/Naruto/Season 1/Naruto.S01E01.mkv",
        )
        assert canonical is not None
        assert "Naruto" in canonical, f"resolved to the wrong instance: {canonical!r}"
        assert "ShowTV" not in canonical

    def test_a_default_library_row_still_resolves_in_the_default_sonarr(self, anime_stack_full):
        canonical, series_id = _resolve(
            anime_stack_full,
            sonarr_episode_id=1011,
            canonical_hint="ShowTV/Season 1/ShowTV.S01E01.mkv",
        )
        assert canonical is not None
        assert "ShowTV" in canonical, f"resolved to the wrong instance: {canonical!r}"
        assert "Naruto" not in canonical

    def test_the_two_libraries_resolve_the_SAME_id_differently(self, anime_stack_full):
        # The whole point. One id, two answers, decided by the owning library.
        a, _ = _resolve(
            anime_stack_full,
            sonarr_episode_id=1011,
            canonical_hint="@anime/Naruto/Season 1/Naruto.S01E01.mkv",
        )
        t, _ = _resolve(
            anime_stack_full, sonarr_episode_id=1011, canonical_hint="ShowTV/Season 1/ShowTV.S01E01.mkv"
        )
        assert a != t, "the same episode id resolved identically for two libraries"


class TestSeriesIdComesFromTheSameInstance:
    def test_provenance_is_not_taken_from_the_wrong_sonarr(self, anime_stack_full):
        # series_id feeds Bazarr's scan-disk trigger. Taking it from the wrong
        # instance points the rescan at an unrelated series, which is the same
        # bug wearing a different hat.
        _c, series_id = _resolve(
            anime_stack_full,
            sonarr_episode_id=1011,
            canonical_hint="@anime/Naruto/Season 1/Naruto.S01E01.mkv",
        )
        assert series_id == 11


class TestDegradesSafely:
    def test_no_hint_falls_back_to_instance_zero(self, anime_stack_full):
        # Legacy callers that cannot supply a hint keep working exactly as
        # before. Documented as a fallback, not as correct behaviour.
        canonical, _ = _resolve(anime_stack_full, sonarr_episode_id=1011, canonical_hint=None)
        assert canonical is not None
        assert "ShowTV" in canonical

    def test_an_unknown_library_slug_degrades_to_instance_zero(self, anime_stack_full):
        # library_for_canonical is fail-soft by contract: an unknown slug must
        # not raise and take out the row.
        canonical, _ = _resolve(anime_stack_full, sonarr_episode_id=1011, canonical_hint="@nosuchlib/x/y.mkv")
        assert canonical is not None

    def test_an_unresolvable_episode_returns_none_not_an_exception(self, anime_stack_full):
        # A control action must not 500 the page.
        canonical, series_id = _resolve(
            anime_stack_full, sonarr_episode_id=999999, canonical_hint="@anime/x/y.mkv"
        )
        assert canonical is None
        assert series_id is None


class TestSingleInstanceIsUnchanged:
    def test_byte_identical_for_a_single_stack(self, coverage_bundle):
        # The overwhelming majority of installs. clients_for degrades every
        # empty binding to instance 0, so this must behave exactly as before.
        import httpx

        def _sonarr(req):
            p = req.url.path
            if p == "/api/v3/episode/1011":
                return httpx.Response(200, json={"id": 1011, "seriesId": 11, "episodeFileId": 101})
            if p == "/api/v3/episodefile/101":
                return httpx.Response(
                    200, json={"id": 101, "path": "/data/tv/ShowTV/Season 1/ShowTV.S01E01.mkv"}
                )
            return httpx.Response(200, json=[])

        bundle = coverage_bundle(sonarr_handler=_sonarr)
        canonical, series_id = _resolve(bundle, sonarr_episode_id=1011, canonical_hint=None)
        assert canonical is not None and "ShowTV" in canonical
        assert series_id == 11
