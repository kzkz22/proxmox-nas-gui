"""Thin HTTP layer over diagnostics.py, mirroring storage/api/sleep.py's
FixRequest/POST .../fix pattern."""

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import diagnostics as diag
from .core import state as state_store
from .core.proc import SystemOpError

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


class FixRequest(BaseModel):
    id: str
    entity: str


@router.get("")
def run_all():
    st = state_store.load_state()
    findings = diag.run_all(st)
    return {
        "findings": findings,
        "generated_at": int(time.time()),
        "summary": {
            sev: sum(1 for f in findings if f["severity"] == sev)
            for sev in ("crit", "warn", "info")
        },
    }


@router.post("/fix")
def fix(body: FixRequest):
    if not diag.is_fixable(body.id):
        raise HTTPException(400, "this check has no one-click fix")
    st = state_store.load_state()
    try:
        detail = diag.apply_fix(st, body.id, body.entity)
    except SystemOpError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "detail": detail}
