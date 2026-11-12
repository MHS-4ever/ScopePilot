from fastapi.testclient import TestClient


def test_review_ui_route_serves_buildless_application(client: TestClient) -> None:
    response = client.get("/review")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "ScopePilot Review" in response.text
    assert 'id="review-key"' in response.text
    assert 'href="/review/assets/styles.css"' in response.text
    assert 'src="/review/assets/app.js"' in response.text
    assert "https://" not in response.text


def test_review_ui_assets_enforce_memory_only_key_and_safe_rendering(
    client: TestClient,
) -> None:
    css = client.get("/review/assets/styles.css")
    javascript = client.get("/review/assets/app.js")

    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert 'headers.set("X-Review-Key", state.key)' in javascript.text
    assert "localStorage" not in javascript.text
    assert "sessionStorage" not in javascript.text
    assert "innerHTML" not in javascript.text
    assert "textContent" in javascript.text
    assert 'url.searchParams.set("brief", briefId)' in javascript.text
    assert 'searchParams.set("key"' not in javascript.text
    assert "/api/v1/review-queue" in javascript.text
    assert "/review/finalize" in javascript.text
    assert "/review/publish" in javascript.text
    assert "Publish approved tasks" in javascript.text
