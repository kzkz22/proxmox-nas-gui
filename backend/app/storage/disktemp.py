"""Reading drive temperatures without waking anything up.

The pure half - turning smartctl's JSON into a number, or into None - lives in
sleepconf.py, alongside the other parsers.

This is the one measurement in the application that is not free. Idleness
comes from /proc/diskstats and the power state from hdparm -C, neither of
which touches a platter; a temperature is a real SMART query, and reading
SMART from a sleeping drive spins it back up. That is not a theoretical
worry - it is precisely what this application's own smartd warning tells
users to stop smartd from doing.

So there are two independent guards, and the first one is the real one:

  1. The caller only asks about disks the monitor has just observed to be
     awake. A sleeping disk is never passed to read_temperature at all.
  2. `smartctl -n standby` refuses to wake a drive that fell asleep between
     that observation and this command, exiting 2 instead.
"""

from typing import Dict, List, Optional

from ..core.proc import run_unchecked
from . import disksleep, sleepconf

SMARTCTL_TIMEOUT = 20

# Two of these bits are ordinary, not failures: bit 1 (2) is "the device is in
# a low-power mode", which is the answer -n standby exists to give, and bit 6
# (64) is "the error log contains old records", which says nothing about
# whether this reading worked. With -j there is always a JSON document to
# read, so the exit status is only used to explain a missing value.
SMART_STANDBY_BIT = 0x02


def read_temperature(path: str) -> Optional[int]:
    """Degrees Celsius for one device, or None when there is no reading.

    None covers every doubtful case on purpose - asleep, unsupported, an
    unparseable answer, a nonsensical value. The result goes straight into a
    history that is later averaged and charted, where a gap is honest and an
    invented sample is not.
    """
    _code, out, _err = run_unchecked(
        ["smartctl", "-j", "-n", "standby", "-A", path], timeout=SMARTCTL_TIMEOUT
    )
    return sleepconf.parse_smart_temperature(out)


def probe(path: str) -> dict:
    """read_temperature plus why it came back empty, for the diagnostics."""
    code, out, err = run_unchecked(
        ["smartctl", "-j", "-n", "standby", "-A", path], timeout=SMARTCTL_TIMEOUT
    )
    celsius = sleepconf.parse_smart_temperature(out)
    if celsius is not None:
        return {"celsius": celsius, "reason": None}
    if code == -1:
        return {"celsius": None, "reason": "smartctl_missing"}
    if code & SMART_STANDBY_BIT or sleepconf.smart_said_standby(out):
        return {"celsius": None, "reason": "standby"}
    return {"celsius": None, "reason": "unsupported"}


def list_temp_disks() -> List[dict]:
    """Every physical disk worth showing a temperature for.

    A superset of the spin-down list: the system disk is included here, marked
    `system`, because it is the one drive that never stops spinning and is
    therefore usually the hottest in the box - and until now the only one the
    interface never mentioned. It gets a reading and nothing else; no policy,
    no controls, and it stays out of every list that could act on a disk.
    """
    manageable = {d["path"]: d for d in disksleep.list_sleep_disks()}
    everything = disksleep.lsblk_disks()
    by_id = disksleep.pools.by_id_map()

    result = []
    for disk in everything:
        known = manageable.get(disk["path"])
        if known:
            result.append({**known, "system": False})
            continue
        names = sorted(by_id.get(disksleep.resolve_device(disk["path"]), []))
        if not names:
            continue
        # Not in the manageable list: either the system disk, or a disk the
        # spin-down side excluded. Either way it can still report a number.
        result.append({**disk, "by_id": names[0], "by_id_all": names, "system": True})
    return sorted(result, key=lambda d: (d["system"], d["by_id"]))


def sample(disks: List[dict], is_awake) -> Dict[str, int]:
    """Read every awake disk once. Returns {by-id: celsius} for what answered.

    `is_awake` is passed in rather than probed here so the monitor can answer
    it from the power state it already sampled this tick - asking the drive
    again would double the cost of the very thing being kept cheap.
    """
    readings: Dict[str, int] = {}
    for disk in disks:
        if not is_awake(disk):
            continue
        celsius = read_temperature(disk["path"])
        if celsius is not None:
            readings[disk["by_id"]] = celsius
    return readings
