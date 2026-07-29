from fastapi import APIRouter
from pydantic import BaseModel

from .. import fsops

router = APIRouter(prefix="/fs", tags=["fs"])


class MkdirRequest(BaseModel):
    parent: str
    name: str
    dataset: bool = False


@router.get("/list")
def list_dirs(path: str = "/"):
    return fsops.list_dirs(path)


@router.post("/mkdir")
def mkdir(body: MkdirRequest):
    return {"path": fsops.make_dir(body.parent, body.name, body.dataset)}
