import pytest

from app.storage.models import Branch, BranchMode, DiskMount, Pool
from app.storage.poolconf import (
    disk_fstab_line,
    mergerfs_options,
    parse_lsblk,
    parse_tag,
    pool_unit,
    pool_unit_name,
    remove_line,
    upsert_line,
)


def make_pool(**kwargs) -> Pool:
    defaults = dict(
        name="media",
        mountpoint="/mnt/pool/media",
        branches=[Branch(path="/mnt/disks/d1"), Branch(path="/mnt/disks/d2")],
    )
    defaults.update(kwargs)
    return Pool(**defaults)


def test_options_defaults():
    opts = mergerfs_options(make_pool())
    assert "allow_other" in opts
    assert "category.create=mfs" in opts
    assert "minfreespace=4G" in opts
    assert "moveonenospc=true" in opts
    assert "fsname=media" in opts
    # Generic fstab options would be rejected by the mergerfs 2.33
    # mount helper, so they must never appear in the option string.
    assert "nofail" not in opts
    assert "x-systemd" not in opts


def test_options_extra_appended():
    pool = make_pool(extra_options="func.getattr=newest,security_capability=false")
    assert mergerfs_options(pool).endswith(
        "func.getattr=newest,security_capability=false"
    )


def test_extra_options_rejects_managed_keys():
    for bad in ("fsname=x", "branches=/a:/b", "nofail", "has space=1"):
        with pytest.raises(ValueError):
            make_pool(extra_options=bad)


def test_pool_unit_branch_modes():
    pool = make_pool(branches=[
        Branch(path="/mnt/disks/d1", mode=BranchMode.RW),
        Branch(path="/mnt/disks/d2", mode=BranchMode.RO),
        Branch(path="/mnt/disks/d3", mode=BranchMode.NC),
    ])
    unit = pool_unit(pool)
    assert pool_unit_name("media") == "pnas-pool-media.service"
    assert "RequiresMountsFor=/mnt/disks/d1 /mnt/disks/d2 /mnt/disks/d3" in unit
    assert (
        "ExecStart=/usr/bin/mergerfs -o " + mergerfs_options(pool)
        + " /mnt/disks/d1=RW:/mnt/disks/d2=RO:/mnt/disks/d3=NC /mnt/pool/media"
    ) in unit
    assert "ExecStop=/bin/umount /mnt/pool/media" in unit
    assert "WantedBy=multi-user.target" in unit


def test_disk_fstab_line():
    disk = DiskMount(uuid="abcd-1234", fstype="ext4", mountpoint="/mnt/disks/d1")
    line = disk_fstab_line("d1", disk)
    assert line == (
        "UUID=abcd-1234 /mnt/disks/d1 ext4 defaults,nofail 0 2 # pnas:disk:d1"
    )


def test_nesting_rejected():
    with pytest.raises(ValueError):
        make_pool(branches=[Branch(path="/mnt/pool/media/sub")])
    with pytest.raises(ValueError):
        make_pool(mountpoint="/mnt/disks/d1/pool",
                  branches=[Branch(path="/mnt/disks/d1")])
    with pytest.raises(ValueError):
        make_pool(branches=[Branch(path="/mnt/disks/d1"),
                            Branch(path="/mnt/disks/d1")])


def test_path_with_space_or_colon_rejected():
    with pytest.raises(ValueError):
        Branch(path="/mnt/my disk")
    with pytest.raises(ValueError):
        Branch(path="/mnt/a:b")


def test_minfreespace_validated():
    make_pool(minfreespace="250G")
    with pytest.raises(ValueError):
        make_pool(minfreespace="lots")


def test_tag_parse():
    assert parse_tag("x y z 0 0 # pnas:pool:media") == ("pool", "media")
    assert parse_tag("UUID=1 /m ext4 defaults 0 2 # pnas:disk:d1") == ("disk", "d1")
    assert parse_tag("UUID=1 /m ext4 defaults 0 2") is None


FSTAB = """# /etc/fstab: static file system information.
UUID=root-uuid / ext4 errors=remount-ro 0 1
UUID=abcd /mnt/disks/d1 ext4 defaults,nofail 0 2 # pnas:disk:d1
"""


def test_upsert_replaces_tagged_line():
    out = upsert_line(FSTAB, "disk", "d1", "NEWLINE # pnas:disk:d1")
    assert "UUID=abcd" not in out
    assert "NEWLINE # pnas:disk:d1" in out
    assert "UUID=root-uuid" in out
    assert out.count("pnas:disk:d1") == 1


def test_upsert_appends_new_line():
    out = upsert_line(FSTAB, "pool", "media", "POOLLINE # pnas:pool:media")
    assert out.endswith("POOLLINE # pnas:pool:media\n")
    assert "UUID=abcd" in out


def test_upsert_idempotent():
    once = upsert_line(FSTAB, "pool", "media", "L # pnas:pool:media")
    twice = upsert_line(once, "pool", "media", "L # pnas:pool:media")
    assert once == twice


def test_remove_line_only_touches_tagged():
    out = remove_line(FSTAB, "disk", "d1")
    assert "UUID=abcd" not in out
    assert "UUID=root-uuid" in out
    assert remove_line(out, "disk", "d1") == out


LSBLK = """{
  "blockdevices": [
    {"path": "/dev/sda", "type": "disk", "size": 1000, "fstype": null,
     "uuid": null, "mountpoint": null, "model": "WD Red", "serial": "S1",
     "label": null, "children": [
       {"path": "/dev/sda1", "type": "part", "size": 990, "fstype": "ext4",
        "uuid": "u-sda1", "mountpoint": null, "model": null, "serial": null,
        "label": "data1"}
     ]},
    {"path": "/dev/sdb", "type": "disk", "size": 500, "fstype": "xfs",
     "uuid": "u-sdb", "mountpoint": "/mnt/disks/old", "model": "Seagate",
     "serial": "S2", "label": null},
    {"path": "/dev/sdc1", "type": "part", "size": 100, "fstype": "swap",
     "uuid": "u-swap", "mountpoint": null, "model": null, "serial": null,
     "label": null},
    {"path": "/dev/sdd", "type": "disk", "size": 500107862016, "fstype": null,
     "uuid": null, "mountpoint": null, "model": "Blank", "serial": "S4",
     "label": null},
    {"path": "/dev/sde1", "type": "part", "size": 990, "fstype": null,
     "uuid": null, "mountpoint": null, "model": null, "serial": null,
     "label": null}
  ]
}"""


def test_parse_lsblk():
    devices = {d["path"]: d for d in parse_lsblk(LSBLK)}
    assert "/dev/sda" not in devices          # no fs, has children
    sda1 = devices["/dev/sda1"]
    assert sda1["mountable"] is True
    assert sda1["formattable"] is False
    assert sda1["model"] == "WD Red"          # inherited from parent disk
    assert sda1["label"] == "data1"
    assert devices["/dev/sdb"]["mountable"] is False   # already mounted
    assert devices["/dev/sdb"]["formattable"] is False
    assert devices["/dev/sdc1"]["mountable"] is False  # swap excluded
    assert devices["/dev/sdc1"]["formattable"] is False  # has a filesystem
    # A blank whole disk with no partition table is a format candidate.
    assert devices["/dev/sdd"]["mountable"] is False
    assert devices["/dev/sdd"]["formattable"] is True
    # A blank partition (e.g. never formatted after creation) too.
    assert devices["/dev/sde1"]["formattable"] is True


def test_parse_lsblk_garbage():
    assert parse_lsblk("not json") == []


PROXMOX_LSBLK = """{
  "blockdevices": [
    {"path": "/dev/sdc", "type": "disk", "size": 512110190592, "fstype": null,
     "uuid": null, "mountpoint": null, "model": "Apacer", "serial": "S3",
     "label": null, "children": [
       {"path": "/dev/sdc1", "type": "part", "size": 1048576, "fstype": null,
        "uuid": null, "mountpoint": null, "model": null, "serial": null,
        "label": null},
       {"path": "/dev/sdc2", "type": "part", "size": 1073741824, "fstype": "vfat",
        "uuid": "boot-uuid", "mountpoint": "/boot/efi", "model": null,
        "serial": null, "label": null},
       {"path": "/dev/sdc3", "type": "part", "size": 500000000000,
        "fstype": "LVM2_member", "uuid": "pv-uuid", "mountpoint": null,
        "model": null, "serial": null, "label": null, "children": [
          {"path": "/dev/mapper/pve-root", "type": "lvm", "size": 74000000000,
           "fstype": "ext4", "uuid": "root-uuid", "mountpoint": "/",
           "model": null, "serial": null, "label": null}
        ]}
     ]},
    {"path": "/dev/sdd", "type": "disk", "size": 500107862016, "fstype": null,
     "uuid": null, "mountpoint": null, "model": "Blank", "serial": "S4",
     "label": null}
  ]
}"""


def test_parse_lsblk_excludes_whole_system_disk():
    devices = {d["path"]: d for d in parse_lsblk(PROXMOX_LSBLK)}
    assert not any(p.startswith("/dev/sdc") for p in devices)
    assert "/dev/mapper/pve-root" not in devices
    assert devices["/dev/sdd"]["formattable"] is True
