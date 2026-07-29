import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .api import api_router
from .core.proc import SystemOpError

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


class Frontend(StaticFiles):
    """StaticFiles that forces revalidation of the HTML entry point.

    StaticFiles sends an ETag but no Cache-Control, so browsers fall back to
    heuristic freshness and may serve index.html from cache without asking.
    After an upgrade that renames or splits the scripts index.html references,
    a stale copy requests files that no longer exist and the page comes up
    blank until the user hard-refreshes. "no-cache" means revalidate, not
    "don't store" - the ETag still turns unchanged requests into a 304.
    """

    def file_response(self, full_path, stat_result, scope, status_code=200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).endswith(".html"):
            response.headers["cache-control"] = "no-cache"
        return response


app.mount("/", Frontend(directory=frontend_dir(), html=True), name="frontend")
