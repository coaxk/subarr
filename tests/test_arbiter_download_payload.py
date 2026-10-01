"""#591: the arbiter's download-candidate POST sent a payload Bazarr rejects.

`download_episode_candidate` / `download_movie_candidate` posted the subtitle
id as `subtitles_id`, omitted the required `seriesid` and `original_format`,
and added `language` + `score` which Bazarr does not accept. Bazarr's handler
calls `post_request_parser.parse_args()`, so two missing required arguments is
a 400 and the subtitle id never arrives under the name it reads. The arbiter's
"accept this human subtitle instead of Whisper" path could not succeed.

Nothing caught it because `test_arbiter_movie_accept.py` mocks `BazarrClient`:
it asserts the call HAPPENED and never inspects the wire payload. These tests
take the #550 approach instead, stated in test_bazarr_history.py's own header -
the stub behaves like BAZARR, enforcing the names and required-ness Bazarr
declares, so a payload mismatch has to fail.

Contract source (read from a live v1.6.2 container, 2026-10-01):
    bazarr/api/providers/providers_episodes.py  post_request_parser
    bazarr/api/providers/providers_movies.py    post_request_parser
Re-verify there when bumping .github/bazarr-verified-version.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

from subarr.integrations import IntegrationError
from subarr.integrations.bazarr import BazarrClient

# Exactly what Bazarr declares required=True on each POST parser. Deliberately
# written out rather than derived: this list IS the record of the contract, so
# changing the client without changing it here fails.
EPISODE_REQUIRED = {"seriesid", "episodeid", "hi", "forced", "original_format", "provider", "subtitle"}
MOVIE_REQUIRED = {"radarrid", "hi", "forced", "original_format", "provider", "subtitle"}

# Names Bazarr does NOT declare on these endpoints. reqparse ignores unknown
# arguments silently, so sending them is harmless but misleading - and `subtitles_id`
# in particular meant the id was never read at all.
NOT_ACCEPTED = {"subtitles_id", "language", "score"}


def _bazarr_like(required: set[str], captured: dict):
    """A transport that behaves like Bazarr's reqparse, not like our caller."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode(), keep_blank_values=True)
        sent = {k: v[0] for k, v in body.items()}
        captured.update(sent)
        missing = sorted(required - set(sent))
        if missing:
            # flask_restx aborts 400 listing the offending arguments
            return httpx.Response(400, json={"message": {m: "Missing required parameter" for m in missing}})
        return httpx.Response(204)

    return handler


def _client(handler) -> BazarrClient:
    c = BazarrClient()
    c._client = httpx.AsyncClient(base_url="http://bazarr:6767", transport=httpx.MockTransport(handler))
    c._configured = True
    return c


def test_episode_download_satisfies_bazarrs_required_params():
    captured: dict = {}
    c = _client(_bazarr_like(EPISODE_REQUIRED, captured))
    asyncio.run(
        c.download_episode_candidate(
            episode_id=42,
            series_id=7,
            provider="opensubtitles",
            subtitles_id="abc123",
            forced=False,
            hi=True,
            original_format=False,
        )
    )
    assert EPISODE_REQUIRED <= set(captured), f"missing: {sorted(EPISODE_REQUIRED - set(captured))}"
    assert captured["seriesid"] == "7"
    assert captured["episodeid"] == "42"
    assert captured["subtitle"] == "abc123", "the id must arrive under Bazarr's key"


def test_movie_download_satisfies_bazarrs_required_params():
    captured: dict = {}
    c = _client(_bazarr_like(MOVIE_REQUIRED, captured))
    asyncio.run(
        c.download_movie_candidate(
            movie_id=766,
            provider="subdl",
            subtitles_id="m-1",
            forced=True,
            hi=False,
            original_format=True,
        )
    )
    assert MOVIE_REQUIRED <= set(captured), f"missing: {sorted(MOVIE_REQUIRED - set(captured))}"
    assert captured["radarrid"] == "766"
    assert captured["subtitle"] == "m-1"


def test_the_subtitle_id_is_not_sent_under_our_own_name():
    """The actual #591 bug: `subtitles_id` is our field name, not Bazarr's."""
    captured: dict = {}
    c = _client(_bazarr_like(EPISODE_REQUIRED, captured))
    asyncio.run(
        c.download_episode_candidate(
            episode_id=1,
            series_id=1,
            provider="p",
            subtitles_id="X",
            forced=False,
            hi=False,
            original_format=False,
        )
    )
    assert "subtitles_id" not in captured
    assert captured.get("subtitle") == "X"


def test_parameters_bazarr_ignores_are_not_sent():
    """language and score are not on these parsers; sending them is noise that
    reads as if it were doing something."""
    captured: dict = {}
    c = _client(_bazarr_like(EPISODE_REQUIRED, captured))
    asyncio.run(
        c.download_episode_candidate(
            episode_id=1,
            series_id=1,
            provider="p",
            subtitles_id="X",
            forced=False,
            hi=False,
            original_format=False,
        )
    )
    assert not (NOT_ACCEPTED & set(captured)), f"unaccepted keys sent: {sorted(NOT_ACCEPTED & set(captured))}"


def test_booleans_are_sent_in_the_form_bazarr_capitalizes():
    """Bazarr does args.get('hi').capitalize(), so it needs a string whose
    capitalize() is 'True'/'False'. A Python bool or 'TRUE' would break it."""
    captured: dict = {}
    c = _client(_bazarr_like(EPISODE_REQUIRED, captured))
    asyncio.run(
        c.download_episode_candidate(
            episode_id=1,
            series_id=1,
            provider="p",
            subtitles_id="X",
            forced=True,
            hi=False,
            original_format=True,
        )
    )
    for key, want in (("forced", "True"), ("hi", "False"), ("original_format", "True")):
        got = captured[key]
        assert got.capitalize() in ("True", "False"), f"{key}={got!r} does not capitalize cleanly"
        assert got.capitalize() == want, f"{key}: sent {got!r}, Bazarr would read {got.capitalize()}"


def test_a_400_from_bazarr_still_raises_IntegrationError():
    """The guard that would have surfaced #591 in production: a rejected
    payload must not be swallowed."""

    def always_400(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Missing required parameter"})

    c = _client(always_400)
    with pytest.raises(IntegrationError):
        asyncio.run(
            c.download_episode_candidate(
                episode_id=1,
                series_id=1,
                provider="p",
                subtitles_id="X",
                forced=False,
                hi=False,
                original_format=False,
            )
        )


# --- the seriesid the POST needs has to be resolved first -------------------
#
# Bazarr supplies it itself, from the same instance the download goes to:
# GET /api/episodes returns rows carrying sonarrSeriesId.


def _episodes_like(captured: dict, rows: list | None = None):
    """A transport that behaves like Bazarr's GET /api/episodes.

    `api/episodes/episodes.py` declares `add_argument('episodeid[]', ...)`, so
    the `[]` is part of the parameter NAME. A bare `episodeid` is not a
    parameter Bazarr reads and the endpoint answers 404 - which is the whole
    point of asserting the wire name below.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        if "episodeid[]" not in request.url.params:
            return httpx.Response(404, json={"message": "Series or Episode ID not provided"})
        return httpx.Response(200, json={"data": [] if rows is None else rows})

    return httpx.MockTransport(handler)


def _episodes_client(transport) -> BazarrClient:
    """Separate from _client above, which takes a HANDLER rather than a
    transport - the two are not interchangeable."""
    c = BazarrClient(base_url="http://bz.test", api_key="k")
    c._client = httpx.AsyncClient(base_url="http://bz.test", transport=transport)
    return c


def test_series_id_is_requested_under_the_name_bazarr_actually_reads():
    captured: dict = {}
    c = _episodes_client(_episodes_like(captured, rows=[{"sonarrEpisodeId": 1011, "sonarrSeriesId": 77}]))

    assert asyncio.run(c.episode_series_id(1011)) == 77
    assert captured["path"] == "/api/episodes"
    # the brackets are part of the NAME; dropping them 404s against real Bazarr
    assert captured["params"].get("episodeid[]") == "1011"
    assert "episodeid" not in captured["params"]


def test_unknown_episode_resolves_to_none_rather_than_a_bogus_series_id():
    # Bazarr answers 200 with an empty data list for an episode it does not
    # know. Returning None lets the router fail with a reason instead of
    # POSTing a request that cannot succeed.
    c = _episodes_client(_episodes_like({}, rows=[]))
    assert asyncio.run(c.episode_series_id(999999)) is None


def test_a_row_without_a_series_id_resolves_to_none():
    c = _episodes_client(_episodes_like({}, rows=[{"sonarrEpisodeId": 1011, "sonarrSeriesId": None}]))
    assert asyncio.run(c.episode_series_id(1011)) is None
