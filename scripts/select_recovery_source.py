"""Select current main for normal release; reject any historical draft."""

from __future__ import annotations

import json
import os
from pathlib import Path

from scripts.github_release import GitHub
from scripts.resolve_tag_ref import SHA


def select_source(api, repository, head):
    if not SHA.fullmatch(head):
        raise ValueError("invalid authorized main SHA")
    drafts = [
        release
        for release in api.collection(f"repos/{repository}/releases")
        if release.get("draft") is True
    ]
    if not drafts:
        return head
    if len(drafts) != 1:
        raise ValueError("multiple pending releases; recovery is ambiguous")
    source = drafts[0].get("target_commitish", "")
    if not SHA.fullmatch(source):
        raise ValueError("pending release target is not an immutable source SHA")
    if source != head:
        raise ValueError(
            "historical draft blocks automatic release; review and resolve it"
        )
    return head


def main():
    head = json.loads(Path("default-ref.json").read_text())["object"]["sha"]
    source = select_source(GitHub(), os.environ["GITHUB_REPOSITORY"], head)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"source_sha={source}\nmain_sha={head}\n")
    print(f"Authorized package source {source}; current CI-tested main {head}")


if __name__ == "__main__":
    main()
