"""The pure half of the bind mount feature.

The generated unit is the only thing standing between a missing filesystem
and an empty Samba share, so its exact content is asserted here rather than
left to an integration test that would need a real mount to notice.
"""

import pytest
from pydantic import ValidationError

from app.storage import bindconf
from app.storage.models import BindMount, Pool

BIND = BindMount(
    name="kz-fontos", source="/mnt/fontos/kz", target="/mnt/family_pool/kz/fontos"
)


def unit(bind=BIND, source_mount="/mnt/fontos", pool_name=None) -> list[str]:
    return bindconf.bind_unit(bind, source_mount, pool_name).splitlines()


def test_unit_name_is_derived_from_the_bind_name():
    assert bindconf.bind_unit_name("kz-fontos") == "pnas-bind-kz-fontos.service"


def test_the_unit_refuses_to_mount_when_the_source_filesystem_is_missing():
    """The guard the whole design hangs on: without it a bind onto a
    not-yet-mounted ZFS dataset succeeds against the empty directory
    underneath, and Samba serves that emptiness."""
    assert "ExecStartPre=/usr/bin/mountpoint -q /mnt/fontos" in unit()


def test_the_unit_checks_the_source_directory_too():
    assert "ExecStartPre=/usr/bin/test -d /mnt/fontos/kz" in unit()


def test_the_unit_creates_the_target_and_binds():
    lines = unit()
    assert "ExecStartPre=/bin/mkdir -p /mnt/family_pool/kz/fontos" in lines
    assert (
        "ExecStart=/bin/mount --bind /mnt/fontos/kz /mnt/family_pool/kz/fontos"
        in lines
    )
    assert "ExecStop=/bin/umount /mnt/family_pool/kz/fontos" in lines


def test_a_plain_source_is_ordered_after_its_mount():
    assert "RequiresMountsFor=/mnt/fontos" in unit()


def test_a_pool_source_is_ordered_after_the_pool_service_instead():
    """A mergerfs pool is mounted by a service, so there is no .mount unit for
    RequiresMountsFor to wait on - naming the service is the only ordering
    that actually holds at boot."""
    lines = unit(source_mount="/mnt/pool/bulk", pool_name="bulk")
    assert "Requires=pnas-pool-bulk.service" in lines
    assert "After=pnas-pool-bulk.service" in lines
    assert not [line for line in lines if line.startswith("RequiresMountsFor=")]


def test_a_source_on_the_root_filesystem_gets_no_guard():
    """Nothing to wait for and nothing to check: / is always mounted, and a
    mountpoint guard on it would pass unconditionally anyway."""
    lines = unit(source_mount="/")
    assert not [line for line in lines if line.startswith("RequiresMountsFor=")]
    assert not [line for line in lines if "mountpoint" in line]


def test_read_only_needs_a_second_mount_call():
    """A bind inherits the source's options; ro has to be remounted on, and
    "bind" must be repeated or the remount hits the underlying filesystem."""
    ro = BindMount(name="ro", source="/srv/a", target="/srv/b", read_only=True)
    assert "ExecStart=/bin/mount -o remount,bind,ro /srv/b" in unit(ro)
    assert "remount" not in bindconf.bind_unit(BIND, "/mnt/fontos")


def test_the_unit_is_marked_as_generated():
    assert unit()[0] == "# Managed by proxmox-nas-gui - DO NOT EDIT."


def test_the_unit_is_enabled_for_boot():
    assert "WantedBy=multi-user.target" in unit()


POOLS = {
    "bulk": Pool(name="bulk", mountpoint="/mnt/pool/bulk", branches=[{"path": "/mnt/disks/d1"}]),
    "inner": Pool(name="inner", mountpoint="/mnt/pool/bulk/inner", branches=[{"path": "/mnt/disks/d2"}]),
}


def test_pool_for_path_finds_the_containing_pool():
    assert bindconf.pool_for_path(POOLS, "/mnt/pool/bulk/kz") == "bulk"


def test_pool_for_path_matches_the_mountpoint_itself():
    assert bindconf.pool_for_path(POOLS, "/mnt/pool/bulk") == "bulk"


def test_pool_for_path_prefers_the_innermost_pool():
    assert bindconf.pool_for_path(POOLS, "/mnt/pool/bulk/inner/kz") == "inner"


def test_pool_for_path_ignores_a_shared_prefix():
    assert bindconf.pool_for_path(POOLS, "/mnt/pool/bulk2/kz") is None


def test_pool_for_path_outside_every_pool():
    assert bindconf.pool_for_path(POOLS, "/mnt/fontos/kz") is None


def test_generate_tree_builds_the_users_times_tiers_grid():
    """The scenario the feature was written for: one browsable tree, two
    physically separate tiers behind it."""
    generated = bindconf.generate_tree(
        "/mnt/family_pool",
        ["kz", "kzs", "kv"],
        [("fontos", "/mnt/fontos"), ("nemfontos", "/mnt/bulk")],
    )
    assert [(b.source, b.target) for b in generated] == [
        ("/mnt/fontos/kz", "/mnt/family_pool/kz/fontos"),
        ("/mnt/bulk/kz", "/mnt/family_pool/kz/nemfontos"),
        ("/mnt/fontos/kzs", "/mnt/family_pool/kzs/fontos"),
        ("/mnt/bulk/kzs", "/mnt/family_pool/kzs/nemfontos"),
        ("/mnt/fontos/kv", "/mnt/family_pool/kv/fontos"),
        ("/mnt/bulk/kv", "/mnt/family_pool/kv/nemfontos"),
    ]
    assert [b.name for b in generated][:2] == ["kz-fontos", "kz-nemfontos"]


def test_generate_tree_tolerates_trailing_slashes():
    generated = bindconf.generate_tree(
        "/mnt/family_pool/", ["kz"], [("fontos", "/mnt/fontos/")]
    )
    assert generated[0].source == "/mnt/fontos/kz"
    assert generated[0].target == "/mnt/family_pool/kz/fontos"


def test_a_bind_into_itself_is_rejected():
    with pytest.raises(ValidationError):
        BindMount(name="loop", source="/mnt/a", target="/mnt/a/inner")


def test_a_path_with_a_systemd_specifier_is_rejected():
    """% starts a specifier expansion in a unit file, so it must never reach
    an ExecStart= line unescaped."""
    with pytest.raises(ValidationError):
        BindMount(name="x", source="/mnt/a%n", target="/mnt/b")


def test_the_root_directory_is_not_a_valid_endpoint():
    with pytest.raises(ValidationError):
        BindMount(name="x", source="/", target="/mnt/b")
