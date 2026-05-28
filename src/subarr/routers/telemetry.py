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
    return {
        "available": True,
        "install_id": st.install_id,
        "opted_in": st.opted_in,
        "last_ping_at": st.last_ping_at,
        "last_error": st.last_error,
        "last_payload": st.last_payload,
        "created_at": st.created_at,
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
