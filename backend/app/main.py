import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import api_router
from .system import SystemOpError

app = FastAPI(title="Proxmox Samba GUI", docs_url=None, redoc_url=None)
app.include_router(api_router)


@app.exception_handler(SystemOpError)
def system_op_error(_request: Request, exc: SystemOpError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def frontend_dir() -> Path:
    configured = os.environ.get("PSG_FRONTEND")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "frontend"


app.mount("/", StaticFiles(directory=frontend_dir(), html=True), name="frontend")
