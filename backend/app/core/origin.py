"""Origin checking for state-changing requests.

Defence in depth behind the session cookie, not the primary CSRF control. The
cookie is already `SameSite=Lax`, which stops a cross-site page from having
the browser attach it to a POST, and every write in this API is a POST, PUT or
DELETE carrying `Content-Type: application/json` - which is not a CORS simple
request, so a cross-origin attempt needs a preflight that no middleware here
answers. This adds a second, independent reason for such a request to fail, so
that the whole defence does not rest on one cookie attribute staying set.

Deliberately a dependency rather than middleware: routes.py already composes
the API out of `Depends(...)`, and hanging this on the same router keeps the
order visible - the origin check runs before the session check, so a
cross-origin caller is refused without learning whether its cookie was any
good.
"""

import os
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

# Reads are exempt. Nothing in this API changes state on a GET, and the
# same-origin policy already stops a cross-site page from reading a response
# it triggered. Narrowing the check also narrows the blast radius of getting
# it wrong: behind a reverse proxy that rewrites Host, only writes would fail
# rather than the entire UI.
GUARDED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _trusted_origins() -> set:
    """Extra origins to accept, for deployments behind a reverse proxy.

    A proxy that terminates TLS under a different name than the one it
    forwards leaves Origin and Host disagreeing for entirely legitimate
    traffic. Rather than trusting X-Forwarded-Host - which the client can
    also send - the accepted names are configured out of band.
    """
    return {
        origin.strip().rstrip("/")
        for origin in os.environ.get("PNAS_TRUSTED_ORIGINS", "").split(",")
        if origin.strip()
    }


def same_origin(request: Request) -> None:
    if request.method not in GUARDED_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        # Not a browser, or a browser that chose not to send one. Refusing
        # here would break curl and every non-browser client for nothing: a
        # page mounting a CSRF attack cannot suppress the header, so its
        # absence is not a case the attack can arrange.
        return
    origin = origin.rstrip("/")
    if origin in _trusted_origins():
        return
    host = request.headers.get("host")
    # "null" is what a sandboxed iframe or a data: URL sends. It matches no
    # host, and treating it as one would be the one way this check could be
    # talked into passing.
    if origin != "null" and host and urlsplit(origin).netloc == host:
        return
    raise HTTPException(status_code=403, detail="cross-origin request refused")
