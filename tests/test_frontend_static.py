"""Static files stay consistent with a frontend rebuilt under a running backend."""

from starlette.testclient import TestClient

from twicc.frontend_static import FrontendStaticFiles


async def fallback(scope, receive, send):
    await send({"type": "http.response.start", "status": 418, "headers": []})
    await send({"type": "http.response.body", "body": b"fallback"})


def make_app(root):
    return FrontendStaticFiles(fallback, root)


def build(root, name):
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    (assets / name).write_text(f"// {name}")
    (root / "index.html").write_text(f'<script src="/static/assets/{name}"></script>')


def test_new_build_is_available_without_backend_restart(tmp_path):
    build(tmp_path, "first.js")
    app = make_app(tmp_path)
    client = TestClient(app)
    assert client.get("/static/assets/first.js").status_code == 200
    (tmp_path / "assets/first.js").unlink()
    build(tmp_path, "second.js")
    response = client.get("/static/assets/second.js")
    assert response.status_code == 200
    assert response.text == "// second.js"
    assert "immutable" in response.headers["cache-control"]
    old = client.get("/static/assets/first.js")
    assert old.status_code == 404
    assert old.headers["cache-control"] == "no-store"


def test_missing_asset_never_enters_browser_or_proxy_cache(tmp_path):
    build(tmp_path, "first.js")
    client = TestClient(make_app(tmp_path))
    response = client.get("/static/assets/missing.js")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_static_build_gap_and_recovery(tmp_path):
    build(tmp_path, "first.js")
    client = TestClient(make_app(tmp_path))
    (tmp_path / "index.html").unlink()
    (tmp_path / "assets/first.js").unlink()
    response = client.get("/static/assets/first.js")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    build(tmp_path, "second.js")
    assert client.get("/static/assets/second.js").status_code == 200


def test_non_static_requests_and_head_are_preserved(tmp_path):
    build(tmp_path, "first.js")
    client = TestClient(make_app(tmp_path))
    assert client.get("/api/anything/").status_code == 418
    assert client.get("/static-other/file").status_code == 418
    response = client.head("/static/assets/first.js")
    assert response.status_code == 200
    assert response.content == b""
    assert client.post("/static/assets/first.js").status_code == 405
    assert client.get("/static/assets/%2e%2e/%2e%2e/outside").status_code != 200
