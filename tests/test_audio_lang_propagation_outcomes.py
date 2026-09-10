"""#516: `_propagate_to_sonarr` had five distinct failure modes, all reported as
`{"attempted": True, "ok": False, "detail": ...}` inside an HTTP 200, and no
test exercised any of them. This file drives the real function against a
MockTransport Sonarr and pins:

  - a machine-readable `reason` on every failure so the UI can group 400
    identical failures into one line instead of 400 alerts;
  - the language table is fetched ONCE across many files (the docstring's
    "cached per process" claim, now true);
  - a language missing from the cached table triggers exactly one refresh
    before giving up, so a Sonarr upgrade that adds a language is picked up
    without waiting for the TTL;
  - the Bazarr "sync with Sonarr" trigger is coalesced per instance: a burst
    of propagations fires one immediate sync and one trailing sync, never one
    per file.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from subarr.integrations.sonarr import SonarrClient
from subarr.routers import audio_lang as mod

PATH = "ShowTV/Season 1/ShowTV.S01E01.mkv"
EPISODE_ID = 5
EPISODE_FILE_ID = 77


class FakeSonarr:
    """A SonarrClient over MockTransport that records every request and lets a
    test script the /language table and the PUT outcome."""

    def __init__(self, *, languages=None, put_status=200, language_status=200):
        self.calls: list[tuple[str, str]] = []
        self.language_replies = list(languages) if languages is not None else [[{"id": 1, "name": "English"}]]
        self.put_status = put_status
        self.language_status = language_status
        self.client = SonarrClient(base_url="http://sonarr.test", api_key="k")
        self.client._client = httpx.AsyncClient(
            base_url="http://sonarr.test", transport=httpx.MockTransport(self._handle)
        )

    def _handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        p = req.url.path
        if p == f"/api/v3/episode/{EPISODE_ID}":
            return httpx.Response(200, json={"id": EPISODE_ID, "episodeFileId": EPISODE_FILE_ID})
        if p == "/api/v3/language":
            if self.language_status != 200:
                return httpx.Response(self.language_status, text="down")
            i = min(len(self.language_gets) - 1, len(self.language_replies) - 1)
            return httpx.Response(200, json=self.language_replies[i])
        if p == "/api/v3/episodefile/bulk":
            return httpx.Response(self.put_status, json=[] if self.put_status == 200 else {"error": "x"})
        return httpx.Response(404, text=f"unexpected {p}")

    @property
    def language_gets(self):
        return [c for c in self.calls if c[1] == "/api/v3/language"]

    @property
    def puts(self):
        return [c for c in self.calls if c == ("PUT", "/api/v3/episodefile/bulk")]


def _request(sonarr: SonarrClient | None, *, items=None, bazarr=None):
    """A request whose bundle routes every library to `sonarr`, with a
    coverage snapshot that knows `items`. Bazarr defaults to unconfigured so
    the success path stops at the Sonarr PUT."""
    unconfigured = SimpleNamespace(is_configured=lambda: False, _base_url="http://bazarr-none.test")

    def client_for(service, _iid):
        if service == "sonarr":
            return sonarr if sonarr is not None else SimpleNamespace(is_configured=lambda: False)
        return bazarr or unconfigured

    bundle = SimpleNamespace(client_for=client_for)
    snap = SimpleNamespace(items=items if items is not None else [])
    cov = SimpleNamespace(get_cached=lambda: snap)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(integrations=bundle, coverage_cache=cov))
    )


def _known_item(path=PATH):
    return {"file_canonical_path": path, "bazarr": {"episode_id": EPISODE_ID}}


def _propagate(request, path=PATH, lang="es"):
    return asyncio.run(mod._propagate_to_sonarr(request, canonical_path=path, lang_code=lang))


# --- every failure names its reason ------------------------------------------


def test_not_configured_is_named():
    r = _propagate(_request(None))
    assert r["attempted"] is True and r["ok"] is False
    assert r["reason"] == "not_configured"


def test_unresolvable_episode_file_is_named_and_carries_the_path():
    fs = FakeSonarr()
    r = _propagate(_request(fs.client, items=[]))  # snapshot does not know the path
    assert r["ok"] is False
    assert r["reason"] == "episode_file_unresolved"
    assert r["path"] == PATH
    assert fs.puts == []


def test_language_fetch_failure_is_named():
    fs = FakeSonarr(language_status=500)
    r = _propagate(_request(fs.client, items=[_known_item()]))
    assert r["ok"] is False
    assert r["reason"] == "language_fetch_failed"
    assert fs.puts == []


def test_unsupported_language_is_named_and_refreshes_the_table_exactly_once():
    # Galician is in neither reply: one cached read, one forced refresh, then
    # an honest failure. Not a refetch per file, not zero refetches.
    fs = FakeSonarr(languages=[[{"id": 1, "name": "English"}], [{"id": 1, "name": "English"}]])
    r = _propagate(_request(fs.client, items=[_known_item()]), lang="gl")
    assert r["ok"] is False
    assert r["reason"] == "language_unsupported"
    assert "Galician" in r["detail"]
    assert len(fs.language_gets) == 2
    assert fs.puts == []


def test_put_failure_is_named():
    fs = FakeSonarr(languages=[[{"id": 1, "name": "English"}, {"id": 3, "name": "Spanish"}]], put_status=500)
    r = _propagate(_request(fs.client, items=[_known_item()]))
    assert r["ok"] is False
    assert r["reason"] == "put_failed"
    assert len(fs.puts) == 1


def test_success_has_no_reason_and_reports_what_was_written():
    fs = FakeSonarr(languages=[[{"id": 1, "name": "English"}, {"id": 3, "name": "Spanish"}]])
    r = _propagate(_request(fs.client, items=[_known_item()]))
    assert r["ok"] is True
    assert "reason" not in r
    assert r["episode_file_id"] == EPISODE_FILE_ID
    assert r["language"] == "Spanish"
    assert len(fs.puts) == 1


# --- the table is fetched once, not once per file ----------------------------


def test_language_table_is_fetched_once_across_many_files():
    table = [[{"id": 1, "name": "English"}, {"id": 3, "name": "Spanish"}]]
    fs = FakeSonarr(languages=table)
    paths = [f"ShowTV/Season 1/ShowTV.S01E{i:02d}.mkv" for i in range(1, 6)]
    req = _request(fs.client, items=[_known_item(p) for p in paths])
    results = [_propagate(req, path=p) for p in paths]
    assert all(r["ok"] for r in results)
    assert len(fs.puts) == 5
    assert len(fs.language_gets) == 1, "the language table was re-fetched per file"


def test_a_language_added_by_a_sonarr_upgrade_is_found_via_the_refresh():
    # First (cached) table lacks Spanish; the refresh sees the upgraded table.
    fs = FakeSonarr(
        languages=[
            [{"id": 1, "name": "English"}],
            [{"id": 1, "name": "English"}, {"id": 3, "name": "Spanish"}],
        ]
    )
    r = _propagate(_request(fs.client, items=[_known_item()]))
    assert r["ok"] is True
    assert len(fs.language_gets) == 2
    assert len(fs.puts) == 1


# --- Bazarr sync coalescing ---------------------------------------------------


class FakeBazarr:
    def __init__(self):
        self.triggers: list[str] = []
        self._base_url = "http://bazarr-x.test"

    def is_configured(self):
        return True

    async def list_tasks(self):
        return [{"job_id": "update_series", "name": "Sync with Sonarr"}]

    async def trigger_task(self, task_id):
        self.triggers.append(task_id)


def test_bazarr_sync_burst_fires_once_now_and_once_trailing(monkeypatch):
    mod._reset_bazarr_sync_state()
    monkeypatch.setattr(mod, "BAZARR_SYNC_COALESCE_S", 0.05)
    bz = FakeBazarr()
    bundle = SimpleNamespace(client_for=lambda svc, iid: bz)

    async def go():
        results = [await mod._trigger_bazarr_sync(bundle, PATH) for _ in range(6)]
        fired_immediately = len(bz.triggers)
        await asyncio.sleep(0.2)  # let the trailing sync land
        return results, fired_immediately

    results, fired_immediately = asyncio.run(go())
    assert fired_immediately == 1, "a burst must not trigger one Bazarr sync per file"
    assert len(bz.triggers) == 2, "the calls that were coalesced must be covered by ONE trailing sync"
    assert results[0]["ok"] is True and results[0].get("coalesced") is False
    assert all(r["ok"] is True and r["coalesced"] is True for r in results[1:])


def test_bazarr_sync_after_the_window_fires_immediately_again(monkeypatch):
    mod._reset_bazarr_sync_state()
    monkeypatch.setattr(mod, "BAZARR_SYNC_COALESCE_S", 0.05)
    bz = FakeBazarr()
    bundle = SimpleNamespace(client_for=lambda svc, iid: bz)

    async def go():
        a = await mod._trigger_bazarr_sync(bundle, PATH)
        await asyncio.sleep(0.1)
        b = await mod._trigger_bazarr_sync(bundle, PATH)
        return a, b

    a, b = asyncio.run(go())
    assert a["coalesced"] is False and b["coalesced"] is False
    assert len(bz.triggers) == 2


def test_bazarr_sync_coalescing_is_per_instance(monkeypatch):
    mod._reset_bazarr_sync_state()
    monkeypatch.setattr(mod, "BAZARR_SYNC_COALESCE_S", 5.0)
    one, two = FakeBazarr(), FakeBazarr()
    two._base_url = "http://bazarr-y.test"
    which = {"n": 0}

    def client_for(svc, iid):
        which["n"] += 1
        return one if which["n"] % 2 else two

    bundle = SimpleNamespace(client_for=client_for)

    async def go():
        return [await mod._trigger_bazarr_sync(bundle, PATH) for _ in range(2)]

    rs = asyncio.run(go())
    assert all(r["coalesced"] is False for r in rs), "a recent sync on one Bazarr must not gate another"
    assert len(one.triggers) == 1 and len(two.triggers) == 1
