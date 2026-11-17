"""Validate workflow_run event metadata against GitHub API evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from scripts.release_policy import authorize_release


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--default-ref", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path(os.environ.get("GITHUB_OUTPUT", ""))
    )
    args = parser.parse_args()
    event = json.loads(args.event.read_text())
    run = json.loads(args.run.read_text())
    workflow = json.loads(args.workflow.read_text())
    default_ref = json.loads(args.default_ref.read_text())
    repository_metadata = json.loads(args.repository.read_text())
    event_repository = event.get("repository", {}).get("full_name")
    default_branch = event.get("repository", {}).get("default_branch")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not event_repository or not default_branch or not repository:
        raise SystemExit("event repository identity/default branch is missing")
    if default_ref.get("object", {}).get("sha") != run.get("head_sha"):
        # Do not emit the branch SHA as a replacement. The tested source itself
        # must still be the current branch tip.
        current_sha = default_ref.get("object", {}).get("sha", "")
    else:
        current_sha = run["head_sha"]
    authorization = authorize_release(
        event_name=os.environ.get("GITHUB_EVENT_NAME", ""),
        event_run_id=event.get("workflow_run", {}).get("id"),
        event_head_sha=event.get("workflow_run", {}).get("head_sha", ""),
        event_repository=event_repository,
        repository=repository,
        default_branch=default_branch,
        workflow_run=run,
        workflow=workflow,
        repository_default_branch=repository_metadata.get("default_branch", ""),
        current_default_sha=current_sha,
    )
    with args.output.open("a") as output:
        output.write(f"source_sha={authorization.source_sha}\n")
    print(f"Authorized release source {authorization.source_sha}.")


if __name__ == "__main__":
    main()
