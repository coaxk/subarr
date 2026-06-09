"""GET /api/mode — read-only transparency view of subgen's tuning.

Rev 2 of the spec dropped mode-switching: the patched subgen wires per-language
SUBGEN_KWARGS_LANG_<CODE> blocks which apply automatically based on detected /
forced language, so no european/japanese container restart dance is needed.
This endpoint surfaces the live tuning map so the Settings tab can show what's
actually configured without anyone having to read compose.yaml by hand.

NB: SUBGEN_KWARGS values are JSON-encoded strings embedded in YAML. We parse
them best-effort; malformed entries are returned as raw strings with a parse
error rather than failing the whole response.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings

router = APIRouter(prefix="/api", tags=["mode"])
log = logging.getLogger(__name__)

_LANG_ENV_RE = re.compile(r"^SUBGEN_KWARGS_LANG_([A-Z]{2})$")


class LangKwargs(BaseModel):
    code: str
    raw: str
    parsed: dict[str, Any] | None = None
    parse_error: str | None = None


class ModeResponse(BaseModel):
    compose_path: str
    top_level_kwargs: dict[str, Any] | None
    top_level_raw: str | None
    top_level_parse_error: str | None
    per_language_kwargs: list[LangKwargs]


def _parse_blob(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        return None, f"json: {e.msg} at col {e.colno}"


@router.get("/mode", response_model=ModeResponse)
def get_mode() -> ModeResponse:
    path: Path = settings.subgen_compose_path
    if not path.exists():
        raise HTTPException(503, detail=f"subgen compose not found at {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(503, detail=f"could not read subgen compose: {e}")

    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise HTTPException(500, detail=f"subgen compose is not valid YAML: {e}")

    env: dict[str, Any] = {}
    services = (doc.get("services") or {}) if isinstance(doc, dict) else {}
    for _svc_name, svc in services.items():
        if isinstance(svc, dict) and isinstance(svc.get("environment"), dict):
            if "SUBGEN_KWARGS" in svc["environment"]:
                env = svc["environment"]
                break

    top_raw = env.get("SUBGEN_KWARGS")
    top_parsed: dict[str, Any] | None = None
    top_err: str | None = None
    if isinstance(top_raw, str):
        top_parsed, top_err = _parse_blob(top_raw)
    elif isinstance(top_raw, dict):
        top_parsed = top_raw
        top_raw = json.dumps(top_raw)
    else:
        top_raw = None

    per_lang: list[LangKwargs] = []
    for key, value in env.items():
        m = _LANG_ENV_RE.match(key)
        if not m:
            continue
        code = m.group(1)
        if isinstance(value, str):
            parsed, err = _parse_blob(value)
            per_lang.append(LangKwargs(code=code, raw=value, parsed=parsed, parse_error=err))
        elif isinstance(value, dict):
            per_lang.append(LangKwargs(code=code, raw=json.dumps(value), parsed=value))
        else:
            per_lang.append(LangKwargs(code=code, raw=str(value), parse_error="unexpected value type"))

    per_lang.sort(key=lambda lk: lk.code)

    return ModeResponse(
        compose_path=str(path),
        top_level_kwargs=top_parsed,
        top_level_raw=top_raw if isinstance(top_raw, str) else None,
        top_level_parse_error=top_err,
        per_language_kwargs=per_lang,
    )
