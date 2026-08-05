"""The disk power-state event log.

A small SQLite database rather than a file, because the whole point of the
log is being able to ask it questions afterwards - which disk, when, and
because of what - with filtering and paging, over months of rows. sqlite3 is
in the standard library, so this adds no dependency.

It lives on the system disk, never on a managed one: writing the log must
never be the thing that wakes a disk up.
"""

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# What happened.
SLEEP = "sleep"
WAKE = "wake"
SLEEP_FAILED = "sleep_failed"
# Temperature crossings are events too, so "when did this disk overheat" is
# answered by the same searchable log as everything else.
TEMP_HIGH = "temp_high"
TEMP_NORMAL = "temp_normal"
EVENTS = (SLEEP, WAKE, SLEEP_FAILED, TEMP_HIGH, TEMP_NORMAL)

# Why it happened.
TIMEOUT = "timeout"        # the idle timer expired and we spun it down
MANUAL = "manual"          # somebody pressed the button
EXTERNAL = "external"      # it changed state without us asking
REASONS = (TIMEOUT, MANUAL, EXTERNAL)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id      INTEGER PRIMARY KEY,
  ts      INTEGER NOT NULL,
  disk    TEXT    NOT NULL,
  event   TEXT    NOT NULL,
  reason  TEXT    NOT NULL,
  actor   TEXT,
  detail  TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_disk_ts ON events(disk, ts);

CREATE TABLE IF NOT EXISTS temperatures (
  ts      INTEGER NOT NULL,
  disk    TEXT    NOT NULL,
  celsius INTEGER NOT NULL,
  PRIMARY KEY (disk, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS temperatures_ts ON temperatures(ts);
"""

# Serialises this process's writers against each other. SQLite would handle
# them anyway, but the monitor thread and a request thread hitting the same
# file makes "database is locked" a question of timing rather than of code,
# and a lock is cheaper to reason about than a retry loop.
_write_lock = threading.Lock()


def db_path() -> Path:
    configured = os.environ.get("PNAS_LOG_DB")
    if configured:
        return Path(configured)
    return Path("/var/lib/proxmox-nas-gui/disk-events.db")


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL so the monitor can append while a request is paging through the
    # table, instead of the two blocking each other.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def record(
    disk: str,
    event: str,
    reason: str,
    actor: Optional[str] = None,
    detail: Optional[str] = None,
    ts: Optional[int] = None,
) -> None:
    with _write_lock:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO events (ts, disk, event, reason, actor, detail)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (int(ts if ts is not None else time.time()),
                 disk, event, reason, actor, detail),
            )
            conn.commit()
        finally:
            conn.close()


def query(
    disk: Optional[str] = None,
    event: Optional[str] = None,
    reason: Optional[str] = None,
    since: Optional[int] = None,
    until: Optional[int] = None,
    text: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
) -> Tuple[List[dict], int]:
    """Filtered, paged rows plus the total the filter matches.

    The total is what lets the UI say "1-25 of 3412" rather than only
    offering a Next button that might do nothing.
    """
    where, params = [], []
    if disk:
        where.append("disk = ?")
        params.append(disk)
    if event:
        where.append("event = ?")
        params.append(event)
    if reason:
        where.append("reason = ?")
        params.append(reason)
    if since is not None:
        where.append("ts >= ?")
        params.append(int(since))
    if until is not None:
        where.append("ts <= ?")
        params.append(int(until))
    if text:
        where.append("(detail LIKE ? OR actor LIKE ? OR disk LIKE ?)")
        pattern = f"%{text}%"
        params.extend([pattern, pattern, pattern])
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    conn = connect()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM events{clause}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM events{clause} ORDER BY ts DESC, id DESC"
            " LIMIT ? OFFSET ?",
            [*params, max(1, min(limit, 200)), max(0, offset)],
        ).fetchall()
        return [dict(row) for row in rows], total
    finally:
        conn.close()


def disks_seen() -> List[str]:
    """Every disk the log has ever mentioned, for the filter dropdown.

    Includes disks that are no longer installed on purpose: looking up what a
    disk did before it was pulled is exactly the sort of question the log
    exists to answer.
    """
    conn = connect()
    try:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT disk FROM events ORDER BY disk"
        ).fetchall()]
    finally:
        conn.close()


def last_state_before(disk: str, ts: int) -> Optional[str]:
    """Whether `disk` was asleep or awake just before `ts`, if the log knows.

    Used to give the timeline a starting colour. Returns None when the log
    does not reach back that far, which the UI renders as "unknown" rather
    than guessing.
    """
    conn = connect()
    try:
        row = conn.execute(
            "SELECT event FROM events WHERE disk = ? AND ts < ? AND event IN (?, ?)"
            " ORDER BY ts DESC, id DESC LIMIT 1",
            (disk, int(ts), SLEEP, WAKE),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return "asleep" if row["event"] == SLEEP else "awake"


def timeline(disk: str, since: int, until: int, current: Optional[str] = None) -> List[dict]:
    """The asleep/awake segments of `disk` between two timestamps.

    Built from the transitions rather than from samples, so a day of history
    costs a handful of rows instead of 2880 of them.
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT ts, event FROM events WHERE disk = ? AND ts >= ? AND ts <= ?"
            " AND event IN (?, ?) ORDER BY ts ASC, id ASC",
            (disk, int(since), int(until), SLEEP, WAKE),
        ).fetchall()
    finally:
        conn.close()

    state = last_state_before(disk, since)
    segments: List[dict] = []
    cursor = int(since)
    for row in rows:
        at = int(row["ts"])
        if at > cursor:
            segments.append({"state": state or "unknown", "start": cursor, "end": at})
        state = "asleep" if row["event"] == SLEEP else "awake"
        cursor = at
    if state is None:
        state = current or "unknown"
    if cursor < until:
        segments.append({"state": state, "start": cursor, "end": int(until)})
    return segments


def summary(since: int, until: int, timelines: Dict[str, List[dict]]) -> Dict[str, int]:
    """Headline numbers for the page: asleep seconds and wake-ups today.

    Takes the timelines the caller already built rather than rebuilding them.
    Each one costs two queries, and the page needs one per disk anyway.
    """
    asleep = sum(
        segment["end"] - segment["start"]
        for segments in timelines.values()
        for segment in segments
        if segment["state"] == "asleep"
    )
    disks = list(timelines)
    conn = connect()
    try:
        placeholders = ",".join("?" * len(disks)) or "''"
        wakes = conn.execute(
            f"SELECT COUNT(*) FROM events WHERE event = ? AND ts >= ? AND ts <= ?"
            f" AND disk IN ({placeholders})",
            [WAKE, int(since), int(until), *disks],
        ).fetchone()[0] if disks else 0
    finally:
        conn.close()
    return {"asleep_seconds": asleep, "wake_count": wakes}


def prune(days: int) -> int:
    """Drop rows older than `days`. Returns how many went."""
    cutoff = int(time.time()) - days * 86400
    with _write_lock:
        conn = connect()
        try:
            cursor = conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()


# --- temperatures -----------------------------------------------------------
#
# A separate table rather than more event rows: these are samples on a fixed
# cadence, not things that happened, and they are read as a series rather than
# as a list. Keeping them apart means the event log stays small enough to page
# through by hand, and lets the two have different retention.

def record_temperature(disk: str, celsius: int, ts: Optional[int] = None) -> None:
    """Store one reading. Only ever called with a real measurement.

    INSERT OR REPLACE, so a monitor restart that samples twice within the same
    second overwrites rather than failing on the primary key.
    """
    with _write_lock:
        conn = connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO temperatures (ts, disk, celsius)"
                " VALUES (?, ?, ?)",
                (int(ts if ts is not None else time.time()), disk, int(celsius)),
            )
            conn.commit()
        finally:
            conn.close()


def record_temperatures(readings: Dict[str, int], ts: Optional[int] = None) -> None:
    """A whole sampling round in one transaction."""
    if not readings:
        return
    at = int(ts if ts is not None else time.time())
    with _write_lock:
        conn = connect()
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO temperatures (ts, disk, celsius)"
                " VALUES (?, ?, ?)",
                [(at, disk, int(c)) for disk, c in readings.items()],
            )
            conn.commit()
        finally:
            conn.close()


def latest_temperatures() -> Dict[str, dict]:
    """The most recent reading per disk, with its timestamp.

    The timestamp is the point: a sleeping disk keeps its last known
    temperature, and "22 C, three hours ago" is far more use than a dash.
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT disk, ts, celsius FROM temperatures t WHERE ts ="
            " (SELECT MAX(ts) FROM temperatures WHERE disk = t.disk)"
        ).fetchall()
    finally:
        conn.close()
    return {r["disk"]: {"celsius": r["celsius"], "ts": r["ts"]} for r in rows}


def temperature_series(
    since: int, until: int, bucket: int, disks: Optional[List[str]] = None
) -> Dict[str, List[list]]:
    """{disk: [[timestamp, celsius], ...]}, averaged into buckets.

    A year of five-minute samples is 105k points per disk; averaging in SQL
    keeps that off the wire and out of the browser. The bucket timestamp is
    its left edge, so points land on clock boundaries.

    Gaps are preserved rather than filled. A stretch with no samples is a
    stretch where the disk was asleep, and interpolating across it would draw
    a temperature the disk never had.
    """
    where = ["ts >= ?", "ts <= ?"]
    params: list = [int(since), int(until)]
    if disks:
        where.append(f"disk IN ({','.join('?' * len(disks))})")
        params.extend(disks)
    bucket = max(1, int(bucket))

    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT disk, (ts / {bucket}) * {bucket} AS bucket_ts,"
            f" AVG(celsius) AS avg_c FROM temperatures"
            f" WHERE {' AND '.join(where)}"
            f" GROUP BY disk, bucket_ts ORDER BY disk, bucket_ts",
            params,
        ).fetchall()
    finally:
        conn.close()

    series: Dict[str, List[list]] = {}
    for row in rows:
        series.setdefault(row["disk"], []).append(
            [int(row["bucket_ts"]), round(row["avg_c"], 1)]
        )
    return series


def temperature_stats(
    since: int, until: int, disks: Optional[List[str]] = None
) -> Dict[str, dict]:
    """Min, average, max and sample count per disk over a window."""
    where = ["ts >= ?", "ts <= ?"]
    params: list = [int(since), int(until)]
    if disks:
        where.append(f"disk IN ({','.join('?' * len(disks))})")
        params.extend(disks)

    conn = connect()
    try:
        rows = conn.execute(
            "SELECT disk, MIN(celsius) AS lo, MAX(celsius) AS hi,"
            " AVG(celsius) AS avg_c, COUNT(*) AS n FROM temperatures"
            f" WHERE {' AND '.join(where)} GROUP BY disk ORDER BY disk",
            params,
        ).fetchall()
    finally:
        conn.close()
    return {
        r["disk"]: {"min": r["lo"], "max": r["hi"],
                    "avg": round(r["avg_c"], 1), "samples": r["n"]}
        for r in rows
    }


def temperature_rows(since: int, until: int, disks: Optional[List[str]] = None):
    """Raw samples, oldest first - what the CSV export writes.

    Deliberately not bucketed: an export exists so the data can be taken
    somewhere else and analysed there, which the averaging would foreclose.
    """
    where = ["ts >= ?", "ts <= ?"]
    params: list = [int(since), int(until)]
    if disks:
        where.append(f"disk IN ({','.join('?' * len(disks))})")
        params.extend(disks)
    conn = connect()
    try:
        return [
            (r["ts"], r["disk"], r["celsius"])
            for r in conn.execute(
                f"SELECT ts, disk, celsius FROM temperatures"
                f" WHERE {' AND '.join(where)} ORDER BY ts, disk",
                params,
            ).fetchall()
        ]
    finally:
        conn.close()


def prune_temperatures(days: int) -> int:
    """Drop readings older than `days`. Separate from the event retention:
    a year of temperatures is a few megabytes and makes summer comparable
    with winter, while a year of event rows is just a long list."""
    cutoff = int(time.time()) - days * 86400
    with _write_lock:
        conn = connect()
        try:
            cursor = conn.execute("DELETE FROM temperatures WHERE ts < ?", (cutoff,))
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()
