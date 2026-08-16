"""The failed-login throttling policy.

Exercised directly rather than through the endpoint: the interesting part is
how the counters grow and expire over time, and driving that through HTTP
would need either a real wait or a patched clock in the request path. Every
call here passes `now` explicitly, so the whole schedule runs in no time at
all.
"""

import pytest

from app.core import loginguard
from app.core.loginguard import FIRST_LOCKOUT, FORGET_AFTER, FREE_ATTEMPTS, MAX_LOCKOUT

IP = "192.0.2.10"
OTHER_IP = "192.0.2.11"


@pytest.fixture(autouse=True)
def clean():
    """The counters are module state, so a leftover lockout from one test
    would silently decide the next one."""
    loginguard.reset()
    yield
    loginguard.reset()


def fail(times: int, now: float = 0.0, ip: str = IP, user: str = "root") -> int:
    last = 0
    for _ in range(times):
        last = loginguard.record_failure(ip, user, now=now)
    return last


def test_the_first_attempts_are_free():
    for i in range(1, FREE_ATTEMPTS + 1):
        assert loginguard.record_failure(IP, "root", now=0) == 0, f"attempt {i}"
    assert loginguard.retry_after(IP, "root", now=0) == 0


def test_the_first_attempt_past_the_allowance_locks_out():
    assert fail(FREE_ATTEMPTS + 1) == FIRST_LOCKOUT
    assert loginguard.retry_after(IP, "root", now=0) > 0


def test_the_lockout_doubles():
    fail(FREE_ATTEMPTS)
    assert [loginguard.record_failure(IP, "root", now=0) for _ in range(4)] == [
        FIRST_LOCKOUT, FIRST_LOCKOUT * 2, FIRST_LOCKOUT * 4, FIRST_LOCKOUT * 8,
    ]


def test_the_lockout_is_capped():
    assert fail(FREE_ATTEMPTS + 20) == MAX_LOCKOUT


def test_the_lockout_expires():
    fail(FREE_ATTEMPTS + 1)
    assert loginguard.retry_after(IP, "root", now=FIRST_LOCKOUT - 1) > 0
    assert loginguard.retry_after(IP, "root", now=FIRST_LOCKOUT + 1) == 0


def test_the_remaining_wait_counts_down():
    fail(FREE_ATTEMPTS + 3, now=0)  # 4 * FIRST_LOCKOUT
    full = loginguard.retry_after(IP, "root", now=0)
    assert full > loginguard.retry_after(IP, "root", now=10) > 0


def test_a_success_clears_the_counters():
    fail(FREE_ATTEMPTS)
    loginguard.record_success(IP, "root", now=0)
    # Back to the full allowance rather than one attempt away from a lockout.
    assert loginguard.record_failure(IP, "root", now=0) == 0


def test_spreading_the_guesses_over_usernames_still_locks_out():
    """The per-address counter exists exactly for this: without it, a name
    per attempt would keep every (address, username) pair inside its own
    allowance forever."""
    for i in range(FREE_ATTEMPTS + 1):
        loginguard.record_failure(IP, f"user{i}", now=0)
    assert loginguard.retry_after(IP, "someone-new", now=0) > 0


def test_one_address_does_not_lock_out_another():
    fail(FREE_ATTEMPTS + 5, ip=IP)
    assert loginguard.retry_after(OTHER_IP, "root", now=0) == 0


def test_old_failures_are_forgotten():
    fail(FREE_ATTEMPTS, now=0)
    later = FORGET_AFTER + 1
    assert loginguard.record_failure(IP, "root", now=later) == 0


def test_forgetting_drops_the_bookkeeping():
    """Not just the penalty - the dict must not keep an entry per address
    that ever typed a password wrong."""
    fail(1, now=0)
    loginguard.retry_after(OTHER_IP, "root", now=FORGET_AFTER + 1)
    assert loginguard._failures == {}
