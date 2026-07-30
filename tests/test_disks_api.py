"""Wiring tests for the disk format/mount endpoints.

Filesystem/subprocess-touching pools.* functions are stubbed out here (as
no_systemd stubs _systemctl in test_pools_api.py) so these exercise only the
endpoint's validation and state/fstab persistence, not real mount/mkfs calls.
"""

import pytest

from app.storage import pools as pool_ops

BLANK_DEVICE = {
    "path": "/dev/sdd", "size": 500107862016, "fstype": "", "uuid": "",
    "mountpoint": None, "model": "Blank", "serial": "S4", "label": "",
    "mountable": False, "formattable": True, "by_id": ["ata-Blank-S4"],
}

FORMATTED_DEVICE = {**BLANK_DEVICE, "fstype": "ext4", "uuid": "new-uuid"}

MOUNTABLE_DEVICE = {
    "path": "/dev/sda1", "size": 990, "fstype": "ext4", "uuid": "u-sda1",
    "mountpoint": None, "model": "WD Red", "serial": "", "label": "data1",
    "mountable": True, "formattable": False, "by_id": [],
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


def test_format_success_persists_mount_and_fstab(
    auth_client, sandbox, no_systemd, stub_mount, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        pool_ops, "list_block_devices", lambda: [BLANK_DEVICE, FORMATTED_DEVICE]
    )
    monkeypatch.setattr(
        pool_ops, "format_device", lambda path, fstype: calls.append((path, fstype))
    )
    # First call (pre-check) sees the blank device, second (post-format
    # re-query) sees it as formatted - simulate that by having the same list
    # already contain both; the endpoint looks up by identical path each
    # time so give it the formatted entry consistently after the format call.
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
    assert calls == [("/dev/sdd", "ext4")]
    fstab = sandbox["PNAS_FSTAB"].read_text()
    assert "UUID=new-uuid /mnt/disks/d1 ext4" in fstab
    assert "# pnas:disk:d1" in fstab


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
