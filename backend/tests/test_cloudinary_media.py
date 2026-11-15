"""Path-to-Cloudinary URL mapping. No network."""

from __future__ import annotations

import hashlib

from app.cloudinary_media import (
    delivery_url,
    image_is_servable,
    resolve_media,
    sign_params,
)


def test_sign_params_matches_cloudinary_sha1():
    params = {"timestamp": "1315060510", "public_id": "sample_image"}
    expected = hashlib.sha1(b"public_id=sample_image&timestamp=1315060510abcd").hexdigest()
    assert sign_params(params, "abcd") == expected


def test_delivery_url_encodes_spaces(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    url = delivery_url("images/My Report/p01-f01.png")
    assert url == "https://res.cloudinary.com/demo/image/upload/images/My%20Report/p01-f01.png"


def test_sidecar_json_uses_raw_upload(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    url = delivery_url("images/psi/p01-f01.json")
    assert url == "https://res.cloudinary.com/demo/raw/upload/images/psi/p01-f01.json"


def test_rejects_unsafe_cloud_name_and_traversal(monkeypatch):
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "evil.example/cloud")
    assert delivery_url("images/a.png") is None
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    assert delivery_url("../secret.png") is None
    assert delivery_url("images/../../.env") is None
    assert resolve_media("../.env") is None
    assert resolve_media("chunks/children.json") is None


def test_local_file_wins_over_cloudinary(tmp_path, monkeypatch):
    data = tmp_path / "data"
    image = data / "images" / "shot.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    monkeypatch.setattr("app.cloudinary_media.DATA_ROOT", data.resolve())
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    hit = resolve_media("images/shot.png")
    assert hit is not None and hit.path == image.resolve()
    assert hit.redirect_url is None


def test_missing_file_redirects_when_cloud_name_set(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr("app.cloudinary_media.DATA_ROOT", data.resolve())
    target = data / "images" / "a b" / "p01-f01.png"
    monkeypatch.delenv("CLOUDINARY_CLOUD_NAME", raising=False)
    assert image_is_servable(target) is False
    assert resolve_media("images/a b/p01-f01.png") is None
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    assert image_is_servable(target) is True
    hit = resolve_media("data/images/a b/p01-f01.png")
    assert hit is not None and hit.path is None
    assert hit.redirect_url == (
        "https://res.cloudinary.com/demo/image/upload/images/a%20b/p01-f01.png"
    )
