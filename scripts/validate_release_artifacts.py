"""Validate a wheel and sdist and record their immutable SHA-256 identities."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path


def metadata_version(path: Path) -> tuple[str, str, str | None]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            metadata_paths = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_paths) != 1:
                raise ValueError(
                    f"{path.name}: expected exactly one wheel METADATA file"
                )
            metadata = archive.read(metadata_paths[0]).decode("utf-8")
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            metadata_paths = [
                member
                for member in archive.getmembers()
                if member.name.endswith("/PKG-INFO")
            ]
            if len(metadata_paths) != 1:
                raise ValueError(
                    f"{path.name}: expected exactly one sdist PKG-INFO file"
                )
            stream = archive.extractfile(metadata_paths[0])
            if stream is None:
                raise ValueError(f"{path.name}: could not read sdist PKG-INFO")
            metadata = stream.read().decode("utf-8")
    else:
        raise ValueError(f"unexpected release artifact: {path.name}")

    fields = {}
    release_source = None
    for line in metadata.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            if key in {"Name", "Version"}:
                fields[key] = value
            elif key == "Project-URL" and value.startswith("Release Source, "):
                release_source = value.removeprefix("Release Source, ")
    if set(fields) != {"Name", "Version"}:
        raise ValueError(f"{path.name}: missing Name or Version metadata")
    return fields["Name"], fields["Version"], release_source


def build_manifest(dist: Path, *, source_sha: str, version: str) -> dict[str, object]:
    files = sorted(
        path
        for path in dist.iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    unexpected = [
        path.name
        for path in dist.iterdir()
        if path.is_file()
        and path.name != ".gitignore"
        and path.suffix != ".whl"
        and not path.name.endswith(".tar.gz")
    ]
    if unexpected:
        raise ValueError(
            f"unexpected files in release directory: {', '.join(unexpected)}"
        )
    wheels = [path for path in files if path.suffix == ".whl"]
    sdists = [path for path in files if path.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1 or len(files) != 2:
        raise ValueError("release must contain exactly one wheel and one sdist")
    artifacts = {}
    for path in files:
        name, built_version, release_source = metadata_version(path)
        if re.sub(r"[-_.]+", "-", name).lower() != "flask-session-manager-sk":
            raise ValueError(f"{path.name}: unexpected package name {name!r}")
        if built_version != version:
            raise ValueError(
                f"{path.name}: expected version {version}, got {built_version}"
            )
        expected_source = f"https://github.com/Skulldorom/flask-session.manager.sk/commit/{source_sha}"
        if release_source != expected_source:
            raise ValueError(
                f"{path.name}: embedded source SHA does not match the authorized source"
            )
        artifacts[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"source_sha": source_sha, "version": version, "artifacts": artifacts}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(
        args.dist, source_sha=args.source_sha, version=args.version
    )
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Validated {len(manifest['artifacts'])} release files for {args.version}.")


if __name__ == "__main__":
    main()
