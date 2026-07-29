"""The frontend is served straight out of the app process by StaticFiles, so
its caching and content types are backend behaviour and belong in these tests.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_html_must_be_revalidated(client, path):
    """Without Cache-Control a browser may serve index.html from cache without
    asking, so an upgrade that changes which scripts it loads leaves the page
    requesting files that are gone."""
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_html_still_answers_304(client):
    """no-cache means revalidate, not "don't store": the ETag must still save
    the transfer."""
    first = client.get("/index.html")
    again = client.get("/index.html", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["cache-control"] == "no-cache"


def test_scripts_are_served_as_javascript(client):
    """A browser refuses to run a module script unless the MIME type is a
    JavaScript one, and StaticFiles falls back to text/plain for extensions it
    does not know."""
    for path in ("/main.js", "/core/api.js", "/samba/shares.js", "/storage/pools.js"):
        content_type = client.get(path).headers["content-type"]
        assert content_type.split(";")[0] in (
            "text/javascript", "application/javascript"
        ), f"{path} served as {content_type}"


def test_other_assets_are_not_forced_to_revalidate(client):
    """Only the HTML entry point is special-cased; hashless assets keep the
    default handling."""
    assert "cache-control" not in client.get("/styles.css").headers
