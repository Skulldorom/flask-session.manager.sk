"""Wheel and sdist metadata validation tests for release artifacts."""

import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from scripts.validate_release_artifacts import build_manifest

SOURCE_SHA = "a" * 40
VERSION = "1.3.3"
SOURCE_URL = (
    "https://github.com/Skulldorom/flask-session.manager.sk/commit/" + SOURCE_SHA
)


def metadata(version=VERSION, source_url=SOURCE_URL):
    return (
        "Metadata-Version: 2.3\n"
        "Name: flask-session.manager.sk\n"
        f"Version: {version}\n"
        f"Project-URL: Release Source, {source_url}\n"
    ).encode()


def artifact_pair(directory: Path, *, wheel_version=VERSION, sdist_version=VERSION):
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / f"flask_session_manager_sk-{wheel_version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"flask_session_manager_sk-{wheel_version}.dist-info/METADATA",
            metadata(wheel_version),
        )

    sdist = directory / f"flask_session_manager_sk-{sdist_version}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        payload = metadata(sdist_version)
        member = tarfile.TarInfo(f"flask_session_manager_sk-{sdist_version}/PKG-INFO")
        member.size = len(payload)
        archive.addfile(member, BytesIO(payload))
    return wheel, sdist


def test_wheel_and_sdist_metadata_are_bound_to_version_and_source(tmp_path):
    artifact_pair(tmp_path)
    manifest = build_manifest(tmp_path, source_sha=SOURCE_SHA, version=VERSION)

    assert manifest["source_sha"] == SOURCE_SHA
    assert manifest["version"] == VERSION
    assert len(manifest["artifacts"]) == 2


def test_mismatched_or_missing_package_metadata_blocks_release(tmp_path):
    artifact_pair(tmp_path, wheel_version="1.3.2", sdist_version=VERSION)

    with pytest.raises(ValueError, match="expected version"):
        build_manifest(tmp_path, source_sha=SOURCE_SHA, version=VERSION)


def test_source_commit_mismatch_blocks_release(tmp_path):
    artifact_pair(tmp_path)
    with pytest.raises(ValueError, match="source SHA"):
        build_manifest(tmp_path, source_sha="b" * 40, version=VERSION)


def test_partial_or_unexpected_release_build_blocks_release(tmp_path):
    wheel, _sdist = artifact_pair(tmp_path)
    wheel.unlink()

    with pytest.raises(ValueError, match="exactly one wheel and one sdist"):
        build_manifest(tmp_path, source_sha=SOURCE_SHA, version=VERSION)
