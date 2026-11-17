"""Resolve a GitHub tag ref to its commit without following arbitrary objects."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_TAG_OBJECTS = 10


def _object(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Git ref has no valid target object")
    object_type = value.get("type")
    sha = value.get("sha")
    if (
        object_type not in {"commit", "tag"}
        or not isinstance(sha, str)
        or not SHA.fullmatch(sha)
    ):
        raise ValueError("Git ref target has an invalid type or SHA")
    return object_type, sha


def resolve_commit(ref: dict[str, Any], *, tag_name: str, repository: str) -> str:
    if ref.get("ref") != f"refs/tags/{tag_name}":
        raise ValueError("GitHub returned a different tag ref")

    object_type, sha = _object(ref.get("object"))
    seen: set[str] = set()
    for _ in range(MAX_TAG_OBJECTS):
        if object_type == "commit":
            return sha
        if sha in seen:
            raise ValueError("GitHub tag objects contain a cycle")
        seen.add(sha)
        result = subprocess.run(
            ["gh", "api", f"repos/{repository}/git/tags/{sha}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise ValueError("GitHub tag object lookup failed")
        try:
            tag_object = json.loads(result.stdout)
            object_type, sha = _object(tag_object.get("object"))
        except (json.JSONDecodeError, AttributeError) as error:
            raise ValueError("GitHub returned a malformed tag object") from error
    raise ValueError("GitHub tag object chain is too deep")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref-file", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    try:
        ref = json.loads(args.ref_file.read_text())
        if not isinstance(ref, dict):
            raise ValueError("GitHub returned a malformed tag ref")
        print(resolve_commit(ref, tag_name=args.tag, repository=args.repository))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
