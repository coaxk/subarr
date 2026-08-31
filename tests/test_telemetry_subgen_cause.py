"""#479: the two fields that split the `unreachable` bucket.

`subgen_kind='unreachable'` covered 108 genuine installs with no way to tell
"never configured" from "configured and broken", or "nothing is listening" from
"something answered that was not subgen".

⚠️ Both fields follow `data_persistent`'s precedent and use None for UNKNOWN.
An absent result and a negative result looking identical is the failure that
produced this issue in the first place; repeating it in the fix would be
particularly poor.
"""

from __future__ import annotations

import types

from subarr.telemetry import TelemetryCollector, TelemetryPayload


def _caps(**kw):
    base = dict(reachable=True, is_subarr_subgen=True, version="2026.08.1", probe_failure=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _collector(caps, stats=None, tmp_path=None):
    from subarr.migrate import run_migrations

    db = tmp_path / "t.db"
    run_migrations(db)
    return TelemetryCollector(
        db_path=db,
        stats_provider=lambda: stats or {},
        subgen_caps_provider=lambda: caps,
        subarr_version="9.9.9",
    )


class TestProbeFailureReachesThePayload:
    def test_a_failed_probe_carries_its_reason(self, tmp_path):
        p = _collector(_caps(reachable=False, probe_failure="dns"), tmp_path=tmp_path).build_payload()
        assert p.subgen_kind == "unreachable"
        assert p.subgen_probe_failure == "dns"

    def test_a_reachable_subgen_reports_no_failure(self, tmp_path):
        p = _collector(_caps(), tmp_path=tmp_path).build_payload()
        assert p.subgen_kind == "subarr-subgen"
        assert p.subgen_probe_failure is None

    def test_never_probed_is_distinct_from_probed_and_failed(self, tmp_path):
        # caps is None means the probe never ran. Reporting that as the same
        # thing as a failure is precisely the conflation this issue is about.
        p = _collector(None, tmp_path=tmp_path).build_payload()
        assert p.subgen_kind == "unreachable"
        assert p.subgen_probe_failure == "not_probed"

    def test_an_http_status_reason_survives_intact(self, tmp_path):
        p = _collector(_caps(reachable=False, probe_failure="http_502"), tmp_path=tmp_path).build_payload()
        assert p.subgen_probe_failure == "http_502"

    def test_missing_attribute_on_caps_does_not_crash_the_ping(self, tmp_path):
        # A caps object from an older code path may not carry the field at all.
        legacy = types.SimpleNamespace(reachable=False, is_subarr_subgen=False, version=None)
        p = _collector(legacy, tmp_path=tmp_path).build_payload()
        assert p.subgen_kind == "unreachable"
        assert p.subgen_probe_failure is None


class TestTargetIsDefaultReachesThePayload:
    def test_true_when_the_stats_provider_says_so(self, tmp_path):
        p = _collector(_caps(), {"subgen_target_is_default": True}, tmp_path=tmp_path).build_payload()
        assert p.subgen_target_is_default is True

    def test_false_when_configured(self, tmp_path):
        p = _collector(_caps(), {"subgen_target_is_default": False}, tmp_path=tmp_path).build_payload()
        assert p.subgen_target_is_default is False

    def test_absent_is_None_meaning_unknown_not_False(self, tmp_path):
        # Defaulting to False would silently report every install as
        # "configured"; defaulting to True would report every install as
        # "never configured". Both are a confident wrong answer.
        p = _collector(_caps(), {}, tmp_path=tmp_path).build_payload()
        assert p.subgen_target_is_default is None


class TestSerialisation:
    def test_both_fields_are_in_to_dict(self, tmp_path):
        # If to_dict drops them the payload looks correct in a debugger and the
        # worker never receives them.
        d = TelemetryPayload(
            install_id="a" * 32,
            sent_at=0.0,
            subarr_version="1",
            python_version="3",
            os_arch="Linux/x86_64",
            subgen_kind="unreachable",
            subgen_version=None,
            integrations={},
            library_bucket="unknown",
            scheduler_enabled=False,
            scheduler_mode=None,
            walks_per_day_30d=0.0,
            subgen_probe_failure="dns",
            subgen_target_is_default=True,
        ).to_dict()
        assert d["subgen_probe_failure"] == "dns"
        assert d["subgen_target_is_default"] is True

    def test_neither_field_can_carry_a_url_or_host(self, tmp_path):
        # The whole point is to answer the question WITHOUT transmitting the
        # target. probe_failure comes from a closed vocabulary; the other is a
        # bool.
        from subarr.subgen_client import classify_probe_failure

        import httpx

        reason = classify_probe_failure(httpx.ConnectError("connect to nas.private.lan:9000 failed"))
        p = _collector(_caps(reachable=False, probe_failure=reason), tmp_path=tmp_path).build_payload()
        blob = str(p.to_dict())
        for leak in ("nas.private", "9000", ".lan"):
            assert leak not in blob
