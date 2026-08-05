"""The background loop that measures idleness and spins disks down.

Runs inside the web application's own process rather than as a second
service. The unit starts a single uvicorn worker, so there is exactly one
loop, no second place for state to live, and restarting the service restarts
the monitor - which is the behaviour anyone debugging this will assume.

The loop is careful about one thing above all: nothing it does may wake a
disk. Idleness comes from /proc/diskstats, which is kernel memory, and the
power state from hdparm -C, which the drive answers without spinning up. A
monitor that polled the disks themselves would be the very problem it exists
to report on.
"""

import asyncio
import contextlib
import logging
import os
import time
from typing import Dict, Optional

from ..core import state as state_store
from .models import TEMP_SSD_OFFSET
from . import disksleep, disktemp, eventlog, sleepconf

log = logging.getLogger(__name__)

# After a failed spin-down, wait this long before trying again. Without it a
# drive that refuses every method would be hammered once per tick forever,
# and would fill the log with the same failure.
RETRY_AFTER = 3600
PRUNE_INTERVAL = 86400

_task: Optional[asyncio.Task] = None
_last_temp = 0.0
# by-id name -> what the last tick saw. The monitor's whole memory; it is
# rebuilt from scratch on restart, which is why the first tick after startup
# records states without logging transitions.
_tracked: Dict[str, dict] = {}
# by-id -> whether the disk was over the critical threshold at the last
# sample, so a crossing is logged once rather than every five minutes.
_hot: Dict[str, bool] = {}
_last_prune = 0.0


def disabled() -> bool:
    return os.environ.get("PNAS_DISABLE_MONITOR") == "1"


def snapshot() -> Dict[str, dict]:
    """What the loop last observed, for the API to serve without re-probing.

    The page would otherwise run hdparm -C per disk on every visit. Harmless,
    but pointless when a fresher answer already exists in memory.
    """
    return {name: dict(entry) for name, entry in _tracked.items()}


def forget(by_id: str) -> None:
    """Drop a disk's tracking so the next tick treats it as newly seen.

    Called after a manual spin-down: the API already logged the event, and
    without this the loop would notice the state change itself and log it a
    second time as an external one.
    """
    _tracked.pop(by_id, None)


def note_manual_sleep(by_id: str, method: Optional[str]) -> None:
    now = time.time()
    _tracked[by_id] = {
        "state": sleepconf.STANDBY,
        "since": now,
        "checked": now,
        "idle_since": now,
        "counters": None,
        "method": method,
        "we_slept_it": True,
        "retry_after": 0.0,
    }


def tick() -> None:
    """One pass over every managed disk. Synchronous on purpose - it shells
    out and touches SQLite, so it is run in a worker thread."""
    global _last_prune, _last_temp

    state = state_store.load_state()
    settings = state.disk_sleep_settings
    now = time.time()

    disks = {d["by_id"]: d for d in disksleep.list_sleep_disks() if d["rotational"]}
    stats = disksleep.read_diskstats()

    for by_id, disk in disks.items():
        policy = state.disk_sleep.get(by_id)
        idle_seconds = policy.idle_seconds if policy else settings.default_idle_seconds
        previous = _tracked.get(by_id)
        counters = stats.get(disk["name"])

        entry = {
            "state": sleepconf.UNKNOWN,
            "since": now,
            "checked": now,
            "idle_since": now,
            "counters": counters,
            "method": policy.method if policy else None,
            "we_slept_it": False,
            "retry_after": 0.0,
        }
        if previous:
            entry.update({
                "since": previous["since"],
                "idle_since": previous["idle_since"],
                "method": previous.get("method") or entry["method"],
                "we_slept_it": previous.get("we_slept_it", False),
                "retry_after": previous.get("retry_after", 0.0),
            })
            # Any change in the completed-request counters means the block
            # layer reached the disk since the last tick, so the idle clock
            # starts over.
            if counters is not None and previous.get("counters") != counters:
                entry["idle_since"] = now

        state_now = disksleep.power_state(disk["path"])
        entry["state"] = state_now

        if previous and previous["state"] != state_now and state_now != sleepconf.UNKNOWN:
            entry["since"] = now
            _log_transition(by_id, previous, entry, counters)
            if not sleepconf.is_asleep(state_now):
                entry["we_slept_it"] = False
                entry["retry_after"] = 0.0

        _tracked[by_id] = entry

        if (
            settings.enabled
            and idle_seconds > 0
            and state_now == sleepconf.ACTIVE
            and now - entry["idle_since"] >= idle_seconds
            and now >= entry["retry_after"]
        ):
            _spin_down(state, by_id, disk, entry, idle_seconds)

    for stale in set(_tracked) - set(disks):
        _tracked.pop(stale, None)

    if settings.temp_enabled and now - _last_temp >= settings.temp_interval_seconds:
        _last_temp = now
        with contextlib.suppress(Exception):
            _sample_temperatures(settings)

    if now - _last_prune > PRUNE_INTERVAL:
        _last_prune = now
        with contextlib.suppress(Exception):
            eventlog.prune(settings.retention_days)
        with contextlib.suppress(Exception):
            eventlog.prune_temperatures(settings.temp_retention_days)


def _awake(disk: dict) -> bool:
    """Whether this tick already saw the disk spinning.

    The whole safety of temperature sampling rests here. A SMART read wakes a
    sleeping drive, so the answer comes from the power state this tick already
    measured with hdparm -C, never from asking the drive again. A disk the
    monitor does not track - the system disk - is awake by definition: it is
    running the operating system.
    """
    entry = _tracked.get(disk["by_id"])
    if entry is None:
        return True
    return not sleepconf.is_asleep(entry["state"])


def _sample_temperatures(settings) -> None:
    disks = disktemp.list_temp_disks()
    readings = disktemp.sample(disks, _awake)
    if not readings:
        return
    eventlog.record_temperatures(readings)

    thresholds = {d["by_id"]: _thresholds(d, settings) for d in disks}
    for by_id, celsius in readings.items():
        warn, crit = thresholds.get(by_id, (settings.temp_warn_celsius,
                                            settings.temp_crit_celsius))
        entry = _tracked.get(by_id)
        if entry is not None:
            entry["temp"] = celsius
            entry["temp_at"] = time.time()
        _log_temperature_crossing(by_id, celsius, crit)


def _thresholds(disk: dict, settings) -> tuple:
    """A device that does not spin runs hotter as a matter of course, so the
    one pair of thresholds is shifted rather than duplicated for those."""
    offset = 0 if disk.get("rotational", True) else TEMP_SSD_OFFSET
    return settings.temp_warn_celsius + offset, settings.temp_crit_celsius + offset


def _log_temperature_crossing(by_id: str, celsius: int, crit: int) -> None:
    """One event when a disk goes over the critical threshold, one when it
    comes back - not a row every five minutes while it stays hot."""
    was_hot = _hot.get(by_id, False)
    is_hot = celsius >= crit
    if is_hot == was_hot:
        return
    _hot[by_id] = is_hot
    eventlog.record(
        by_id,
        eventlog.TEMP_HIGH if is_hot else eventlog.TEMP_NORMAL,
        eventlog.EXTERNAL,
        detail=f"{celsius} C",
    )


def _log_transition(by_id: str, previous: dict, entry: dict, counters) -> None:
    was_asleep = sleepconf.is_asleep(previous["state"])
    is_now_asleep = sleepconf.is_asleep(entry["state"])
    if was_asleep and not is_now_asleep:
        eventlog.record(by_id, eventlog.WAKE, eventlog.EXTERNAL,
                        detail=_io_detail(previous.get("counters"), counters))
    elif not was_asleep and is_now_asleep:
        # Our own spin-downs are logged where they happen, with the reason
        # that triggered them; reaching here means the drive's own APM timer
        # or something outside this application did it.
        eventlog.record(by_id, eventlog.SLEEP, eventlog.EXTERNAL,
                        detail="the drive entered standby on its own")


def _io_detail(before, after) -> Optional[str]:
    if not before or not after:
        return None
    reads = max(0, after[0] - before[0])
    writes = max(0, after[1] - before[1])
    return f"reads={reads} writes={writes}"


def _spin_down(state, by_id: str, disk: dict, entry: dict, idle_seconds: int) -> None:
    ok, method, detail = disksleep.spin_down(disk["path"], entry.get("method"))
    now = time.time()
    if ok:
        entry.update({
            "state": sleepconf.STANDBY, "since": now, "checked": now,
            "method": method, "we_slept_it": True, "retry_after": 0.0,
        })
        eventlog.record(by_id, eventlog.SLEEP, eventlog.TIMEOUT,
                        detail=f"idle {idle_seconds}s; {detail}")
        _remember_method(state, by_id, method)
    else:
        entry["retry_after"] = now + RETRY_AFTER
        eventlog.record(by_id, eventlog.SLEEP_FAILED, eventlog.TIMEOUT,
                        detail=detail)


def _remember_method(state, by_id: str, method: Optional[str]) -> None:
    """Persist the spin-down command that worked, so later attempts start
    with it instead of paying for the failures again."""
    if not method:
        return
    with state_store.lock:
        fresh = state_store.load_state()
        policy = fresh.disk_sleep.get(by_id)
        if policy is None or policy.method == method:
            return
        policy.method = method
        state_store.save_state(fresh)


async def _loop() -> None:
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:  # noqa: BLE001 - a monitor that dies is worse
            log.exception("disk sleep monitor tick failed")
        try:
            interval = state_store.load_state().disk_sleep_settings.poll_seconds
        except Exception:  # noqa: BLE001
            interval = 30
        await asyncio.sleep(interval)


async def start() -> None:
    global _task
    if disabled() or _task is not None:
        return
    _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _task
    _task = None
