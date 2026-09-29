import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app, bundles


def setup_function():
    bundles.clear()


def test_health():
    client = app.test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_create_bundle():
    client = app.test_client()

    response = client.post("/bundles", json={
        "product_id": "test-product",
        "release_version": "v1.0"
    })

    data = response.get_json()

    assert response.status_code == 202
    assert data["product_id"] == "test-product"
    assert data["release_version"] == "v1.0"
    assert data["status"] == "PENDING"
    assert data["manifest"] == []
    assert data["artifacts"] == []  
    assert "bundle_id" in data


def test_missing_product_id():
    client = app.test_client()

    response = client.post("/bundles", json={
        "release_version": "v1.0"
    })

    assert response.status_code == 400
    assert response.get_json() == {"error": "product_id is required"}


def test_missing_release_version():
    client = app.test_client()

    response = client.post("/bundles", json={
        "product_id": "test-product"
    })

    assert response.status_code == 400
    assert response.get_json() == {"error": "release_version is required"}


def test_get_bundle():
    client = app.test_client()

    create_response = client.post("/bundles", json={
        "product_id": "test-product",
        "release_version": "v1.0"
    })

    bundle_id = create_response.get_json()["bundle_id"]

    response = client.get(f"/bundles/{bundle_id}")

    assert response.status_code == 200
    assert response.get_json()["bundle_id"] == bundle_id


def test_get_nonexistent_bundle():
    client = app.test_client()

    response = client.get("/bundles/does-not-exist")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Bundle not found"}