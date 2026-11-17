"""Verify downloaded GitHub Release assets match the built release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.release_policy import validate_release_artifact_identity


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-manifest", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    expected = json.loads(args.expected_manifest.read_text())
    actual_manifest_path = args.release_dir / "release-manifest.json"
    actual = json.loads(actual_manifest_path.read_text())
    validate_release_artifact_identity(
        source_sha=actual.get("source_sha", ""),
        expected_source_sha=args.source_sha,
        manifest_version=actual.get("version", ""),
        expected_version=args.version,
    )
    if expected != actual:
        raise SystemExit("GitHub Release manifest differs from the candidate build")
    actual_files = {path.name for path in args.release_dir.iterdir() if path.is_file()}
    expected_files = set(expected["artifacts"]) | {"release-manifest.json"}
    if actual_files != expected_files:
        raise SystemExit(
            "GitHub Release asset set is incomplete or contains unexpected files"
        )
    for name, digest in expected["artifacts"].items():
        actual_digest = hashlib.sha256(
            (args.release_dir / name).read_bytes()
        ).hexdigest()
        if actual_digest != digest:
            raise SystemExit(f"GitHub Release asset hash conflict: {name}")
    print(f"Verified immutable GitHub Release assets for v{args.version}.")


if __name__ == "__main__":
    main()
