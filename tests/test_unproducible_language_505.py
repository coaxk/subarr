"""#505 (second cause): subarr queued rows whose language its subgen cannot make.

Whisper writes exactly ONE subtitle language per job. In translate mode that is
always English regardless of the file; in transcribe mode it is the file's own
audio language. subarr could see neither, which produced a loop where BOTH
sides were behaving correctly and nothing connected them:

  1. subarr sees a row wanting Spanish with no Spanish subtitle. Correct.
  2. subarr queues it.
  3. subgen, in translate mode, sees the English subtitle it would have written
     already on disk and refuses. Also correct.
  4. Next scheduled walk, repeat. Forever.

AztecGuyGDL measured about fifteen files cycling on a fifteen minute schedule.

The gate is DELIBERATELY NOT built on `per_request_task`. subgen can accept a
per-request transcribe/translate override, so it is tempting to say subarr
could always ask for the other mode and therefore both languages are
producible. It cannot: the production queue path in scan_runner calls
`batch()` with no `task=` at all, so the global setting is what runs. Counting
per_request_task here would let rows through that then fail exactly as before.
"""

from __future__ import annotations


# ── the pure question: what can this instance produce for this file? ─────────


def test_translate_mode_can_only_ever_produce_english():
    from subarr.subgen_client import producible_subtitle_languages

    assert producible_subtitle_languages("translate", ["jpn"]) == {"en"}
    assert producible_subtitle_languages("translate", ["spa", "eng"]) == {"en"}
    assert producible_subtitle_languages("translate", []) == {"en"}


def test_transcribe_mode_produces_the_files_own_audio_language():
    from subarr.subgen_client import producible_subtitle_languages

    assert producible_subtitle_languages("transcribe", ["jpn"]) == {"ja"}
    assert producible_subtitle_languages("transcribe", ["spa", "eng"]) == {"es", "en"}


def test_transcribe_with_unknown_audio_is_UNKNOWN_not_empty():
    """An empty set would mean 'can produce nothing' and would gate every
    untagged file out of the queue. subgen detects the language itself in that
    case, so subarr genuinely does not know: the honest answer is None."""
    from subarr.subgen_client import producible_subtitle_languages

    assert producible_subtitle_languages("transcribe", ["und"]) is None
    assert producible_subtitle_languages("transcribe", []) is None
    assert producible_subtitle_languages("transcribe", [None, ""]) is None


def test_an_unadvertised_or_unrecognised_mode_never_gates():
    """Backward compatibility is the whole reason this is capability-gated. An
    older subgen advertises nothing, and must behave exactly as it does today."""
    from subarr.subgen_client import producible_subtitle_languages

    assert producible_subtitle_languages(None, ["jpn"]) is None
    assert producible_subtitle_languages("", ["jpn"]) is None
    assert producible_subtitle_languages("something-new", ["jpn"]) is None


# ── the capability actually being read off the wire ──────────────────────────


def test_capabilities_carry_the_output_configuration():
    from subarr.subgen_client import SubgenCapabilities

    caps = SubgenCapabilities(
        reachable=True,
        version="2026.08.1",
        has_queue=True,
        has_batch=True,
        is_subarr_subgen=True,
    )
    assert caps.transcribe_or_translate is None, "must default to unknown, not to a mode"
    assert caps.subtitle_language_name is None


# ── the flag, and then the thing that actually matters: its consumption ──────


def _item(*, missing, audio, score=9999):
    from subarr.coverage_engine import CoverageItem

    it = CoverageItem(
        title="Dragon Ball Super",
        media_type="episode",
        canonical_path="TV/Dragon Ball Super",
        file_canonical_path="TV/Dragon Ball Super/S03E12.mkv",
    )
    it.missing_subtitles = list(missing)
    it.audio_langs = list(audio)
    it.score = score
    it.monitored = True
    it.verification_state = "verified"
    return it


def test_scoring_flags_a_row_this_instance_cannot_satisfy():
    from subarr.coverage_engine import _score

    it = _item(missing=["es"], audio=["jpn"])
    _score(it, {}, subgen_output_task="translate")
    assert it.wanted_lang_subgen_cannot_produce is True
    assert any("spanish" in r.lower() or "es" in r.lower() for r in it.score_reasons)


def test_scoring_leaves_a_satisfiable_row_alone():
    from subarr.coverage_engine import _score

    it = _item(missing=["en"], audio=["jpn"])
    _score(it, {}, subgen_output_task="translate")
    assert it.wanted_lang_subgen_cannot_produce is False


def test_scoring_does_not_gate_when_the_mode_is_unknown():
    from subarr.coverage_engine import _score

    it = _item(missing=["es"], audio=["jpn"])
    _score(it, {}, subgen_output_task=None)
    assert it.wanted_lang_subgen_cannot_produce is False


def test_a_partially_satisfiable_row_is_not_gated():
    """Wanting English AND Spanish from an English-only instance is still worth
    queueing: the English half is real work this instance can do."""
    from subarr.coverage_engine import _score

    it = _item(missing=["en", "es"], audio=["jpn"])
    _score(it, {}, subgen_output_task="translate")
    assert it.wanted_lang_subgen_cannot_produce is False


# THE consumption test. #505's first cause was a flag that was computed,
# serialised, rendered in Coverage and covered by a dozen assertions while
# NOTHING consulted it when deciding what to queue. Asserting the flag is not
# enough; this asserts the auto-queue actually acts on it.


def test_the_auto_queue_actually_refuses_the_flagged_row():
    from subarr.auto_queue import evaluate
    from subarr.schedule_store import AutoQueueRules

    it = _item(missing=["es"], audio=["jpn"])
    it.wanted_lang_subgen_cannot_produce = True
    rules = AutoQueueRules(mode="auto", min_score=0, require_monitored=False)

    decisions = evaluate([it], rules)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.action == "skip", f"the flagged row was queued anyway: {d.reason}"
    assert "produce" in d.reason.lower() or "cannot" in d.reason.lower()


def test_an_unflagged_row_is_still_queued():
    """The guard must not swallow everything: this is the negative control."""
    from subarr.auto_queue import evaluate
    from subarr.schedule_store import AutoQueueRules

    it = _item(missing=["en"], audio=["jpn"])
    rules = AutoQueueRules(mode="auto", min_score=0, require_monitored=False)

    decisions = evaluate([it], rules)
    assert decisions[0].action == "queue", decisions[0].reason
