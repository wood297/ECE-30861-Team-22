"""Tests for bundle generation (REQ-05, REQ-08): terminal status, the zip archive and download_url.

Uses the shared fixtures: prd-88 v1.2 is complete, v1.3 has Test Logs missing and v1.4 has an
empty SBOM (see test_runner/oracle.json).
"""
import io
import json
import zipfile
from pathlib import Path

import pytest
from werkzeug.test import Client

import bundle_service
from app import app, bundles
from bundle_service import final_status

FIXTURES = Path(__file__).parent.parent / "fixtures" / "prd-88"


@pytest.fixture(autouse=True)
def clean_state():
    bundles.clear()
    bundle_service.archives.clear()


@pytest.fixture
def client():
    return app.test_client()


def create(client, release, product="prd-88"):
    response = client.post("/bundles", json={"product_id": product, "release_version": release})
    assert response.status_code == 202
    return client.get(f"/bundles/{response.get_json()['bundle_id']}").get_json()


def download(client, body):
    response = client.get(body["download_url"])
    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    return zipfile.ZipFile(io.BytesIO(response.data))


@pytest.mark.parametrize("statuses, expected", [
    (["PRESENT", "PRESENT"], "COMPLETED"),
    (["PRESENT", "MISSING"], "PARTIAL_SUCCESS"),
    (["PRESENT", "INVALID"], "PARTIAL_SUCCESS"),
    (["PRESENT", "INACCESSIBLE"], "PARTIAL_SUCCESS"),
    (["MISSING", "MISSING"], "PARTIAL_SUCCESS"),
])
def test_final_status(statuses, expected):
    assert final_status([{"category": str(i), "status": s} for i, s in enumerate(statuses)]) == expected


@pytest.mark.parametrize("release, expected", [
    ("v1.2", "COMPLETED"),
    ("v1.3", "PARTIAL_SUCCESS"),
    ("v1.4", "PARTIAL_SUCCESS"),
])
def test_bundle_reaches_terminal_status(client, release, expected):
    assert create(client, release)["status"] == expected


def test_archive_holds_source_files_byte_for_byte(client):
    archive = download(client, create(client, "v1.2"))

    expected = {path.name: path.read_bytes() for path in FIXTURES.joinpath("v1.2").iterdir()}
    assert {name: archive.read(name) for name in archive.namelist() if name != "manifest.json"} == expected


def test_archive_leaves_out_unsatisfied_categories(client):
    assert "test_logs.md" not in download(client, create(client, "v1.3")).namelist()
    assert "sbom.md" not in download(client, create(client, "v1.4")).namelist()


def test_archive_manifest_matches_the_response(client):
    body = create(client, "v1.3")

    manifest = json.loads(download(client, body).read("manifest.json"))

    assert manifest == {key: body[key] for key in ("product_id", "release_version", "status", "manifest")}


def test_identical_requests_give_identical_archives(client):
    first, second = create(client, "v1.2"), create(client, "v1.2")

    assert first["bundle_id"] != second["bundle_id"]
    assert client.get(first["download_url"]).data == client.get(second["download_url"]).data


def test_failed_bundle_has_no_download(client):
    body = create(client, "v0.0-does-not-exist")

    assert body["status"] == "FAILED"
    assert "download_url" not in body
    assert client.get(f"/bundles/{body['bundle_id']}/download").status_code == 404


def test_unknown_bundle_download_is_404(client):
    response = client.get("/bundles/does-not-exist/download")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Bundle archive not found"}


def test_download_url_is_absolute_behind_the_proxy():
    # Production path: Caddy (HTTPS) -> ProxyFix -> /system mount -> app
    from wsgi import application

    client = Client(application)
    headers = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "team22.example"}
    created = client.post("/system/bundles", json={"product_id": "prd-88", "release_version": "v1.2"},
                          headers=headers).get_json()
    body = client.get(f"/system/bundles/{created['bundle_id']}", headers=headers).get_json()

    assert body["download_url"] == f"https://team22.example/system/bundles/{created['bundle_id']}/download"
