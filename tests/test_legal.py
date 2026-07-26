"""Legal pages API tests."""

from fastapi.testclient import TestClient

from blockhost_backend.main import create_app


def test_legal_pages_list() -> None:
    client = TestClient(create_app())
    res = client.get("/legal")
    assert res.status_code == 200
    pages = res.json()
    slugs = {p["slug"] for p in pages}
    assert {"terms", "privacy", "refunds"}.issubset(slugs)


def test_legal_page_html() -> None:
    client = TestClient(create_app())
    res = client.get("/legal/terms/html")
    assert res.status_code == 200
    assert "Terms of Service" in res.text
