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


def _apply(st, body: "FixRequest") -> str:
    try:
        return diag.apply_fix(st, body.id, body.entity)
    except SystemOpError as exc:
        raise HTTPException(409, str(exc))


@router.post("/fix")
def fix(body: FixRequest):
    if not diag.is_fixable(body.id):
        raise HTTPException(400, "this check has no one-click fix")
    # Most fixes only act on the running system - mount something, chown
    # something - and leave state.json alone, so they need neither the lock
    # nor a save. The few that change saved configuration (enabling
    # passthrough rewrites the pool) do, or the change would survive only
    # until the next time anything reloaded the state.
    if not diag.mutates_state(body.id):
        return {"ok": True, "detail": _apply(state_store.load_state(), body)}
    with state_store.lock:
        st = state_store.load_state()
        detail = _apply(st, body)
        state_store.save_state(st)
    return {"ok": True, "detail": detail}
