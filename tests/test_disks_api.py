"""Wiring tests for the disk format/mount endpoints.

Filesystem/subprocess-touching pools.* functions are stubbed out here (as
no_systemd stubs _systemctl in test_pools_api.py) so these exercise only the
endpoint's validation and state/fstab persistence, not real mount/mkfs calls.
"""

import json

import pytest

from app.storage import pools as pool_ops

BLANK_DEVICE = {
    "path": "/dev/sdd", "type": "disk", "size": 500107862016, "fstype": "",
    "uuid": "", "mountpoint": None, "model": "Blank", "serial": "S4", "label": "",
    "mountable": False, "formattable": True, "by_id": ["ata-Blank-S4"],
}

FORMATTED_DEVICE = {
    **BLANK_DEVICE, "path": "/dev/sdd1", "type": "part",
    "fstype": "ext4", "uuid": "new-uuid",
}

# An already-partitioned blank partition (e.g. sda1) - no partition table to
# create, formatting happens directly on the given path.
BLANK_PARTITION = {
    "path": "/dev/sda1", "type": "part", "size": 990, "fstype": "", "uuid": "",
    "mountpoint": None, "model": "WD Red", "serial": "", "label": "data1",
    "mountable": False, "formattable": True, "by_id": [],
}

FORMATTED_PARTITION = {**BLANK_PARTITION, "fstype": "ext4", "uuid": "new-uuid-2"}

MOUNTABLE_DEVICE = {
    "path": "/dev/sda1", "type": "part", "size": 990, "fstype": "ext4",
    "uuid": "u-sda1", "mountpoint": None, "model": "WD Red", "serial": "",
    "label": "data1", "mountable": True, "formattable": False, "by_id": [],
}


@pytest.fixture
def no_systemd(monkeypatch):
    monkeypatch.setattr(pool_ops, "_systemctl", lambda *args: True)


@pytest.fixture
def stub_mount(monkeypatch):
    """Avoid the real `mount` subprocess call; fstab writing still runs
    against the sandboxed PNAS_FSTAB path."""
    monkeypatch.setattr(pool_ops, "mount_disk", lambda mountpoint: None)


def test_format_rejects_unknown_path(auth_client, sandbox, no_systemd, monkeypatch):
    monkeypatch.setattr(pool_ops, "list_block_devices", lambda: [])
    response = auth_client.post(
        "/api/disks/format",
        json={"path": "/dev/sdz", "fstype": "ext4", "name": "d1"},
    )
    assert response.status_code == 404


def test_format_rejects_non_formattable_device(
    auth_client, sandbox, no_systemd, monkeypatch
):
    monkeypatch.setattr(pool_ops, "list_block_devices", lambda: [MOUNTABLE_DEVICE])
    response = auth_client.post(
        "/api/disks/format",
        json={"path": "/dev/sda1", "fstype": "ext4", "name": "d1"},
    )
    assert response.status_code == 409


def test_format_rejects_unsupported_fstype(auth_client, sandbox, no_systemd):
    response = auth_client.post(
        "/api/disks/format",
        json={"path": "/dev/sdd", "fstype": "btrfs", "name": "d1"},
    )
    assert response.status_code == 400


def test_format_rejects_duplicate_mount_name(
    auth_client, sandbox, no_systemd, monkeypatch
):
    (sandbox["PNAS_STATE_DIR"] / "state.json").write_text(
        '{"version": 1, "shares": {}, "pools": {}, '
        '"disk_mounts": {"d1": {"uuid": "abcd-1234", "fstype": "ext4", '
        '"mountpoint": "/mnt/disks/d1"}}}'
    )
    response = auth_client.post(
        "/api/disks/format",
        json={"path": "/dev/sdd", "fstype": "ext4", "name": "d1"},
    )
    assert response.status_code == 409


def test_format_of_a_whole_disk_partitions_first(
    auth_client, sandbox, no_systemd, stub_mount, monkeypatch
):
    """type: "disk" (no partition table, e.g. sdd) must be partitioned
    before formatting, and the mount must end up on the new partition, not
    the raw disk - so the disk stays usable elsewhere if it's ever moved."""
    format_calls = []
    partition_calls = []
    monkeypatch.setattr(
        pool_ops, "format_device",
        lambda path, fstype: format_calls.append((path, fstype)),
    )
    monkeypatch.setattr(
        pool_ops, "partition_whole_disk",
        lambda path: (partition_calls.append(path), "/dev/sdd1")[1],
    )
    devices = {"before": True}

    def list_devices():
        if devices["before"]:
            devices["before"] = False
            return [BLANK_DEVICE]
        return [FORMATTED_DEVICE]

    monkeypatch.setattr(pool_ops, "list_block_devices", list_devices)

    response = auth_client.post(
        "/api/disks/format",
        json={"path": "/dev/sdd", "fstype": "ext4", "name": "d1"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "mountpoint": "/mnt/disks/d1"}
    assert partition_calls == ["/dev/sdd"]
    assert format_calls == [("/dev/sdd1", "ext4")]
    fstab = sandbox["PNAS_FSTAB"].read_text()
    assert "UUID=new-uuid /mnt/disks/d1 ext4" in fstab
    assert "# pnas:disk:d1" in fstab


def test_format_of_an_existing_partition_skips_partitioning(
    auth_client, sandbox, no_systemd, stub_mount, monkeypatch
):
    """type: "part" (already has a partition, just blank, e.g. sda1) must
    format in place - there is nothing to partition."""
    format_calls = []
    monkeypatch.setattr(
        pool_ops, "format_device",
        lambda path, fstype: format_calls.append((path, fstype)),
    )
    monkeypatch.setattr(
        pool_ops, "partition_whole_disk",
        lambda path: pytest.fail("partition_whole_disk must not be called for a part"),
    )
    devices = {"before": True}

    def list_devices():
        if devices["before"]:
            devices["before"] = False
            return [BLANK_PARTITION]
        return [FORMATTED_PARTITION]

    monkeypatch.setattr(pool_ops, "list_block_devices", list_devices)

    response = auth_client.post(
        "/api/disks/format",
        json={"path": "/dev/sda1", "fstype": "ext4", "name": "d1"},
    )

    assert response.status_code == 200
    assert format_calls == [("/dev/sda1", "ext4")]
    fstab = sandbox["PNAS_FSTAB"].read_text()
    assert "UUID=new-uuid-2 /mnt/disks/d1 ext4" in fstab


def test_list_block_devices_forces_tree_output(monkeypatch):
    """Without --tree=PATH, lsblk silently returns a flat list (no nested
    "children") because our -o column list omits NAME - which breaks both
    the has-children parent/partition split and the system-disk exclusion
    in parse_lsblk. Confirmed against a real Proxmox host: the same command
    without this flag listed /dev/sdc (the system disk) as formattable."""
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return '{"blockdevices": []}'

    monkeypatch.setattr(pool_ops, "run", fake_run)
    pool_ops.list_block_devices()

    assert "--tree=PATH" in captured[0]


def test_partition_whole_disk_creates_single_gpt_partition(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("input_text")))
        if cmd[0] == "lsblk":
            return json.dumps({"blockdevices": [{
                "path": "/dev/sdd", "type": "disk", "size": 500107862016,
                "fstype": None, "uuid": None, "mountpoint": None,
                "model": "Blank", "serial": "S4", "label": None,
                "children": [{
                    "path": "/dev/sdd1", "type": "part", "size": 500106813440,
                    "fstype": None, "uuid": None, "mountpoint": None,
                    "model": None, "serial": None, "label": None,
                }],
            }]})
        return ""

    monkeypatch.setattr(pool_ops, "run", fake_run)

    result = pool_ops.partition_whole_disk("/dev/sdd")

    assert result == "/dev/sdd1"
    cmds = [c for c, _ in calls]
    assert cmds[0] == ["wipefs", "-a", "/dev/sdd"]
    assert cmds[1][0] == "sfdisk"
    assert calls[1][1] == "label: gpt\n,,L\n"
    assert cmds[2][:2] == ["udevadm", "settle"]


def test_partition_whole_disk_rejects_unexpected_partition_count(monkeypatch):
    """If sfdisk didn't produce exactly one new partition (e.g. the disk
    already had leftover partitions sfdisk appended to), formatting the
    wrong thing would be worse than failing loudly."""

    def fake_run(cmd, **kwargs):
        if cmd[0] == "lsblk":
            return json.dumps({"blockdevices": [{
                "path": "/dev/sdd", "type": "disk", "size": 1000,
                "fstype": None, "uuid": None, "mountpoint": None,
                "model": None, "serial": None, "label": None,
            }]})
        return ""

    monkeypatch.setattr(pool_ops, "run", fake_run)

    with pytest.raises(pool_ops.SystemOpError):
        pool_ops.partition_whole_disk("/dev/sdd")
