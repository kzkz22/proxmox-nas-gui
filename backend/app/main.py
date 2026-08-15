import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .core.proc import SystemOpError
from .routes import api_router
from .storage import monitor


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Owns the disk sleep monitor for as long as the app is up.

    The monitor runs in this process rather than as a second unit: the
    service starts a single uvicorn worker, so there is exactly one loop and
    restarting the service restarts it. Wiring it here rather than inside the
    storage package keeps the process lifecycle in the composition root.
    """
    await monitor.start()
    try:
        yield
    finally:
        await monitor.stop()


app = FastAPI(title="Proxmox NAS GUI", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(api_router)


@app.exception_handler(SystemOpError)
def system_op_error(_request: Request, exc: SystemOpError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def frontend_dir() -> Path:
    configured = os.environ.get("PNAS_FRONTEND")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "frontend"


# Everything the application itself is made of. All of it changes together on
# an upgrade, and none of it is fingerprinted, so all of it has to revalidate.
APP_ASSET_SUFFIXES = (".html", ".js", ".json", ".css")


class Frontend(StaticFiles):
    """StaticFiles that forces revalidation of the application's own assets.

    StaticFiles sends an ETag but no Cache-Control, so browsers fall back to
    heuristic freshness and may serve a file from cache without asking. For
    index.html that means an upgrade which renames or splits the scripts it
    references comes up blank until a hard refresh. For the i18n bundles it is
    quieter and worse: the page renders, but strings added in the upgrade have
    no translation in the cached copy, so the UI shows raw keys like
    "diagfinding.pool_needs_remount.title" - a bug report about missing
    translations that are in fact present on disk.

    "no-cache" means revalidate, not "don't store" - the ETag still turns
    unchanged requests into a 304, so the cost is one conditional request per
    asset per load.
    """

    def file_response(self, full_path, stat_result, scope, status_code=200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).endswith(APP_ASSET_SUFFIXES):
            response.headers["cache-control"] = "no-cache"
        return response


app.mount("/", Frontend(directory=frontend_dir(), html=True), name="frontend")
