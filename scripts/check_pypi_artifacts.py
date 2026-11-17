"""Compare the complete candidate artifact set with immutable PyPI files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts.release_policy import existing_artifact_state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path(os.environ.get("GITHUB_OUTPUT", ""))
    )
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
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
    existing = {}
    if release is not None:
        existing = {
            item["filename"]: item["digests"]["sha256"]
            for item in release.get("urls", [])
        }
    state = existing_artifact_state(manifest["artifacts"], existing)
    with args.output.open("a") as output:
        output.write(f"state={state}\n")
    print(f"PyPI artifact state for {manifest['version']}: {state}")


if __name__ == "__main__":
    main()
