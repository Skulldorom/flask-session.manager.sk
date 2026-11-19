"""Compare the complete candidate artifact set with immutable PyPI files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts.release_policy import existing_artifact_state


def pypi_artifact_state(manifest: dict, release=None, *, fetch=True) -> str:
    if fetch:
        version_url = (
            f"https://pypi.org/pypi/flask-session.manager.sk/{manifest['version']}/json"
        )
        try:
            with urlopen(version_url, timeout=20) as response:
                release = json.load(response)
        except HTTPError as error:
            if error.code != 404:
                raise
            release = None
    urls = release.get("urls", []) if release is not None else []
    existing = {item["filename"]: item["digests"]["sha256"] for item in urls}
    if len(existing) != len(urls):
        raise ValueError("PyPI returned duplicate filenames")
    return existing_artifact_state(manifest["artifacts"], existing)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--pypi-json",
        type=Path,
        help=("Use a local PyPI version JSON fixture instead of a network request."),
    )
    parser.add_argument(
        "--output", type=Path, default=Path(os.environ.get("GITHUB_OUTPUT", ""))
    )
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    release = json.loads(args.pypi_json.read_text()) if args.pypi_json else None
    state = pypi_artifact_state(manifest, release, fetch=not args.pypi_json)
    with args.output.open("a") as output:
        output.write(f"state={state}\n")
    print(f"PyPI artifact state for {manifest['version']}: {state}")


if __name__ == "__main__":
    main()
