"""Select a deterministic release version from immutable source evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from scripts.release_policy import ReleasePolicyError, choose_release_version


def pending_release_for_source(
    releases: list[dict], source_sha: str
) -> dict[str, str] | None:
    marker = f"Release-Source-SHA: {source_sha}"
    matching = [
        release for release in releases if marker in (release.get("body") or "")
    ]
    if len(matching) > 1:
        raise ReleasePolicyError("multiple GitHub releases claim the same source SHA")
    if not matching:
        return None
    release = matching[0]
    tag = release.get("tag_name", "")
    match = re.fullmatch(r"v(\d+\.\d+\.\d+)", tag)
    if not match:
        raise ReleasePolicyError(
            "source-bound GitHub release has an invalid version tag"
        )
    return {"source_sha": source_sha, "version": match.group(1), "tag": tag}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--pypi-json", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--pull-requests", type=Path, required=True)
    parser.add_argument("--releases", type=Path, required=True)
    parser.add_argument("--commit-message-file", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path(os.environ.get("GITHUB_OUTPUT", ""))
    )
    args = parser.parse_args()
    pypi = json.loads(args.pypi_json.read_text())
    latest_version = pypi["info"]["version"]
    source_url = (pypi["info"].get("project_urls") or {}).get("Release Source")
    commit_message = args.commit_message_file.read_text()
    pending = pending_release_for_source(
        json.loads(args.releases.read_text()), args.source_sha
    )
    if "[skip-version]" in commit_message:
        part = "skip"
        version = latest_version
    else:
        version, part = choose_release_version(
            latest_pypi_version=latest_version,
            source_sha=args.source_sha,
            current_pypi_source_url=source_url,
            pending_release=pending,
            associated_pull_requests=json.loads(args.pull_requests.read_text()),
            default_branch=args.branch,
            commit_message=commit_message,
        )
    with args.output.open("a") as output:
        output.write(
            f"version={version}\npart={part}\n"
            f"skip={'true' if part == 'skip' else 'false'}\n"
        )
    print(f"Selected {version} ({part}).")


if __name__ == "__main__":
    main()
