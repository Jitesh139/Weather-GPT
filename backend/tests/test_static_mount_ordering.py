"""Regression test for a real bug found while adding the /voice/* routes:
main.py used to mount StaticFiles at "/" (for serving the frontend)
BEFORE defining /health, /query, etc. Starlette resolves routes in
registration order, and a Mount at "/" matches every path by prefix -
so inside the actual Docker deployment (where the frontend directory
exists and the mount activates), every API route would have been
silently shadowed and returned 404, never reaching this project's own
handlers. This never showed up in local sandbox testing because
/frontend doesn't exist outside the container.

Fixed by moving the mount to the very end of main.py. This test proves
the ordering matters using the same Starlette primitives, independent
of Docker.
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient


def _build_app(mount_first: bool) -> FastAPI:
    app = FastAPI()

    def add_mount():
        app.mount("/", StaticFiles(directory=".", html=True), name="frontend")

    def add_routes():
        @app.get("/health")
        def health():
            return {"status": "ok"}

    if mount_first:
        add_mount()
        add_routes()
    else:
        add_routes()
        add_mount()

    return app


def test_mount_before_routes_shadows_api_routes():
    """Demonstrates the bug: mounting first breaks /health."""
    app = _build_app(mount_first=True)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 404


def test_mount_after_routes_does_not_shadow_api_routes():
    """Confirms the fix: mounting last (as main.py now does) leaves
    /health reachable.
    """
    app = _build_app(mount_first=False)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
