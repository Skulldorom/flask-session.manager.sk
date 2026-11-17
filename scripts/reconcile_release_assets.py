"""Classify verified assets on an existing draft release for safe retry."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def missing_release_assets(manifest: dict, release_dir: Path) -> list[str]:
    expected = manifest["artifacts"]
    allowed = set(expected) | {"release-manifest.json"}
    present = {path.name for path in release_dir.iterdir() if path.is_file()}
    unexpected = present - allowed
    if unexpected:
        raise ValueError(f"unexpected GitHub Release assets: {sorted(unexpected)}")

    manifest_path = release_dir / "release-manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError("existing release manifest conflicts with candidate")

    for name, expected_hash in expected.items():
        path = release_dir / name
        if path.exists():
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError(f"existing release asset conflicts: {name}")

    return sorted(allowed - present)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--missing-output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    missing = missing_release_assets(manifest, args.release_dir)
    args.missing_output.write_text("".join(f"{name}\n" for name in missing))
    print(f"Missing draft assets: {len(missing)}")


if __name__ == "__main__":
    main()
