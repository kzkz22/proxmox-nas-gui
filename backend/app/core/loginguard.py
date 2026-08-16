"""Throttling for failed sign-in attempts.

Kept out of auth.py so the policy - how many attempts are free, how fast the
lockout grows, when it is forgotten - can be exercised without PAM, a request
object or a real clock. Nothing here touches the filesystem or the network.

State lives in memory, like the session store next door. That is the same
trade-off the sessions already make: the service runs a single uvicorn worker,
so there is exactly one copy of these counters, and a restart clears them. An
attacker cannot restart the service, and an administrator who can has no need
to guess a password.

Two counters guard every attempt, and the longer of the two waits wins:

  (address, username)  the ordinary case - somebody guessing one account from
                       one machine.
  address              so that spreading the guesses over many usernames does
                       not hand out a fresh allowance each time.

Both are keyed on the peer address the socket reports and never on
X-Forwarded-For: the service is reached directly rather than through a proxy,
and a header the client picks would let an attacker mint a new key per
request - which is worse than no throttling at all, because it would look like
throttling.
"""

import threading
import time
from typing import Dict, Optional, Tuple

# Failures that cost nothing. A wrong password typed a few times is a person,
# not an attack, and the point of the free attempts is that the honest case
# never meets the lockout.
FREE_ATTEMPTS = 5
# The first failure past the free ones waits this long, and every further one
# doubles it: 30s, 1m, 2m, 4m, 8m, then the cap.
FIRST_LOCKOUT = 30
# Capped deliberately low for an administration tool: an hour-long lockout
# punishes the locked-out administrator far more than the guesser, and 15
# minutes already cuts an online guessing rate to a few attempts per hour.
MAX_LOCKOUT = 900
# A counter nobody has added to for this long is dropped, which both forgives
# yesterday's typos and keeps the dict from growing without bound.
FORGET_AFTER = 3600

Key = Tuple[str, ...]

_lock = threading.Lock()
_failures: Dict[Key, dict] = {}


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else now


def _keys(address: str, username: str) -> Tuple[Key, Key]:
    return (address, username), (address,)


def _lockout_for(count: int) -> int:
    if count <= FREE_ATTEMPTS:
        return 0
    return min(FIRST_LOCKOUT * 2 ** (count - FREE_ATTEMPTS - 1), MAX_LOCKOUT)


def _forget_stale(now: float) -> None:
    for key in [k for k, e in _failures.items() if e["seen"] + FORGET_AFTER < now]:
        del _failures[key]


def retry_after(address: str, username: str, now: Optional[float] = None) -> int:
    """Seconds the caller still has to wait; 0 when the attempt may proceed."""
    now = _now(now)
    with _lock:
        _forget_stale(now)
        waits = [
            _failures[key]["until"] - now
            for key in _keys(address, username)
            if key in _failures
        ]
    return max([int(w) + 1 for w in waits if w > 0], default=0)


def record_failure(address: str, username: str, now: Optional[float] = None) -> int:
    """Count one failed attempt and return the lockout it just earned.

    The return value is the wait this failure imposes, which is 0 while the
    attempt is still within the free allowance - the caller logs it, but the
    answer to the request itself stays an ordinary 401 either way, so that a
    guesser learns nothing about which usernames exist.
    """
    now = _now(now)
    longest = 0
    with _lock:
        _forget_stale(now)
        for key in _keys(address, username):
            entry = _failures.setdefault(key, {"count": 0, "until": 0.0, "seen": now})
            entry["count"] += 1
            entry["seen"] = now
            lockout = _lockout_for(entry["count"])
            if lockout:
                entry["until"] = now + lockout
            longest = max(longest, lockout)
    return longest


def record_success(address: str, username: str, now: Optional[float] = None) -> None:
    """Forget both counters after a successful sign-in."""
    with _lock:
        _forget_stale(_now(now))
        for key in _keys(address, username):
            _failures.pop(key, None)


def reset() -> None:
    """Drop all bookkeeping. For the tests, and for nothing else."""
    with _lock:
        _failures.clear()
