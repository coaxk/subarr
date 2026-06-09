"""GET/POST /api/telemetry — opt-out + last-payload disclosure.

The Settings panel uses these to show exactly what we sent on the
most recent ping and to let users opt out. Per the v1.0 product
decision, telemetry is ON by default; this router is how opt-out is
surfaced.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")


@router.get("/telemetry/state")
def telemetry_state(request: Request) -> dict[str, Any]:
    tc = getattr(request.app.state, "telemetry", None)
    if tc is None:
        return {"available": False}
    st = tc.state()
    # #11: derive a 'healthy' flag so the Settings panel can show a
    # green chip on the Telemetry row instead of users having to
    # compare two timestamps mentally. Definition: the most recent
    # successful ping is newer than the most recent error, OR there
    # have never been any errors. Inverse: degraded.
    last_err_at = st.last_error_at
    last_ok_at = st.last_ping_at
    if st.last_error and last_err_at is not None:
        healthy = last_ok_at is not None and last_ok_at >= last_err_at
    else:
        # No known error, OR error pre-dates migration 005 so we have
        # no timestamp for it — treat as healthy iff we ever sent
        # successfully. The post-migration error path always sets
        # last_error_at so this branch only matters for legacy rows.
        healthy = last_ok_at is not None
    return {
        "available": True,
        "install_id": st.install_id,
        "opted_in": st.opted_in,
        "last_ping_at": st.last_ping_at,
        "last_error": st.last_error,
        "last_error_at": st.last_error_at,
        "last_payload": st.last_payload,
        "created_at": st.created_at,
        "healthy": healthy,
    }


@router.get("/telemetry/preview")
def telemetry_preview(request: Request) -> dict[str, Any]:
    """Build the payload we'd send right now without transmitting.
    Used by the Settings preview button so users can see the EXACT
    JSON before opting in/out."""
    tc = getattr(request.app.state, "telemetry", None)
    if tc is None:
        return {"available": False}
    return {"available": True, "payload": tc.build_payload().to_dict()}


@router.post("/telemetry/opt-in")
def telemetry_opt_in(request: Request) -> dict[str, Any]:
    tc = getattr(request.app.state, "telemetry", None)
    if tc is None:
        return {"available": False}
    tc.set_opt_in(True)
    return {"available": True, "opted_in": True}


@router.post("/telemetry/opt-out")
def telemetry_opt_out(request: Request) -> dict[str, Any]:
    tc = getattr(request.app.state, "telemetry", None)
    if tc is None:
        return {"available": False}
    tc.set_opt_in(False)
    return {"available": True, "opted_in": False}


@router.post("/telemetry/send-now")
async def telemetry_send_now(request: Request) -> dict[str, Any]:
    """Force-send. Used by the Settings 'Send now' button (testing the
    pipe) and by the wizard's final step if user opted in."""
    tc = getattr(request.app.state, "telemetry", None)
    if tc is None:
        return {"available": False}
    sent, err = await tc.send_now()
    return {"available": True, "sent": sent, "error": err}
