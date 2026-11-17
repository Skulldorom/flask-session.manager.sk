"""Regression tests for the exact-source release authorization policy."""

from copy import deepcopy

import pytest

from scripts.release_policy import (
    ReleasePolicyError,
    authorize_release,
    bump_version,
    can_finalize_release,
    choose_release_version,
    classify_release,
    existing_artifact_state,
    validate_release_artifact_identity,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
REPOSITORY = "Skulldorom/flask-session.manager.sk"


def run(**overrides):
    value = {
        "id": 99,
        "repository": {"full_name": REPOSITORY, "default_branch": "main"},
        "head_repository": {"full_name": REPOSITORY},
        "head_branch": "main",
        "head_sha": SHA_A,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "workflow_id": 42,
    }
    value.update(overrides)
    return value


def workflow(**overrides):
    value = {"id": 42, "name": "CI", "path": ".github/workflows/ci.yml"}
    value.update(overrides)
    return value


def authorize(**overrides):
    values = {
        "event_name": "workflow_run",
        "event_run_id": 99,
        "event_head_sha": SHA_A,
        "event_repository": REPOSITORY,
        "repository": REPOSITORY,
        "default_branch": "main",
        "workflow_run": run(),
        "workflow": workflow(),
        "repository_default_branch": "main",
        "current_default_sha": SHA_A,
    }
    values.update(overrides)
    return authorize_release(**values)


def test_successful_ci_authorizes_only_the_exact_current_sha():
    assert authorize().source_sha == SHA_A
    with pytest.raises(ReleasePolicyError, match="stale"):
        authorize(current_default_sha=SHA_B)


@pytest.mark.parametrize(
    "changes",
    [
        {"conclusion": "failure"},
        {"conclusion": "cancelled"},
        {"conclusion": None},
        {"status": "in_progress"},
        {"event": "pull_request"},
        {"event": "push", "head_branch": "refs/tags/v1.3.3"},
        {"head_repository": {"full_name": "attacker/fork"}},
        {"repository": {"full_name": "attacker/fork"}},
        {"name": "Other workflow"},
        {"path": ".github/workflows/release.yml"},
        {"head_sha": "not-a-sha"},
    ],
)
def test_invalid_or_untrusted_ci_evidence_fails_closed(changes):
    with pytest.raises(ReleasePolicyError):
        authorize(workflow_run=run(**changes))


@pytest.mark.parametrize("event", ["workflow_dispatch", "push", "pull_request"])
def test_manual_tag_and_pr_events_cannot_authorize_release(event):
    with pytest.raises(ReleasePolicyError, match="workflow_run"):
        authorize(event_name=event)


def test_wrong_workflow_identity_fails_closed():
    with pytest.raises(ReleasePolicyError, match="identity"):
        authorize(workflow=workflow(id=7))


def test_event_payload_must_match_verified_run_identity():
    with pytest.raises(ReleasePolicyError, match="identifier"):
        authorize(event_run_id=100)
    with pytest.raises(ReleasePolicyError, match="event SHA"):
        authorize(event_head_sha=SHA_B)
    with pytest.raises(ReleasePolicyError, match="event repository"):
        authorize(event_repository="attacker/fork")


def pr(*labels):
    return {
        "merged_at": "2026-11-17T10:00:00Z",
        "merge_commit_sha": SHA_A,
        "base": {"ref": "main"},
        "labels": [{"name": label} for label in labels],
    }


def test_release_classification_defaults_to_patch_and_respects_overrides():
    assert (
        classify_release(associated_pull_requests=[], default_branch="main") == "patch"
    )
    assert (
        classify_release(
            associated_pull_requests=[pr()], default_branch="main", source_sha=SHA_A
        )
        == "patch"
    )
    assert (
        classify_release(
            associated_pull_requests=[pr("version:minor")],
            default_branch="main",
            source_sha=SHA_A,
        )
        == "minor"
    )
    assert (
        classify_release(
            associated_pull_requests=[pr("version:major")],
            default_branch="main",
            source_sha=SHA_A,
        )
        == "major"
    )
    assert (
        classify_release(
            associated_pull_requests=[pr("version:minor", "version:major")],
            default_branch="main",
            source_sha=SHA_A,
        )
        == "major"
    )


def test_unrelated_ambiguous_or_unmerged_pull_requests_are_not_misclassified():
    with pytest.raises(ReleasePolicyError, match="multiple"):
        classify_release(
            associated_pull_requests=[pr(), pr()],
            default_branch="main",
            source_sha=SHA_A,
        )
    unrelated = deepcopy(pr())
    unrelated["base"]["ref"] = "develop"
    with pytest.raises(ReleasePolicyError, match="not merged"):
        classify_release(
            associated_pull_requests=[unrelated],
            default_branch="main",
            source_sha=SHA_A,
        )
    unmerged = deepcopy(pr())
    unmerged["merged_at"] = None
    with pytest.raises(ReleasePolicyError, match="not merged"):
        classify_release(
            associated_pull_requests=[unmerged], default_branch="main", source_sha=SHA_A
        )


def test_pull_request_merge_sha_must_match_the_tested_source():
    stale = deepcopy(pr())
    stale["merge_commit_sha"] = SHA_B
    with pytest.raises(ReleasePolicyError, match="does not match"):
        classify_release(
            associated_pull_requests=[stale], default_branch="main", source_sha=SHA_A
        )


def test_release_automation_marker_does_not_loop():
    assert (
        classify_release(
            associated_pull_requests=[],
            default_branch="main",
            commit_message="chore: release bookkeeping [skip-version]",
        )
        is None
    )


@pytest.mark.parametrize(
    ("version", "part", "expected"),
    [
        ("1.3.2", "patch", "1.3.3"),
        ("1.3.2", "minor", "1.4.0"),
        ("1.3.2", "major", "2.0.0"),
    ],
)
def test_version_bumps_are_deterministic(version, part, expected):
    assert bump_version(version, part) == expected


def test_duplicate_release_events_and_partial_publication_reuse_reserved_version():
    assert choose_release_version(
        latest_pypi_version="1.3.2",
        source_sha=SHA_A,
        current_pypi_source_url=None,
        pending_release={"source_sha": SHA_A, "version": "1.3.3"},
        associated_pull_requests=[pr("version:minor")],
        default_branch="main",
    ) == ("1.3.3", "reserved-release")
    source_url = f"https://github.com/{REPOSITORY}/commit/{SHA_A}"
    assert choose_release_version(
        latest_pypi_version="1.3.3",
        source_sha=SHA_A,
        current_pypi_source_url=source_url,
        pending_release=None,
        associated_pull_requests=[],
        default_branch="main",
    ) == ("1.3.3", "already-published-source")


def test_pending_release_from_another_source_is_not_reused():
    with pytest.raises(ReleasePolicyError, match="different source"):
        choose_release_version(
            latest_pypi_version="1.3.2",
            source_sha=SHA_A,
            current_pypi_source_url=None,
            pending_release={"source_sha": SHA_B, "version": "1.3.3"},
            associated_pull_requests=[],
            default_branch="main",
        )


def test_published_source_and_draft_release_version_must_agree():
    source_url = f"https://github.com/{REPOSITORY}/commit/{SHA_A}"
    with pytest.raises(ReleasePolicyError, match="conflicts"):
        choose_release_version(
            latest_pypi_version="1.3.3",
            source_sha=SHA_A,
            current_pypi_source_url=source_url,
            pending_release={"source_sha": SHA_A, "version": "1.3.4"},
            associated_pull_requests=[],
            default_branch="main",
        )


def test_artifact_retries_require_the_complete_matching_set():
    built = {"wheel.whl": "a", "source.tar.gz": "b"}
    assert existing_artifact_state(built, {}) == "publish"
    assert existing_artifact_state(built, built) == "already-published"
    with pytest.raises(ReleasePolicyError, match="partial or conflicting"):
        existing_artifact_state(built, {"wheel.whl": "a"})
    with pytest.raises(ReleasePolicyError, match="partial or conflicting"):
        existing_artifact_state(built, {"wheel.whl": "changed", "source.tar.gz": "b"})


def test_failed_build_validation_or_publication_cannot_finalize_release():
    with pytest.raises(ReleasePolicyError, match="no release artifacts"):
        existing_artifact_state({}, {})
    assert not can_finalize_release(
        reservation_verified=True, artifact_state="publish", publish_result="failure"
    )
    assert not can_finalize_release(
        reservation_verified=False,
        artifact_state="already-published",
        publish_result="skipped",
    )


def test_exact_pypi_retry_can_finish_after_a_github_release_failure():
    assert can_finalize_release(
        reservation_verified=True,
        artifact_state="already-published",
        publish_result="skipped",
    )
    assert not can_finalize_release(
        reservation_verified=True,
        artifact_state="already-published",
        publish_result="failure",
    )


def test_artifact_handoff_is_bound_to_authorized_sha_and_version():
    validate_release_artifact_identity(
        source_sha=SHA_A,
        expected_source_sha=SHA_A,
        manifest_version="1.3.3",
        expected_version="1.3.3",
    )
    with pytest.raises(ReleasePolicyError, match="source SHA"):
        validate_release_artifact_identity(
            source_sha=SHA_B,
            expected_source_sha=SHA_A,
            manifest_version="1.3.3",
            expected_version="1.3.3",
        )
    with pytest.raises(ReleasePolicyError, match="version"):
        validate_release_artifact_identity(
            source_sha=SHA_A,
            expected_source_sha=SHA_A,
            manifest_version="1.3.2",
            expected_version="1.3.3",
        )
