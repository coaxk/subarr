"""#669: name the subgen skip rule on the Queue page.

subarr-subgen v4.40 reports `skip_reasons` in its /batch reply, so a skipped
row can show which rule fired instead of "reason not in /batch response". For
an older subgen subarr falls back to its probe cache to recognise a
translate-mode skip caused by an embedded English text track.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from subarr.routers.queue import _explain_skip_reasons, _path_outcome_chip, _skip_explainer
from subarr.scan_runner import classify_batch_outcome, valid_skip_reasons
from subarr.scan_store import PATH_STATUS_SKIPPED
from subarr.submission_language import explain_embedded_subtitle_skip

CANON = "TV/x.mkv"

_EMBEDDED_DETAIL = (
    "the file already carries an embedded English subtitle track, so subgen had nothing to do. Not an issue."
)

OLD_BODY = {
    "walked": 1,
    "queued": 0,
    "skipped": 1,
    "already_in_queue": 0,
    "no_audio": 0,
    "pending_language_detect": 0,
}


def _skipped_body(skip_reasons, skipped=None):
    n = sum(skip_reasons.values()) if skipped is None else skipped
    return {
        "walked": n,
        "queued": 0,
        "skipped": n,
        "already_in_queue": 0,
        "no_audio": 0,
        "pending_language_detect": 0,
        "skip_reasons": skip_reasons,
    }


def _caps(mode="translate", **kw):
    base = {"transcribe_or_translate": mode, "skip_audio_languages": ("en",), "bypass_skip": False}
    base.update(kw)
    return SimpleNamespace(**base)


def _subtitle(language="eng", codec="subrip", forced=False, commentary=False, index=0):
    from subarr.media_probe import SubtitleStream

    return SubtitleStream(
        index=index,
        language=language,
        codec=codec,
        title=None,
        default=False,
        forced=forced,
        sdh=False,
        commentary=commentary,
    )


def _probe_store(subtitles=(), audio_tags=()):
    from subarr.media_probe import AudioStream, ProbeResult

    def get(canonical):
        return ProbeResult(
            canonical_path=canonical,
            audio=[
                AudioStream(index=i, language=t, codec=None, title=None, default=i == 0)
                for i, t in enumerate(audio_tags)
            ],
            subtitles=list(subtitles),
        )

    return SimpleNamespace(get=get)


def _raising_probe_store():
    def get(*args, **kwargs):
        raise RuntimeError("probe cache gone")

    return SimpleNamespace(get=get)


def _fake_request(**state):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state)))


def _explainer(*args, **kwargs):
    raise RuntimeError("explain_skip must not be called")


# ---- 1: what counts as a skip_reasons entry ----------------------------------


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"walked": 1},
        {"skip_reasons": []},
        {"skip_reasons": {}},
        {"skip_reasons": {"a": "1"}},
        {"skip_reasons": {"a": True}},
        {"skip_reasons": {"a": 0}},
        {"skip_reasons": {"a": -1}},
        {"skip_reasons": {1: 2}},
    ],
)
def test_valid_skip_reasons_drops_everything_unusable(body):
    assert valid_skip_reasons(body) == {}


def test_valid_skip_reasons_keeps_only_positive_int_counts():
    assert valid_skip_reasons({"skip_reasons": {"a": 2, "b": False, "c": 1}}) == {"a": 2, "c": 1}


def test_valid_skip_reasons_returns_a_new_dict():
    body = {"skip_reasons": {"a": 1}}
    out = valid_skip_reasons(body)
    out["b"] = 2
    assert body == {"skip_reasons": {"a": 1}}


# ---- 2-4: the scan error text ------------------------------------------------


def test_classify_names_the_single_skip_rule():
    status, error = classify_batch_outcome(200, _skipped_body({"internal_sub": 1}))
    assert status == PATH_STATUS_SKIPPED
    assert "subgen skipped 1 (internal_sub 1)" in error
    assert "reason not in /batch response" not in error


def test_classify_names_several_skip_rules_by_key():
    status, error = classify_batch_outcome(200, _skipped_body({"internal_sub": 2, "audio_lang": 1}))
    assert status == PATH_STATUS_SKIPPED
    assert "subgen skipped 3 (audio_lang 1, internal_sub 2)" in error


@pytest.mark.parametrize(
    "body",
    [OLD_BODY, {**OLD_BODY, "skip_reasons": {"a": True}}],
)
def test_classify_keeps_the_old_text_without_usable_skip_reasons(body):
    _status, error = classify_batch_outcome(200, body)
    assert "reason not in /batch response" in error


# ---- 5-6: a single named rule on the Queue chip -------------------------------


@pytest.mark.parametrize(
    ("reason", "label", "detail"),
    [
        (
            "internal_sub",
            "sub embedded in file",
            (
                "the file already carries a subtitle track in the target language, so subgen had "
                "nothing to do. Not an issue."
            ),
        ),
        (
            "external_sub",
            "sub already exists",
            "a matching subtitle is already on disk, so subgen had nothing to do. Not an issue.",
        ),
        (
            "named_sub",
            "sub already exists",
            (
                "a subtitle named for SUBTITLE_LANGUAGE_NAME is already on disk, so subgen had "
                "nothing to do. Not an issue."
            ),
        ),
        (
            "output_exists",
            "sub already exists",
            "the subtitle subgen would write is already on disk, so subgen had nothing to do. Not an issue.",
        ),
        (
            "internal_sub_configured",
            "sub embedded in file",
            (
                "the file carries an embedded subtitle in the SKIP_IF_INTERNAL_SUBTITLES_LANGUAGE "
                "language, so subgen skipped it as set. Not an issue."
            ),
        ),
        (
            "lrc_exists",
            "lyrics already exist",
            "an .lrc file is already beside this audio file, so subgen had nothing to do. Not an issue.",
        ),
    ],
)
def test_a_benign_skip_rule_is_filed_as_sub_exists(reason, label, detail):
    chip = _path_outcome_chip(PATH_STATUS_SKIPPED, _skipped_body({reason: 1}), None, canonical_path=CANON)
    assert chip == {
        "category": "skipped",
        "label": label,
        "detail": detail,
        "skip_reason": "sub_exists",
    }


@pytest.mark.parametrize(
    ("reason", "label", "detail"),
    [
        (
            "no_preferred_audio",
            "skipped: no preferred audio",
            "no audio track matches PREFERRED_AUDIO_LANGUAGES, and LIMIT_TO_PREFERRED_AUDIO_LANGUAGE is on.",
        ),
        (
            "unknown_language",
            "skipped: audio language unknown",
            "the audio track has no language tag, and SKIP_UNKNOWN_LANGUAGE is on.",
        ),
        (
            "unknown_language_has_subs",
            "skipped: audio language unknown",
            (
                "the audio track has no language tag, the file has embedded subtitles, and "
                "SKIP_IF_NO_LANGUAGE_BUT_SUBTITLES_EXIST is on."
            ),
        ),
        (
            "skip_sub_language",
            "skipped: subtitle language",
            "the file has an embedded subtitle in a language on SKIP_SUBTITLE_LANGUAGES.",
        ),
    ],
)
def test_a_non_benign_skip_rule_names_itself_and_skips_the_explainer(reason, label, detail):
    chip = _path_outcome_chip(
        PATH_STATUS_SKIPPED,
        _skipped_body({reason: 1}),
        None,
        canonical_path=CANON,
        explain_skip=_explainer,
    )
    assert chip == {
        "category": "skipped",
        "label": label,
        "detail": detail,
        "skip_reason": reason,
    }


# ---- 7-9: audio_lang still asks the explainer --------------------------------


def test_an_audio_lang_skip_uses_the_explainers_answer():
    calls = []

    def explain(canonical):
        calls.append(canonical)
        return {"skip_reason": "audio_lang_expected", "label": "L", "detail": "D"}

    chip = _path_outcome_chip(
        PATH_STATUS_SKIPPED,
        _skipped_body({"audio_lang": 1}),
        None,
        canonical_path=CANON,
        explain_skip=explain,
    )
    assert chip == {"category": "skipped", "skip_reason": "audio_lang_expected", "label": "L", "detail": "D"}
    assert calls == [CANON]


@pytest.mark.parametrize(
    "explain_skip",
    [lambda canonical: None, _explainer, None],
    ids=["returns-none", "raises", "absent"],
)
def test_an_audio_lang_skip_without_an_explanation_keeps_the_skip_list_text(explain_skip):
    chip = _path_outcome_chip(
        PATH_STATUS_SKIPPED,
        _skipped_body({"audio_lang": 1}),
        None,
        canonical_path=CANON,
        explain_skip=explain_skip,
    )
    assert chip == {
        "category": "skipped",
        "label": "skipped: audio language",
        "detail": (
            "the audio language is on this subgen's SKIP_IF_AUDIO_LANGUAGES list. If the audio is "
            "really another language, verify it in Review and requeue."
        ),
        "skip_reason": "audio_lang",
    }


def test_a_benign_rule_never_calls_the_explainer():
    calls = []

    def explain(canonical):
        calls.append(canonical)
        return {"skip_reason": "audio_lang_expected", "label": "L", "detail": "D"}

    chip = _path_outcome_chip(
        PATH_STATUS_SKIPPED,
        _skipped_body({"internal_sub": 1}),
        None,
        canonical_path=CANON,
        explain_skip=explain,
    )
    assert chip["skip_reason"] == "sub_exists"
    assert calls == []


# ---- 10, 17: nothing usable in the body falls back to the old path ------------


@pytest.mark.parametrize("reason", ["unknown", "future_rule"])
def test_an_unnamed_rule_falls_back_to_the_old_unknown_chip(reason):
    chip = _path_outcome_chip(
        PATH_STATUS_SKIPPED,
        _skipped_body({reason: 1}),
        None,
        canonical_path=None,
        explain_skip=None,
    )
    assert chip["skip_reason"] == "unknown"
    assert chip["label"] == "skipped"


@pytest.mark.parametrize("body", [None, OLD_BODY], ids=["body-none", "no-skip-reasons"])
def test_a_body_without_skip_reasons_keeps_the_old_unknown_chip(body):
    chip = _path_outcome_chip(PATH_STATUS_SKIPPED, body, "generic", canonical_path=None, explain_skip=None)
    assert chip["skip_reason"] == "unknown"
    assert chip["label"] == "skipped"


# ---- 11-16: several named rules -----------------------------------------------


def test_all_benign_rules_together_are_one_sub_exists_row():
    chip = _path_outcome_chip(
        PATH_STATUS_SKIPPED,
        _skipped_body({"internal_sub": 2, "external_sub": 1}),
        None,
        canonical_path=CANON,
    )
    assert chip == {
        "category": "skipped",
        "label": "sub already exists",
        "detail": "subgen skipped 3: 2 sub embedded in file, 1 sub already on disk. Not an issue.",
        "skip_reason": "sub_exists",
    }


def test_one_non_benign_rule_among_benign_ones_names_that_rule():
    chip = _path_outcome_chip(
        PATH_STATUS_SKIPPED,
        _skipped_body({"internal_sub": 2, "audio_lang": 1}),
        None,
        canonical_path=CANON,
        explain_skip=_explainer,
    )
    assert chip == {
        "category": "skipped",
        "label": "skipped",
        "detail": "subgen skipped 3: 2 sub embedded in file, 1 audio language on the skip list.",
        "skip_reason": "audio_lang",
    }


def test_two_non_benign_rules_stay_unknown():
    chip = _path_outcome_chip(
        PATH_STATUS_SKIPPED,
        _skipped_body({"audio_lang": 1, "skip_sub_language": 1}),
        None,
        canonical_path=CANON,
    )
    assert chip["skip_reason"] == "unknown"
    assert (
        chip["detail"]
        == "subgen skipped 2: 1 audio language on the skip list, 1 embedded sub language on the skip list."
    )


def test_a_benign_rule_beside_an_unrecorded_one_stays_unknown():
    chip = _path_outcome_chip(
        PATH_STATUS_SKIPPED,
        _skipped_body({"internal_sub": 1, "unknown": 1}),
        None,
        canonical_path=CANON,
    )
    assert chip["skip_reason"] == "unknown"
    assert chip["detail"] == "subgen skipped 2: 1 sub embedded in file, 1 reason not reported."


def test_a_benign_rule_beside_a_future_one_stays_unknown():
    chip = _path_outcome_chip(
        PATH_STATUS_SKIPPED,
        _skipped_body({"internal_sub": 1, "future_rule": 1}),
        None,
        canonical_path=CANON,
    )
    assert chip["skip_reason"] == "unknown"
    assert chip["detail"] == "subgen skipped 2: 1 future_rule, 1 sub embedded in file."


def test_equal_counts_are_ordered_by_reason():
    chip = _path_outcome_chip(
        PATH_STATUS_SKIPPED,
        _skipped_body({"external_sub": 1, "internal_sub": 1}),
        None,
        canonical_path=CANON,
    )
    assert chip["detail"] == "subgen skipped 2: 1 sub already on disk, 1 sub embedded in file. Not an issue."


# ---- 18-20: the embedded-subtitle explainer ----------------------------------


@pytest.mark.parametrize("language", ["eng", "en"])
def test_an_embedded_english_text_track_explains_the_skip(language):
    out = explain_embedded_subtitle_skip(
        CANON,
        caps=_caps(),
        probe_store=_probe_store([_subtitle(language=language, codec="subrip")]),
    )
    assert out == {
        "skip_reason": "sub_exists",
        "label": "sub embedded in file",
        "detail": _EMBEDDED_DETAIL,
    }


@pytest.mark.parametrize(
    "subtitles",
    [
        [_subtitle(forced=True)],
        [_subtitle(commentary=True)],
        [_subtitle(codec="hdmv_pgs_subtitle")],
        [_subtitle(codec="pgssub")],
        [_subtitle(language="fre")],
        [_subtitle(language=None)],
        [],
    ],
    ids=[
        "forced",
        "commentary",
        "pgs-codec",
        "pgssub-codec",
        "not-english",
        "no-language",
        "no-subtitles",
    ],
)
def test_the_embedded_explainer_needs_a_usable_english_text_track(subtitles):
    assert explain_embedded_subtitle_skip(CANON, caps=_caps(), probe_store=_probe_store(subtitles)) is None


@pytest.mark.parametrize(
    ("caps", "probe_store"),
    [
        (_caps(mode="transcribe"), _probe_store([_subtitle()])),
        (_caps(mode=None), _probe_store([_subtitle()])),
        (None, _probe_store([_subtitle()])),
        (_caps(), None),
        (_caps(), SimpleNamespace(get=lambda canonical: None)),
        (_caps(), _raising_probe_store()),
    ],
    ids=["transcribe", "no-mode", "no-caps", "no-probe-store", "no-probe", "probe-raises"],
)
def test_the_embedded_explainer_declines_when_it_cannot_tell(caps, probe_store):
    assert explain_embedded_subtitle_skip(CANON, caps=caps, probe_store=probe_store) is None


# ---- 21-22: the bound explainer ----------------------------------------------


def test_the_skip_explainer_prefers_the_embedded_subtitle_explanation():
    request = _fake_request(
        subgen_caps=_caps(),
        probe_store=_probe_store([_subtitle(language="eng", codec="subrip")]),
        audio_lang=None,
    )
    assert _skip_explainer(request)(CANON) == {
        "skip_reason": "sub_exists",
        "label": "sub embedded in file",
        "detail": _EMBEDDED_DETAIL,
    }


def test_the_skip_explainer_still_runs_the_audio_explanation():
    request = _fake_request(
        subgen_caps=_caps(skip_audio_languages=("en",), bypass_skip=False),
        probe_store=_probe_store([], audio_tags=["eng"]),
        audio_lang=None,
    )
    assert _skip_explainer(request)(CANON)["skip_reason"] == "audio_lang"


# ---- _explain_skip_reasons is the helper behind the chip ----------------------


def test_explain_skip_reasons_reports_nothing_without_usable_counts():
    assert _explain_skip_reasons(None) is None
    assert _explain_skip_reasons(OLD_BODY) is None
    assert _explain_skip_reasons(_skipped_body({"unknown": 1})) is None
