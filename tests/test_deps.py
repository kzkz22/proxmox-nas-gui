"""Tests for the rule that stops a pool being deleted or unmounted while a
Samba share still points inside it.

This is the single cross-cutting invariant between the Samba and the mergerfs
half of the application, and getting it wrong means pulling a filesystem out
from under a live share, so it is tested on its own.
"""

from app.models import Pool, Share, State
from app.pools import dependent_shares


def make_state(*paths: str) -> State:
    st = State()
    for i, path in enumerate(paths):
        name = f"share{i}"
        st.shares[name] = Share(name=name, path=path)
    return st


def test_empty_state_has_no_dependents():
    assert dependent_shares(State(), "/mnt/pool/media") == []


def test_share_exactly_on_the_mountpoint():
    st = make_state("/mnt/pool/media")
    assert dependent_shares(st, "/mnt/pool/media") == ["share0"]


def test_share_below_the_mountpoint():
    st = make_state("/mnt/pool/media/movies")
    assert dependent_shares(st, "/mnt/pool/media") == ["share0"]


def test_sibling_with_a_common_prefix_is_not_a_dependent():
    """/mnt/pool/media2 merely starts with the same characters as
    /mnt/pool/media - it is a different directory and must not match."""
    st = make_state("/mnt/pool/media2", "/mnt/pool/mediaX/sub")
    assert dependent_shares(st, "/mnt/pool/media") == []


def test_unrelated_paths_are_not_dependents():
    st = make_state("/srv/data", "/mnt/disks/d1")
    assert dependent_shares(st, "/mnt/pool/media") == []


def test_trailing_slash_on_the_mountpoint_is_ignored():
    st = make_state("/mnt/pool/media", "/mnt/pool/media/movies")
    assert dependent_shares(st, "/mnt/pool/media/") == ["share0", "share1"]


def test_multiple_dependents_are_sorted():
    st = State()
    for name in ("zulu", "alpha", "mike"):
        st.shares[name] = Share(name=name, path=f"/mnt/pool/media/{name}")
    assert dependent_shares(st, "/mnt/pool/media") == ["alpha", "mike", "zulu"]


def test_pool_own_branches_are_irrelevant():
    """A branch living under the mountpoint would be rejected by the Pool
    validator anyway; only shares count as dependents."""
    st = make_state("/srv/other")
    st.pools["media"] = Pool(
        name="media",
        mountpoint="/mnt/pool/media",
        branches=[{"path": "/mnt/disks/d1"}],
    )
    assert dependent_shares(st, "/mnt/pool/media") == []
