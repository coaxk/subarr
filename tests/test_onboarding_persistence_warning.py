"""#473: warn about non-persistent /data during onboarding, not after.

Measured on the live fleet 2026-08-30: of installs whose client reports the
field at all, 220 of 611 (36%) run an ephemeral /data. Those users lose every
audio-language verification, queue row and coverage result on each restart, and
a fresh telemetry install_id is minted, which is what inflated the fleet count
~100x and made #202 look like an activation failure.

The detection already exists (data_persistence.py, #196/#202) and already
surfaces: a red Health row, the header pill, and a log error. 36% persisting in
the broken config anyway says those surfaces are not reaching people.

Onboarding is the one moment a user is actively configuring and could fix it in
seconds by adding a volume. It is also the only moment where the warning is
CHEAP to act on: afterwards they have already invested the setup they are about
to lose. The wizard never checked.
"""

from __future__ import annotations


def test_state_exposes_persistence(app_with_stub):
    """The wizard cannot warn about what the API does not tell it."""
    c = app_with_stub
    body = c.get("/api/onboarding/state").json()
    assert "data_persistent" in body


def test_persistent_install_reports_true(app_with_stub):
    c = app_with_stub
    c.app.state.data_persistent = True
    assert c.get("/api/onboarding/state").json()["data_persistent"] is True


def test_ephemeral_install_reports_false(app_with_stub):
    """The case that matters: 36% of reporting installs."""
    c = app_with_stub
    c.app.state.data_persistent = False
    assert c.get("/api/onboarding/state").json()["data_persistent"] is False


def test_unknown_stays_unknown_and_is_not_coerced_to_a_warning(app_with_stub):
    """None means "we could not tell", which is the normal case OUTSIDE a
    container (dev hosts, bare metal).

    It must not render as False. A wizard that told every bare-metal user their
    data was about to be lost would be worse than saying nothing: the warning
    would be wrong most of the time and people would learn to dismiss it.
    """
    c = app_with_stub
    c.app.state.data_persistent = None
    assert c.get("/api/onboarding/state").json()["data_persistent"] is None


def test_missing_attribute_does_not_500(app_with_stub):
    """Defensive: the field is set at boot, but a partially-initialised app
    (or a test harness) must not take the wizard down with it."""
    c = app_with_stub
    if hasattr(c.app.state, "data_persistent"):
        delattr(c.app.state, "data_persistent")
    r = c.get("/api/onboarding/state")
    assert r.status_code == 200
    assert r.json()["data_persistent"] is None
