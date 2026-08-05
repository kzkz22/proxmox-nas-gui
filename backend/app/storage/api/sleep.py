import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ...core import state as state_store
from ...core.auth import current_user
from ...core.proc import SystemOpError
from .. import disksleep, eventlog, monitor, sleepconf
from ..models import IDLE_CHOICES, DiskSleepPolicy, DiskSleepSettings

router = APIRouter(prefix="/sleep", tags=["sleep"])

BY_ID_RE = sleepconf.BY_ID_RE
DAY = 86400
# How stale the monitor's view may be before the endpoint asks the drive
# itself. Two ticks of the default interval: one missed tick is normal, two
# means the loop is not running (disabled, or still starting up).
SNAPSHOT_MAX_AGE = 90


class PolicyRequest(BaseModel):
    idle_seconds: int


class FixRequest(BaseModel):
    disk: str
    check: str


def _require_disk(by_id: str) -> dict:
    if not BY_ID_RE.match(by_id):
        raise HTTPException(400, "invalid disk id")
    disk = next((d for d in disksleep.list_sleep_disks() if d["by_id"] == by_id), None)
    if not disk:
        raise HTTPException(404, "no such disk")
    return disk


def _live_state(disk: dict, snapshot: dict, now: float) -> dict:
    """The disk's power state, from the monitor when it is fresh enough.

    Falling back to a live hdparm -C matters more than it looks: with the
    monitor disabled the page would otherwise report every disk as unknown
    and the manual spin-down button would have nothing to show for itself.
    """
    entry = snapshot.get(disk["by_id"])
    if entry and now - entry["checked"] <= SNAPSHOT_MAX_AGE:
        return {
            "state": entry["state"],
            "since": int(entry["since"]),
            "idle_since": int(entry["idle_since"]),
        }
    return {"state": disksleep.power_state(disk["path"]), "since": None, "idle_since": None}


@router.get("")
def list_sleep():
    st = state_store.load_state()
    disks = disksleep.list_sleep_disks()
    snapshot = monitor.snapshot()
    now = time.time()
    since = int(now) - DAY

    managed = [d for d in disks if d["rotational"]]
    described = disksleep.describe(st, managed)

    timelines = {
        d["by_id"]: eventlog.timeline(d["by_id"], since, int(now))
        for d in managed
    }

    entries = []
    for disk in managed:
        policy = st.disk_sleep.get(disk["by_id"])
        live = _live_state(disk, snapshot, now)
        info = described.get(disk["by_id"], {})
        entries.append({
            "by_id": disk["by_id"],
            "path": disk["path"],
            "model": disk["model"],
            "serial": disk["serial"],
            "size": disk["size"],
            "mountpoints": disk["mountpoints"],
            "fstypes": disk["fstypes"],
            "idle_seconds": policy.idle_seconds if policy else
                            st.disk_sleep_settings.default_idle_seconds,
            "configured": policy is not None,
            "method": policy.method if policy else None,
            "asleep": sleepconf.is_asleep(live["state"]),
            "zfs_pool": info.get("zfs_pool"),
            "pools": sorted(
                name for name, pool in st.pools.items()
                if any(b.path in disk["mountpoints"] for b in pool.branches)
            ),
            "disk_mounts": sorted(
                name for name, mount in st.disk_mounts.items()
                if mount.mountpoint in disk["mountpoints"]
            ),
            "timeline": timelines[disk["by_id"]],
            "warnings": info.get("warnings", []),
            **live,
        })

    return {
        "disks": entries,
        "other": [
            {"by_id": d["by_id"], "path": d["path"], "model": d["model"],
             "size": d["size"], "reason": "not_rotational"}
            for d in disks if not d["rotational"]
        ],
        "settings": st.disk_sleep_settings.model_dump(),
        "idle_choices": list(IDLE_CHOICES),
        "hd_idle_running": disksleep.hd_idle_running(),
        "summary": {
            **eventlog.summary(since, int(now), timelines),
            "total": len(managed),
            "asleep": sum(1 for e in entries if e["asleep"]),
            "warnings": sum(len(e["warnings"]) for e in entries),
        },
    }


@router.get("/io")
def disk_io():
    """Raw byte counters per managed disk, for the live throughput readout.

    Deliberately the cheapest endpoint in the application: one read of
    /proc/diskstats plus a cached name lookup. No lsblk, no hdparm, no ZFS -
    it is polled every couple of seconds by every open browser tab, and it
    must not cost anything, least of all a disk access that would wake the
    very drives this page is trying to keep asleep.

    Rates are not computed here. The counters and the timestamp go to the
    browser, which divides by its own measured interval: that stays correct
    when a tab is throttled or the request is slow, and it keeps two open
    tabs from stealing each other's previous sample.
    """
    counters = disksleep.read_disk_io()
    return {
        "ts": time.time(),
        "disks": {
            by_id: {"read_bytes": io[0], "write_bytes": io[1]}
            for by_id, name in disksleep.disk_names().items()
            if (io := counters.get(name)) is not None
        },
    }


@router.put("/policy/{by_id}")
def set_policy(by_id: str, body: PolicyRequest):
    disk = _require_disk(by_id)
    if not disk["rotational"]:
        raise HTTPException(409, "this device does not spin, so it cannot sleep")
    if body.idle_seconds not in IDLE_CHOICES:
        raise HTTPException(400, "invalid idle time")
    with state_store.lock:
        st = state_store.load_state()
        policy = st.disk_sleep.get(by_id) or DiskSleepPolicy()
        policy.idle_seconds = body.idle_seconds
        st.disk_sleep[by_id] = policy
        state_store.save_state(st)
    # The idle clock restarts from the change, so raising the timeout does
    # not spin a disk down a second later because the old one had elapsed.
    monitor.forget(by_id)
    return {"ok": True}


@router.post("/spindown/{by_id}")
def spin_down_now(by_id: str, user: str = Depends(current_user)):
    disk = _require_disk(by_id)
    if not disk["rotational"]:
        raise HTTPException(409, "this device does not spin, so it cannot sleep")
    st = state_store.load_state()
    policy = st.disk_sleep.get(by_id)
    ok, method, detail = disksleep.spin_down(disk["path"], policy.method if policy else None)
    if not ok:
        eventlog.record(by_id, eventlog.SLEEP_FAILED, eventlog.MANUAL,
                        actor=user, detail=detail)
        raise HTTPException(409, f"could not spin this disk down: {detail}")
    eventlog.record(by_id, eventlog.SLEEP, eventlog.MANUAL, actor=user, detail=detail)
    monitor.note_manual_sleep(by_id, method)
    with state_store.lock:
        st = state_store.load_state()
        policy = st.disk_sleep.get(by_id) or DiskSleepPolicy()
        if policy.method != method:
            policy.method = method
            st.disk_sleep[by_id] = policy
            state_store.save_state(st)
    return {"ok": True, "method": method}


@router.put("/settings")
def set_settings(body: DiskSleepSettings):
    with state_store.lock:
        st = state_store.load_state()
        st.disk_sleep_settings = body
        state_store.save_state(st)
    return {"ok": True}


@router.get("/events")
def list_events(
    disk: Optional[str] = None,
    event: Optional[str] = None,
    reason: Optional[str] = None,
    since: Optional[int] = None,
    until: Optional[int] = None,
    text: Optional[str] = None,
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    if event and event not in eventlog.EVENTS:
        raise HTTPException(400, "unknown event type")
    if reason and reason not in eventlog.REASONS:
        raise HTTPException(400, "unknown reason")
    rows, total = eventlog.query(
        disk=disk, event=event, reason=reason, since=since, until=until,
        text=text, limit=limit, offset=offset,
    )
    return {"events": rows, "total": total, "offset": offset,
            "disks": eventlog.disks_seen()}


@router.post("/fix")
def run_fix(body: FixRequest):
    if body.check not in disksleep.FIXABLE:
        raise HTTPException(400, "this check has no one-click fix")
    disk = _require_disk(body.disk)
    st = state_store.load_state()
    try:
        detail = disksleep.apply_fix(st, body.check, disk)
    except SystemOpError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "detail": detail}


@router.post("/takeover")
def takeover():
    """Stop hd-idle and adopt its per-disk timings.

    Both halves matter: leaving hd-idle running would mean two things spinning
    the same disks down, and the log would be missing every spin-down it did.
    Dropping its settings on the floor would silently turn sleeping off for
    disks the user had already configured.
    """
    imported, notes = disksleep.take_over_from_hd_idle(IDLE_CHOICES)
    known = {d["by_id"] for d in disksleep.list_sleep_disks()}
    applied = 0
    with state_store.lock:
        st = state_store.load_state()
        for by_id, seconds in imported.items():
            if by_id not in known:
                notes.append(f"{by_id}: not present, skipped")
                continue
            policy = st.disk_sleep.get(by_id) or DiskSleepPolicy()
            policy.idle_seconds = seconds
            st.disk_sleep[by_id] = policy
            applied += 1
        state_store.save_state(st)
    return {"ok": True, "imported": applied, "notes": notes}
