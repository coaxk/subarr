import asyncio
from subarr.forced_segment import ForcedSegmentParams
from subarr.forced_segment_lid import LocalLidBackend


def _run(coro):
    return asyncio.run(coro)


def test_local_backend_flags_confident_foreign_window():
    utts = [(0.0, 4.0), (5.0, 9.0), (30.0, 34.0)]
    params = ForcedSegmentParams(lid_window_s=15.0, lid_min_confidence=0.5, lid_max_english_prob=0.25)

    def fake_classify(samples):
        return fake_classify.queue.pop(0)

    fake_classify.queue = [("de", 0.9, 0.02), ("en", 0.95, 0.95)]
    clips = []

    def fake_clip(fs, s, e, out, track=0):
        clips.append((s, e))

    def fake_read(_path):
        return [0.0]

    be = LocalLidBackend(params=params, classify_fn=fake_classify, clip_fn=fake_clip, read_fn=fake_read)
    classified = _run(be.classify("/x.mkv", utts, tmp="/tmp"))
    assert classified == [((0.0, 4.0), True), ((5.0, 9.0), True), ((30.0, 34.0), False)]
    assert clips == [(0.0, 9.0), (30.0, 34.0)]


def test_local_backend_over_flags_on_clip_failure():
    utts = [(0.0, 4.0)]
    params = ForcedSegmentParams()

    def boom(*a, **k):
        raise RuntimeError("ffmpeg died")

    be = LocalLidBackend(
        params=params, classify_fn=lambda s: ("en", 0.9, 0.9), clip_fn=boom, read_fn=lambda p: [0.0]
    )
    classified = _run(be.classify("/x.mkv", utts, tmp="/tmp"))
    assert classified == [((0.0, 4.0), True)]


def test_local_backend_none_verdict_over_flags():
    utts = [(0.0, 4.0)]
    be = LocalLidBackend(
        params=ForcedSegmentParams(),
        classify_fn=lambda s: None,
        clip_fn=lambda *a, **k: None,
        read_fn=lambda p: [0.0],
    )
    classified = _run(be.classify("/x.mkv", utts, tmp="/tmp"))
    assert classified == [((0.0, 4.0), True)]
