"""Keep normal release selection on current main; share historical CI checks."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from scripts.github_release import GitHub
from scripts.release_policy import authorize_release
from scripts.resolve_tag_ref import SHA

# Deliberately exact paths: no package, build config or dependency files.
RECOVERY_PATHS = {
    ".github/workflows/release.yml",
    ".github/workflows/ci.yml",
    "docs/release-and-compatibility.md",
    "docs/release-recovery.md",
    "scripts/github_release.py",
    "scripts/select_recovery_source.py",
    "scripts/release_recovery.py",
    "scripts/check_pypi_artifacts.py",
    "tests/test_release_recovery.py",
    "tests/fixtures/v1.3.3-artifacts.zip",
    "scripts/authorize_release_event.py",
    "scripts/parse_gh_response.py",
    "scripts/resolve_tag_ref.py",
    "scripts/select_release.py",
    "scripts/reconcile_release_assets.py",
    "scripts/verify_release_assets.py",
    "tests/fake_github_cli.py",
    "tests/test_release_workflow_integration.py",
    "tests/test_github_release.py",
    "tests/test_recovery_source.py",
    "tests/test_workflow_security.py",
}


def select_source(api, repository, head, *, git_diff):
    drafts = [
        r
        for r in api.collection(f"repos/{repository}/releases")
        if r.get("draft") is True
    ]
    if not drafts:
        return head
    # Never silently skip a pending reservation to choose another version.
    if len(drafts) != 1:
        raise ValueError("multiple pending releases; recovery is ambiguous")
    release = drafts[0]
    source = release.get("target_commitish", "")
    if not SHA.fullmatch(source):
        raise ValueError("pending release target is not an immutable source SHA")
    if source == head:
        return head
    raise ValueError(
        "historical draft blocks automatic release; use the one-time recovery procedure"
    )


def verify_source_ci(api, repository, source, *, run_id=None):
    workflow = api.request(f"repos/{repository}/actions/workflows/ci.yml")
    if run_id is None:
        evidence = api.request(
            f"repos/{repository}/actions/workflows/{workflow['id']}/runs"
            f"?head_sha={source}&event=push&branch=main&status=success&per_page=100"
        )
        runs = evidence.get("workflow_runs")
        if not isinstance(runs, list) or not runs:
            raise ValueError("package source has no successful CI evidence")
        run_id = runs[0]["id"]
    run = api.request(f"repos/{repository}/actions/runs/{run_id}")
    return authorize_release(
        event_name="workflow_run",
        event_run_id=run_id,
        event_head_sha=source,
        event_repository=repository,
        repository=repository,
        default_branch="main",
        workflow_run=run,
        workflow=workflow,
        repository_default_branch="main",
        current_default_sha=source,
    )


def git_diff(source, head):
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", source, head],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", source, head],
        check=True,
        capture_output=True,
    )
    return result.stdout.decode().rstrip("\0").split("\0")


def main():
    head = json.loads(Path("default-ref.json").read_text())["object"]["sha"]
    source = select_source(
        GitHub(), os.environ["GITHUB_REPOSITORY"], head, git_diff=git_diff
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"source_sha={source}\nmain_sha={head}\n")
    print(f"Authorized package source {source}; current CI-tested main {head}")


if __name__ == "__main__":
    main()
