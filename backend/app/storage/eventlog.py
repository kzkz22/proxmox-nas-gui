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
EVENTS = (SLEEP, WAKE, SLEEP_FAILED)

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
