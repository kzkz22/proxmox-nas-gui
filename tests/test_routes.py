"""The API surface, pinned.

The routers are assembled from three packages in app/routes.py. A router
dropped during a refactor removes endpoints silently, and a router included
outside the authenticated wrapper publishes them without a session check -
neither shows up in the other tests, so both are asserted here.
"""

import pytest

from app.main import app

# Every endpoint the frontend calls. Update deliberately, not to make a
# failure go away.
EXPECTED_ROUTES = {
    ("POST", "/api/login"),
    ("POST", "/api/logout"),
    ("GET", "/api/session"),
    ("GET", "/api/state"),
    ("GET", "/api/fs/list"),
    ("POST", "/api/fs/mkdir"),
    ("GET", "/api/shares"),
    ("POST", "/api/shares"),
    ("PUT", "/api/shares/{name}"),
    ("DELETE", "/api/shares/{name}"),
    ("GET", "/api/shares/{name}/recycle"),
    ("POST", "/api/shares/{name}/recycle/empty"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("PUT", "/api/users/{name}"),
    ("DELETE", "/api/users/{name}"),
    ("GET", "/api/groups"),
    ("POST", "/api/groups"),
    ("PUT", "/api/groups/{name}"),
    ("DELETE", "/api/groups/{name}"),
    ("PUT", "/api/settings"),
    ("POST", "/api/service/restart"),
    ("GET", "/api/pools"),
    ("POST", "/api/pools"),
    ("PUT", "/api/pools/{name}"),
    ("DELETE", "/api/pools/{name}"),
    ("POST", "/api/pools/{name}/mount"),
    ("POST", "/api/pools/{name}/unmount"),
    ("GET", "/api/disks"),
    ("POST", "/api/disks/mount"),
    ("DELETE", "/api/disks/mount/{name}"),
}

# Reachable without a session, by design.
PUBLIC_ROUTES = {
    ("POST", "/api/login"),
    ("POST", "/api/logout"),
}


def collect_routes() -> set[tuple[str, str]]:
    """Walk the app's route tree, following the wrappers include_router
    leaves behind, and return every (method, path) pair."""
    from fastapi.routing import APIRoute

    found: set[tuple[str, str]] = set()

    def walk(node, prefix=""):
        routes = getattr(node, "routes", None)
        if routes is None:
            original = getattr(node, "original_router", None)
            if original is not None:
                context = getattr(node, "include_context", None)
                walk(original, prefix + (getattr(context, "prefix", "") or ""))
            return
        for route in routes:
            if isinstance(route, APIRoute):
                for method in route.methods:
                    found.add((method, prefix + route.path))
            else:
                walk(route, prefix)

    walk(app)
    return found


def test_api_surface_is_unchanged():
    assert collect_routes() == EXPECTED_ROUTES


@pytest.mark.parametrize(
    "method,path", sorted(EXPECTED_ROUTES - PUBLIC_ROUTES)
)
def test_every_endpoint_requires_a_session(client, method, path, sandbox):
    """Asserted against the running app rather than by inspecting
    dependencies: include_router applies the session check as a wrapper, so it
    is not visible on the route objects themselves."""
    concrete = path.replace("{name}", "nonexistent")
    response = client.request(method, concrete)
    assert response.status_code == 401, (
        f"{method} {path} answered {response.status_code} without a session"
    )


def test_the_session_endpoint_accepts_a_valid_session(auth_client):
    response = auth_client.get("/api/session")
    assert response.status_code == 200
    assert response.json() == {"user": "root"}
