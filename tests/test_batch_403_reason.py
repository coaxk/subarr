"""#540: subgen's containment guard answers POST /batch with 403 and a body
naming the reason ("directory is outside the allowed media root"). subarr
replaced it with a guess about auth and reverse proxies (#524). Show what
subgen said, and name the variable that fixes it."""

from __future__ import annotations

from subarr.scan_runner import classify_batch_outcome
from subarr.scan_store import PATH_STATUS_ERROR


def test_containment_403_names_the_allowlist_variable():
    status, err = classify_batch_outcome(
        403, {"walked": 0, "error": "directory is outside the allowed media root"}
    )
    assert status == PATH_STATUS_ERROR
    assert "outside the allowed media root" in err
    assert "SUBGEN_PATH_ALLOWLIST" in err
    assert "auth" not in err.lower() and "proxy" not in err.lower()


def test_403_with_another_error_body_shows_that_error():
    status, err = classify_batch_outcome(403, {"error": "model is loading"})
    assert status == PATH_STATUS_ERROR
    assert "model is loading" in err
    assert "SUBGEN_PATH_ALLOWLIST" not in err


def test_403_without_a_body_keeps_the_auth_proxy_wording():
    status, err = classify_batch_outcome(403, {})
    assert status == PATH_STATUS_ERROR
    assert "403" in err and ("auth" in err.lower() or "proxy" in err.lower())


def test_401_with_error_body_shows_it_too():
    _, err = classify_batch_outcome(401, {"error": "api key required"})
    assert "api key required" in err
