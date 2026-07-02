"""#357 — the audit walker classifies a confident-multilingual file as
'multilingual' (not 'confused'/'suspect') and auto-records the set.

The Beasts (gl/es/fr, each a high-confidence distinct chunk) is a 1/1/1 split
that today buckets as 'confused'. With per-chunk probabilities it is recognised
as multilingual. Existing fixtures carry no per-chunk probability, so they are
unaffected (classify returns [] -> stays confused/agrees)."""

from __future__ import annotations


import pytest

from subarr.audio_audit import _derive_status


def _split_conf(pairs):
    """chunks with per-chunk probability, e.g. [("gl",0.9),("es",0.9),("fr",0.8)]."""
    from collections import Counter

    langs = [lang for lang, _ in pairs]
    c = Counter(langs)
    top, n = c.most_common(1)[0]
    return {
        "aggregate": {"language": top, "n_agreeing": n, "n_total": len(langs)},
        "chunks": [{"language": lang, "probability": p} for lang, p in pairs],
    }


# ---- _derive_status unit tests -------------------------------------------------


def test_derive_status_multilingual_when_two_high_conf():
    det = {"language": "gl", "n_agreeing": 1, "n_total": 3, "unanimous": False}
    assert (
        _derive_status(
            det, mixed=False, mislabel=False, multitrack=False, multilingual_langs=["gl", "es", "fr"]
        )
        == "multilingual"
    )


def test_derive_status_single_high_conf_not_multilingual():
    det = {"language": "de", "n_agreeing": 3, "n_total": 3, "unanimous": True}
    assert (
        _derive_status(det, mixed=False, mislabel=False, multitrack=False, multilingual_langs=["de"])
        == "agrees"
    )


def test_derive_status_mixed_still_bilingual_precedence():
    det = {"language": "en", "n_agreeing": 2, "n_total": 3, "unanimous": False}
    # a mixed (bilingual heuristic) file stays bilingual even if 2 high-conf langs
    assert (
        _derive_status(det, mixed=True, mislabel=False, multitrack=False, multilingual_langs=["en", "sr"])
        == "bilingual"
    )


def test_derive_status_no_multilingual_arg_is_backward_compatible():
    det = {"language": "en", "n_agreeing": 1, "n_total": 3, "unanimous": False}
    # 1/1/1 with no per-chunk confidence -> confused, exactly as before
    assert _derive_status(det, mixed=False, mislabel=False, multitrack=False) == "confused"


# ---- walker integration --------------------------------------------------------


class _FakeSubgen:
    def __init__(self, by_path):
        self._by_path = by_path

    async def detect_language_robust(self, path):
        return self._by_path[path]


class _FakeLangStore:
    def __init__(self):
        self.rows: dict = {}

    def upsert(self, *, canonical_path, lang_code, **kw):
        self.rows[canonical_path] = {"lang_code": lang_code, **kw}


def _store(tmp_path):
    # Mirror the app: migrations own the schema (010_audio_audit.sql).
    from subarr.migrate import run_migrations
    from subarr.audio_audit_store import AudioAuditStore

    db = tmp_path / "a.db"
    run_migrations(db)
    return AudioAuditStore(db)


def _identity(path):
    return path


@pytest.mark.asyncio
async def test_walker_classifies_high_conf_split_as_multilingual(tmp_path):
    from subarr.audio_audit import AudioAuditWalker

    s = _store(tmp_path)
    subgen = _FakeSubgen(
        {
            "beasts.mkv": _split_conf([("gl", 0.91), ("es", 0.88), ("fr", 0.76)]),
            # same 1/1/1 shape but LOW confidence -> stays confused (not multilingual)
            "confused.mkv": _split_conf([("en", 0.2), ("sr", 0.18), ("fr", 0.11)]),
        }
    )
    worklist = [("beasts.mkv", "fr", 10.0), ("confused.mkv", "en", 10.0)]
    w = AudioAuditWalker(subgen, s, worklist=lambda: worklist, to_subgen=_identity)
    await w.start()
    await w._task
    assert s.get("beasts.mkv").status == "multilingual"
    assert s.get("confused.mkv").status == "confused"
