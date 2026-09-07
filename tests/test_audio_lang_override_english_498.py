"""#498: a user-verified ENGLISH audio language must reach subgen when doing so
cannot cause a skip.

Background, because the mechanism is counter-intuitive: subgen's
`audio_language_override` SUBSTITUTES the declared language into the skip check
rather than bypassing it:

    if audio_language_override is not None:
        audio_langs = [audio_language_override]
    ...
    if any(lang in skip_audio_languages for lang in audio_langs):
        return True   # skip

So forwarding `en` to an install running SKIP_IF_AUDIO_LANGUAGES=eng makes the
file SKIP, the opposite of what the verifying user wanted. That is why English
was excluded outright. The cost was that installs which skip nothing silently
discarded a verification the user had explicitly made.

subarr can now see the effective list (subarr-subgen patch 0048 advertises it),
so the exclusion becomes conditional rather than absolute.
"""

from __future__ import annotations


def _store(tmp_path):
    from subarr.audio_lang_store import AudioLangStore
    from subarr.migrate import run_migrations

    db = tmp_path / "subarr.db"
    run_migrations(db)
    return AudioLangStore(db)


def _verified_en(tmp_path):
    s = _store(tmp_path)
    s.upsert(canonical_path="e.mkv", lang_code="en", source="user", confidence=1.0)
    return s


def test_english_forwarded_when_subgen_skips_nothing(tmp_path):
    """The reported case: no English skip configured, so declaring `en` cannot
    cause a skip and it seeds the Whisper source language instead."""
    from subarr.audio_lang_store import resolve_audio_language_override

    s = _verified_en(tmp_path)
    assert resolve_audio_language_override(s, "e.mkv", skip_audio_languages=()) == "en"


def test_english_withheld_when_subgen_skips_english(tmp_path):
    """The regression this fix must not cause. Forwarding `en` here would put the
    file ON the skip list, so it stays withheld."""
    from subarr.audio_lang_store import resolve_audio_language_override

    s = _verified_en(tmp_path)
    assert resolve_audio_language_override(s, "e.mkv", skip_audio_languages=("en",)) is None


def test_english_withheld_when_capability_absent(tmp_path):
    """Older subgen does not advertise the list. Unknown must mean the
    pre-#498 behaviour, never an optimistic guess: guessing wrong here silently
    stops transcription rather than merely failing to start it."""
    from subarr.audio_lang_store import resolve_audio_language_override

    s = _verified_en(tmp_path)
    assert resolve_audio_language_override(s, "e.mkv", skip_audio_languages=None) is None
    # And the default argument must behave the same as an explicit None.
    assert resolve_audio_language_override(s, "e.mkv") is None


def test_non_english_is_unaffected_by_the_skip_list(tmp_path):
    """Deliberately scoped. A verified non-English language is forwarded exactly
    as before, even when subgen would skip it, because changing that would alter
    behaviour well beyond the reported bug. Pinned so the scoping is a decision
    rather than an accident."""
    from subarr.audio_lang_store import resolve_audio_language_override

    s = _store(tmp_path)
    s.upsert(canonical_path="j.mkv", lang_code="ja", source="user", confidence=1.0)
    assert resolve_audio_language_override(s, "j.mkv", skip_audio_languages=("ja",)) == "ja"
    assert resolve_audio_language_override(s, "j.mkv", skip_audio_languages=()) == "ja"
    assert resolve_audio_language_override(s, "j.mkv") == "ja"


def test_english_still_gated_by_the_evidence_rules(tmp_path):
    """Relaxing the English exclusion must not weaken the #105 evidence gate: a
    low-confidence English verification is still refused even with no skip list."""
    from subarr.audio_lang_store import resolve_audio_language_override

    s = _store(tmp_path)
    s.upsert(canonical_path="w.mkv", lang_code="en", source="tautulli", confidence=0.2)
    assert resolve_audio_language_override(s, "w.mkv", skip_audio_languages=()) is None


def test_eng_three_letter_form_also_honoured(tmp_path):
    """The store normalises to 2-letter, but the guard historically matched both
    forms. Whichever the store yields, an English verification must be treated
    consistently against the skip list."""
    from subarr.audio_lang_store import resolve_audio_language_override

    s = _store(tmp_path)
    s.upsert(canonical_path="x.mkv", lang_code="eng", source="user", confidence=1.0)
    assert resolve_audio_language_override(s, "x.mkv", skip_audio_languages=("en",)) is None
    assert resolve_audio_language_override(s, "x.mkv", skip_audio_languages=()) == "en"
