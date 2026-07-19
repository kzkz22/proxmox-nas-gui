import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import poolconf, pools, state as state_store
from ..models import DiskMount

router = APIRouter(prefix="/disks", tags=["disks"])

MOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,30}$")


class DiskMountRequest(BaseModel):
    uuid: str
    name: str


@router.get("")
def list_disks():
    st = state_store.load_state()
    return {
        "devices": pools.list_block_devices(),
        "mounts": {
            name: {
                **dm.model_dump(),
                "mounted": pools.is_mounted(dm.mountpoint),
                "usage": pools.usage(dm.mountpoint)
                if pools.is_mounted(dm.mountpoint) else None,
                "used_by_pools": pools.pools_using_path(st, dm.mountpoint),
            }
            for name, dm in st.disk_mounts.items()
        },
    }


@router.post("/mount")
def mount_disk(body: DiskMountRequest):
    if not MOUNT_NAME_RE.match(body.name):
        raise HTTPException(400, "invalid mount name")
    with state_store.lock:
        st = state_store.load_state()
        if body.name in st.disk_mounts:
            raise HTTPException(409, "mount name already exists")
        device = next(
            (d for d in pools.list_block_devices() if d["uuid"] == body.uuid), None
        )
        if not device:
            raise HTTPException(404, "no device with this uuid")
        if not device["mountable"]:
            raise HTTPException(409, "device is not mountable (in use or unsupported)")
        dm = DiskMount(
            uuid=body.uuid, fstype=device["fstype"],
            mountpoint=f"/mnt/disks/{body.name}",
        )
        fstab = poolconf.upsert_line(
            pools.read_fstab(), "disk", body.name,
            poolconf.disk_fstab_line(body.name, dm),
        )
        pools.write_fstab(fstab)
        pools.mount_disk(dm.mountpoint)
        st.disk_mounts[body.name] = dm
        state_store.save_state(st)
        return {"ok": True, "mountpoint": dm.mountpoint}


@router.delete("/mount/{name}")
def unmount_disk(name: str):
    with state_store.lock:
        st = state_store.load_state()
        dm = st.disk_mounts.get(name)
        if not dm:
            raise HTTPException(404, "no such disk mount")
        used_by = pools.pools_using_path(st, dm.mountpoint)
        if used_by:
            raise HTTPException(
                409, "disk is a branch of pool(s): " + ", ".join(used_by)
            )
        pools.unmount_disk(dm.mountpoint)
        pools.write_fstab(poolconf.remove_line(pools.read_fstab(), "disk", name))
        del st.disk_mounts[name]
        state_store.save_state(st)
        return {"ok": True}
