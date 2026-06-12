"""#203 — "what you're missing" update nudge.

The fleet sat on 1.1.0/1.2.x for weeks: a bare version chip doesn't convert.
The atom feed already carries the last ~10 releases, and our release TITLES
are descriptive ("v1.5.2 - movie coverage (Radarr), ..."), so the missed-
release titles ARE the digest — no HTML bodies, no sanitization surface.
"""

from __future__ import annotations

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>tag:github.com,2008:https://github.com/coaxk/subarr/releases</id>
  <entry>
    <id>tag:github.com,2008:Repository/1/v1.5.2</id>
    <updated>2026-06-12T08:00:00Z</updated>
    <link rel="alternate" type="text/html" href="https://github.com/coaxk/subarr/releases/tag/v1.5.2"/>
    <title>v1.5.2 - movie coverage (Radarr), data-persistence guard</title>
    <content type="html">&lt;p&gt;notes&lt;/p&gt;</content>
  </entry>
  <entry>
    <id>tag:github.com,2008:Repository/1/v1.5.1</id>
    <updated>2026-06-11T08:00:00Z</updated>
    <link rel="alternate" type="text/html" href="https://github.com/coaxk/subarr/releases/tag/v1.5.1"/>
    <title>v1.5.1 - multiple media locations, arm64, fleet crash telemetry</title>
    <content type="html">&lt;p&gt;notes&lt;/p&gt;</content>
  </entry>
  <entry>
    <id>tag:github.com,2008:Repository/1/v1.4.0</id>
    <updated>2026-06-09T08:00:00Z</updated>
    <link rel="alternate" type="text/html" href="https://github.com/coaxk/subarr/releases/tag/v1.4.0"/>
    <title>v1.4.0 - aftercare, track-mismatch fix, queue authority</title>
    <content type="html">&lt;p&gt;notes&lt;/p&gt;</content>
  </entry>
</feed>"""


def test_parse_atom_entries_returns_all_with_titles(subarr_env):
    from subarr.update_checker import parse_atom_entries

    entries = parse_atom_entries(_FEED)
    assert [e["tag"] for e in entries] == ["v1.5.2", "v1.5.1", "v1.4.0"]
    assert entries[0]["title"].startswith("v1.5.2 - movie coverage")
    assert entries[0]["notes_url"].endswith("/releases/tag/v1.5.2")
    assert entries[2]["released_at"] is not None


def test_parse_atom_feed_still_returns_newest(subarr_env):
    # Back-compat: the single-entry parser keeps its contract (incl. body).
    from subarr.update_checker import parse_atom_feed

    newest = parse_atom_feed(_FEED)
    assert newest["tag"] == "v1.5.2"
    assert newest["body"]  # body only matters for the newest (breaking detect)


def test_missed_releases_between_current_and_latest(subarr_env):
    from subarr.update_checker import missed_releases, parse_atom_entries

    entries = parse_atom_entries(_FEED)
    # On 1.4.0: missing the two newer releases, newest first.
    missed = missed_releases(entries, "1.4.0")
    assert [m["tag"] for m in missed] == ["v1.5.2", "v1.5.1"]
    # Up to date: nothing missed.
    assert missed_releases(entries, "v1.5.2") == []
    # Older than the feed window (e.g. 1.1.0): everything in the feed is missed.
    assert [m["tag"] for m in missed_releases(entries, "1.1.0")] == ["v1.5.2", "v1.5.1", "v1.4.0"]
    # Unknown current: don't guess — nothing missed.
    assert missed_releases(entries, None) == []


def test_state_roundtrips_missed_releases(subarr_env, tmp_path):
    from subarr.migrate import run_migrations
    from subarr.update_checker import UpdateChecker

    db = tmp_path / "t.db"
    run_migrations(db)
    chk = UpdateChecker(db, products={"subarr": "coaxk/subarr"})
    chk._write_state(
        product="subarr",
        repo="coaxk/subarr",
        current_version="1.4.0",
        latest_version="v1.5.2",
        latest_released_at=1781000000.0,
        release_notes_url="https://github.com/coaxk/subarr/releases/tag/v1.5.2",
        is_breaking=False,
        last_error=None,
        missed=[
            {"tag": "v1.5.2", "title": "v1.5.2 - movie coverage", "notes_url": "u1", "released_at": 1.0},
            {"tag": "v1.5.1", "title": "v1.5.1 - multi-library", "notes_url": "u2", "released_at": 2.0},
        ],
    )
    st = {s.product: s for s in chk.states()}["subarr"]
    assert [m["tag"] for m in st.missed_releases] == ["v1.5.2", "v1.5.1"]
    assert st.to_dict()["missed_releases"][0]["title"].startswith("v1.5.2")
