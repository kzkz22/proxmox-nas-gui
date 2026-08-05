"""Tests for the rule that stops a pool being deleted or unmounted while a
Samba share still points inside it.

This is the single cross-cutting invariant between the Samba and the mergerfs
half of the application, and getting it wrong means pulling a filesystem out
from under a live share, so it is tested on its own.
"""

from app.core.deps import blockers_for_path, shares_containing_path
from app.models import State
from app.samba.models import Share
from app.storage.models import BindMount, Pool


def make_state(*paths: str) -> State:
    st = State()
    for i, path in enumerate(paths):
        name = f"share{i}"
        st.shares[name] = Share(name=name, path=path)
    return st


def test_empty_state_has_no_dependents():
    assert blockers_for_path(State(), "/mnt/pool/media") == []


def test_share_exactly_on_the_mountpoint():
    st = make_state("/mnt/pool/media")
    assert blockers_for_path(st, "/mnt/pool/media") == ["share0"]


def test_share_below_the_mountpoint():
    st = make_state("/mnt/pool/media/movies")
    assert blockers_for_path(st, "/mnt/pool/media") == ["share0"]


def test_sibling_with_a_common_prefix_is_not_a_dependent():
    """/mnt/pool/media2 merely starts with the same characters as
    /mnt/pool/media - it is a different directory and must not match."""
    st = make_state("/mnt/pool/media2", "/mnt/pool/mediaX/sub")
    assert blockers_for_path(st, "/mnt/pool/media") == []


def test_unrelated_paths_are_not_dependents():
    st = make_state("/srv/data", "/mnt/disks/d1")
    assert blockers_for_path(st, "/mnt/pool/media") == []


def test_trailing_slash_on_the_mountpoint_is_ignored():
    st = make_state("/mnt/pool/media", "/mnt/pool/media/movies")
    assert blockers_for_path(st, "/mnt/pool/media/") == ["share0", "share1"]


def test_multiple_dependents_are_sorted():
    st = State()
    for name in ("zulu", "alpha", "mike"):
        st.shares[name] = Share(name=name, path=f"/mnt/pool/media/{name}")
    assert blockers_for_path(st, "/mnt/pool/media") == ["alpha", "mike", "zulu"]


def bind(source: str, target: str) -> BindMount:
    return BindMount(name="b", source=source, target=target)


def test_a_share_reaching_the_path_through_a_bind_mount_counts():
    """The point of a bind mount is that the share never names the storage it
    actually sits on, so a purely direct check would clear a pool for deletion
    while a live share was being served from it."""
    st = make_state("/mnt/family_pool/kz/fontos")
    st.bind_mounts["b"] = bind("/mnt/pool/media/kz", "/mnt/family_pool/kz/fontos")
    assert blockers_for_path(st, "/mnt/pool/media") == ["share0"]


def test_a_share_below_the_bind_target_counts_too():
    st = make_state("/mnt/family_pool/kz/fontos/photos")
    st.bind_mounts["b"] = bind("/mnt/pool/media/kz", "/mnt/family_pool/kz/fontos")
    assert blockers_for_path(st, "/mnt/pool/media") == ["share0"]


def test_a_path_inside_the_bind_source_maps_to_the_matching_subtree():
    """Removing /mnt/pool/media/kz/photos empties exactly the photos folder of
    the presentation tree, not the whole bind."""
    st = make_state("/mnt/family_pool/kz/fontos/photos")
    st.bind_mounts["b"] = bind("/mnt/pool/media/kz", "/mnt/family_pool/kz/fontos")
    assert blockers_for_path(st, "/mnt/pool/media/kz/photos") == ["share0"]
    assert blockers_for_path(st, "/mnt/pool/media/kz/other") == []


def test_an_unrelated_bind_mount_does_not_drag_shares_in():
    st = make_state("/mnt/family_pool/kz/fontos")
    st.bind_mounts["b"] = bind("/mnt/elsewhere/kz", "/mnt/family_pool/kz/fontos")
    assert blockers_for_path(st, "/mnt/pool/media") == []


def test_shares_containing_a_path_are_not_blockers():
    st = make_state("/mnt/family_pool")
    assert blockers_for_path(st, "/mnt/family_pool/kz/fontos") == []
    assert shares_containing_path(st, "/mnt/family_pool/kz/fontos") == ["share0"]


def test_a_share_exactly_on_the_path_is_not_reported_as_containing_it():
    """Otherwise it would be both refused and warned about."""
    st = make_state("/mnt/family_pool/kz/fontos")
    assert shares_containing_path(st, "/mnt/family_pool/kz/fontos") == []


def test_pool_own_branches_are_irrelevant():
    """A branch living under the mountpoint would be rejected by the Pool
    validator anyway; only shares count as dependents."""
    st = make_state("/srv/other")
    st.pools["media"] = Pool(
        name="media",
        mountpoint="/mnt/pool/media",
        branches=[{"path": "/mnt/disks/d1"}],
    )
    assert blockers_for_path(st, "/mnt/pool/media") == []
