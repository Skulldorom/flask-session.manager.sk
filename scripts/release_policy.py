"""Fail-closed policy helpers for the Flask release workflow.

These functions intentionally use plain data objects so release decisions can
be regression-tested without GitHub credentials or publishing to PyPI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class ReleasePolicyError(ValueError):
    """Raised when release evidence is missing, inconsistent, or ambiguous."""


@dataclass(frozen=True)
class Authorization:
    source_sha: str
    reason: str


def authorize_release(
    *,
    event_name: str,
    event_run_id: int,
    event_head_sha: str,
    event_repository: str,
    repository: str,
    default_branch: str,
    workflow_run: dict[str, Any],
    workflow: dict[str, Any],
    repository_default_branch: str,
    current_default_sha: str,
) -> Authorization:
    """Authorize only a successful CI push run for the current main commit."""
    if event_name != "workflow_run":
        raise ReleasePolicyError("only workflow_run events may authorize release")
    if event_repository != repository:
        raise ReleasePolicyError(
            "event repository does not match the workflow repository"
        )
    if workflow_run.get("id") != event_run_id:
        raise ReleasePolicyError(
            "event run identifier does not match the verified API run"
        )
    source_sha = workflow_run.get("head_sha")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ReleasePolicyError("CI run has no valid immutable source SHA")
    if workflow_run.get("repository", {}).get("full_name") != repository:
        raise ReleasePolicyError("CI run repository does not match this repository")
    if repository_default_branch != default_branch:
        raise ReleasePolicyError("CI run default branch does not match the repository")
    if workflow_run.get("head_repository", {}).get("full_name") != repository:
        raise ReleasePolicyError("CI source comes from a different repository")
    if workflow_run.get("head_branch") != default_branch:
        raise ReleasePolicyError("CI source is not on the default branch")
    if workflow_run.get("event") != "push":
        raise ReleasePolicyError("CI must be a push run, not a pull request or tag run")
    if workflow_run.get("status") != "completed":
        raise ReleasePolicyError("CI run is not completed")
    if workflow_run.get("conclusion") != "success":
        raise ReleasePolicyError("CI run did not conclude successfully")
    if workflow_run.get("name") != "CI":
        raise ReleasePolicyError("CI workflow name does not match")
    if workflow_run.get("path") != ".github/workflows/ci.yml":
        raise ReleasePolicyError("CI workflow path does not match")
    workflow_id = workflow_run.get("workflow_id")
    if not isinstance(workflow_id, int) or workflow.get("id") != workflow_id:
        raise ReleasePolicyError("CI run workflow identity does not match")
    if (
        workflow.get("name") != "CI"
        or workflow.get("path") != ".github/workflows/ci.yml"
    ):
        raise ReleasePolicyError("resolved workflow is not the trusted CI workflow")
    if source_sha != current_default_sha:
        raise ReleasePolicyError(
            "successful CI SHA is stale; refusing to redirect release"
        )
    if source_sha != event_head_sha:
        raise ReleasePolicyError("event SHA does not match the verified API run SHA")
    return Authorization(source_sha, "successful CI for current default-branch SHA")


def classify_release(
    *,
    associated_pull_requests: list[dict[str, Any]],
    default_branch: str,
    source_sha: str | None = None,
    commit_message: str = "",
) -> str | None:
    """Choose patch/minor/major deterministically, or skip marked automation."""
    if "[skip-version]" in commit_message:
        return None
    if len(associated_pull_requests) > 1:
        raise ReleasePolicyError("source SHA is associated with multiple pull requests")
    if not associated_pull_requests:
        # Direct main pushes (including maintenance commits) are patch releases.
        return "patch"

    pr = associated_pull_requests[0]
    if pr.get("merged_at") is None or pr.get("base", {}).get("ref") != default_branch:
        raise ReleasePolicyError(
            "associated pull request is not merged into the default branch"
        )
    if source_sha is not None and pr.get("merge_commit_sha") != source_sha:
        raise ReleasePolicyError(
            "pull request merge commit does not match the tested source SHA"
        )
    labels = {item.get("name") for item in pr.get("labels", [])}
    if "version:major" in labels:
        return "major"
    if "version:minor" in labels:
        return "minor"
    return "patch"


def bump_version(version: str, part: str) -> str:
    """Increment a stable X.Y.Z version without prerelease ambiguity."""
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version)
    if not match or part not in {"major", "minor", "patch"}:
        raise ReleasePolicyError("version or increment is invalid")
    major, minor, patch = map(int, match.groups())
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


def choose_release_version(
    *,
    latest_pypi_version: str,
    source_sha: str,
    current_pypi_source_url: str | None,
    pending_release: dict[str, str] | None,
    associated_pull_requests: list[dict[str, Any]],
    default_branch: str,
    commit_message: str = "",
) -> tuple[str, str]:
    """Keep retries on their reserved version; otherwise increment PyPI latest."""
    expected_url = (
        f"https://github.com/Skulldorom/flask-session.manager.sk/commit/{source_sha}"
    )
    if current_pypi_source_url == expected_url:
        if pending_release and pending_release.get("version") != latest_pypi_version:
            raise ReleasePolicyError(
                "published source metadata conflicts with its pending release version"
            )
        return latest_pypi_version, "already-published-source"
    if pending_release:
        if pending_release.get("source_sha") != source_sha:
            raise ReleasePolicyError(
                "pending release belongs to a different source SHA"
            )
        version = pending_release.get("version", "")
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ReleasePolicyError("pending release has an invalid version")
        return version, "reserved-release"
    part = classify_release(
        associated_pull_requests=associated_pull_requests,
        default_branch=default_branch,
        source_sha=source_sha,
        commit_message=commit_message,
    )
    if part is None:
        raise ReleasePolicyError(
            "release automation marker cannot reserve a new release"
        )
    return bump_version(latest_pypi_version, part), part


def existing_artifact_state(expected: dict[str, str], existing: dict[str, str]) -> str:
    """Return publish/already-published; reject partial or mismatched retries."""
    if not expected:
        raise ReleasePolicyError("no release artifacts were built")
    if not existing:
        return "publish"
    if expected == existing:
        return "already-published"
    raise ReleasePolicyError(
        "PyPI contains a partial or conflicting artifact set; "
        "manual reconciliation required"
    )


def can_finalize_release(
    *, reservation_verified: bool, artifact_state: str, publish_result: str
) -> bool:
    """Permit public GitHub release only after reservation and PyPI proof."""
    if not reservation_verified:
        return False
    if artifact_state == "already-published":
        return publish_result in {"success", "skipped"}
    if artifact_state == "publish":
        return publish_result == "success"
    return False


def validate_release_artifact_identity(
    *,
    source_sha: str,
    expected_source_sha: str,
    manifest_version: str,
    expected_version: str,
) -> None:
    """Reject an artifact handed off from a different source or version."""
    if source_sha != expected_source_sha:
        raise ReleasePolicyError("artifact source SHA does not match authorized CI SHA")
    if manifest_version != expected_version:
        raise ReleasePolicyError(
            "artifact version does not match selected release version"
        )
