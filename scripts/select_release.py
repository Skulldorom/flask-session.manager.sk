"""Select a deterministic release version from immutable source evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from scripts.release_policy import (
    ReleasePolicyError,
    bump_version,
    choose_release_version,
)
from scripts.resolve_tag_ref import SHA

VERSION_TAG = re.compile(r"refs/tags/(v(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){2})(\^\{\})?")


def pending_release_for_source(
    releases: list[dict], source_sha: str
) -> dict[str, str] | None:
    marker = f"Release-Source-SHA: {source_sha}"
    matching = [
        release
        for release in releases
        if marker in (release.get("body") or "").splitlines()
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


def tag_targets(raw: str) -> dict[str, str]:
    refs = {}
    peeled = {}
    for line in raw.splitlines():
        sha, separator, ref = line.partition("\t")
        if not separator or not SHA.fullmatch(sha):
            raise ReleasePolicyError("malformed Git tag listing")
        match = VERSION_TAG.fullmatch(ref)
        if match:
            tag, dereferenced = match.groups()
            targets = peeled if dereferenced else refs
            if tag in targets:
                raise ReleasePolicyError(f"duplicate Git tag listing: {tag}")
            targets[tag] = sha
    if peeled.keys() - refs.keys():
        raise ReleasePolicyError("peeled tag is missing its Git ref")
    return refs | peeled


def next_available_version(version, source_sha, releases, tags, pypi_versions):
    """Keep the semantic increment and advance its patch slot past occupied versions."""
    release_tags = {release.get("tag_name") for release in releases}
    while True:
        tag = f"v{version}"
        target = tags.get(tag)
        if target == source_sha and tag in release_tags:
            raise ReleasePolicyError(
                "release metadata conflicts with the current source tag"
            )
        if (
            tag not in release_tags
            and version not in pypi_versions
            and target in (None, source_sha)
        ):
            # An exact-source tag alone can be from an interrupted reservation.
            return version
        version = bump_version(version, "patch")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--pypi-json", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--pull-requests", type=Path, required=True)
    parser.add_argument("--releases", type=Path, required=True)
    parser.add_argument("--tag-refs", type=Path, required=True)
    parser.add_argument("--commit-message-file", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path(os.environ.get("GITHUB_OUTPUT", ""))
    )
    args = parser.parse_args()
    pypi = json.loads(args.pypi_json.read_text())
    latest_version = pypi["info"]["version"]
    source_url = (pypi["info"].get("project_urls") or {}).get("Release Source")
    commit_message = args.commit_message_file.read_text()
    releases = json.loads(args.releases.read_text())
    pending = pending_release_for_source(releases, args.source_sha)
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
        if part in {"patch", "minor", "major"}:
            version = next_available_version(
                version,
                args.source_sha,
                releases,
                tag_targets(args.tag_refs.read_text()),
                pypi.get("releases", {}),
            )
    with args.output.open("a") as output:
        output.write(
            f"version={version}\npart={part}\n"
            f"skip={'true' if part == 'skip' else 'false'}\n"
        )
    print(f"Selected {version} ({part}).")


if __name__ == "__main__":
    main()
