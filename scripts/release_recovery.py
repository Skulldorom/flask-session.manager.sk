"""One-time, explicitly authorized v1.3.3 recovery. No local PyPI uploads.

Read-only by default. Only reconcile/finalize can mutate GitHub, and only the
pinned existing reservation. The workflow commands never mutate GitHub.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from scripts.check_pypi_artifacts import pypi_artifact_state
from scripts.github_release import GitHub, Release
from scripts.select_recovery_source import RECOVERY_PATHS, git_diff, verify_source_ci
from scripts.validate_release_artifacts import metadata_version

REPOSITORY = "Skulldorom/flask-session.manager.sk"
SOURCE = "06b73bebaae8fa397db17a8aab2304ce3a9896f6"
VERSION = "1.3.3"
RELEASE_ID = 448693106
SOURCE_CI = 44095831330
HASHES = {
    "flask_session_manager_sk-1.3.3-py3-none-any.whl": (
        "137e73f89f675698b2d1aa7c2dff228265ea88f1ab2019171dd1467c3b56896d"
    ),
    "flask_session_manager_sk-1.3.3.tar.gz": (
        "b7c28b506fbc4369f8dc401a888f2a8b4283dd031ff52c2fb7939c77b74f8189"
    ),
    "release-manifest.json": (
        "2c35539d92581341b05a7df3a3ea77598dd65ac6081ad3c81b5c69a765e644d2"
    ),
}
CONFIRM_PUBLICATION = "publish-v1.3.3-448693106"


def maintainer(api, login):
    if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9-]+", login):
        raise ValueError("invalid maintainer identity")
    permission = api.request(
        f"repos/{REPOSITORY}/collaborators/{quote(login, safe='')}/permission"
    )
    if (
        permission.get("user", {}).get("login", "").lower() != login.lower()
        or permission.get("permission") not in {"admin", "write"}
        or permission.get("role_name") not in {"admin", "maintain"}
    ):
        raise ValueError("recovery requires a current repository maintainer/admin")


def verify_sources(api, main_sha, *, diff=git_diff):
    ref = api.request(f"repos/{REPOSITORY}/git/ref/heads/main")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", main_sha)
        or ref.get("object", {}).get("sha") != main_sha
        or ref.get("object", {}).get("type") != "commit"
        or ref.get("ref") != "refs/heads/main"
    ):
        raise ValueError("current main changed or expected main SHA is invalid")
    if main_sha != SOURCE:
        changed = diff(SOURCE, main_sha)  # Also proves ancestry, not just a path diff.
        if not changed or not set(changed) <= RECOVERY_PATHS:
            raise ValueError("recovery blocked by application/build or unknown changes")
    verify_source_ci(api, REPOSITORY, SOURCE, run_id=SOURCE_CI)
    if main_sha != SOURCE:
        verify_source_ci(api, REPOSITORY, main_sha)


def authorize_workflow(api, event, env, *, diff=git_diff):
    inputs = event.get("inputs", {})
    main_sha = inputs.get("main_sha", "")
    expected = {
        "repository": REPOSITORY,
        "version": VERSION,
        "source_sha": SOURCE,
        "release_id": str(RELEASE_ID),
        "main_sha": main_sha,
        "confirmation": CONFIRM_PUBLICATION,
    }
    if (
        env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or env.get("GITHUB_REPOSITORY") != REPOSITORY
        or env.get("GITHUB_REF") != "refs/heads/main"
        or env.get("GITHUB_WORKFLOW_REF")
        != f"{REPOSITORY}/.github/workflows/release.yml@refs/heads/main"
        or env.get("GITHUB_SHA") != main_sha
        or env.get("GITHUB_WORKFLOW_SHA") != main_sha
        or event.get("repository", {}).get("full_name") != REPOSITORY
        or event.get("repository", {}).get("default_branch") != "main"
        or event.get("ref") not in {"main", "refs/heads/main"}
        or inputs != expected
        or event.get("sender", {}).get("login") != env.get("GITHUB_ACTOR")
    ):
        raise ValueError("unauthorized recovery event or non-pinned release identity")
    for login in {env.get("GITHUB_ACTOR"), env.get("GITHUB_TRIGGERING_ACTOR")}:
        maintainer(api, login)
    verify_sources(api, main_sha, diff=diff)
    return main_sha


def candidate(api, dist, main_sha):
    # Independent build must reproduce the observed original bytes, not just
    # supply a self-consistent manifest chosen by the operator.
    for name, digest in HASHES.items():
        if hashlib.sha256((dist / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"original build hash mismatch: {name}")
    for name in HASHES.keys() - {"release-manifest.json"}:
        _, version, source_url = metadata_version(dist / name)
        if (
            version != VERSION
            or source_url != f"https://github.com/{REPOSITORY}/commit/{SOURCE}"
        ):
            raise ValueError("original package source/version metadata mismatch")
    return Release(api, REPOSITORY, SOURCE, VERSION, dist, main_sha=main_sha)


def inspect_recovery(api, dist, main_sha, *, require_tag=False, diff=git_diff):
    verify_sources(api, main_sha, diff=diff)
    release = candidate(api, dist, main_sha)
    record, tag = release.verify(release_id=RELEASE_ID, require_tag=require_tag)
    state = pypi_artifact_state(release.manifest)
    plan = {
        "repository": REPOSITORY,
        "version": VERSION,
        "source_sha": SOURCE,
        "release_id": RELEASE_ID,
        "main_sha": main_sha,
        "draft": record["draft"],
        "tag": "verified" if tag else "absent",
        "assets": "all three original hashes verified",
        "pypi_state": state,
        "reconcile": [f"create refs/tags/v{VERSION} at {SOURCE}"]
        if tag is None
        else [],
        "publication": "restricted OIDC workflow; no local package upload",
        "finalize": "maintainer PATCH existing release after exact PyPI verification",
    }
    return release, plan


def execute_operator(action, args, api, *, diff=git_diff):
    if (args.repository, args.version, args.source_sha, args.release_id) != (
        REPOSITORY,
        VERSION,
        SOURCE,
        RELEASE_ID,
    ):
        raise ValueError("recovery identity must match the pinned original release")
    release, plan = inspect_recovery(api, args.dist, args.main_sha, diff=diff)
    print(json.dumps(plan, indent=2, sort_keys=True))
    if action in {"inspect", "validate", "plan"}:
        return plan
    confirmation = f"{action} {REPOSITORY} v{VERSION} {SOURCE} {RELEASE_ID}"
    if args.confirm != confirmation:
        raise ValueError(f"explicit --confirm must be: {confirmation}")
    user = api.request("user")
    if user.get("type") != "User":
        raise ValueError("one-time mutation requires an authenticated human maintainer")
    maintainer(api, user.get("login"))
    if action == "reconcile" and not plan["draft"]:
        raise ValueError("release is no longer pending")
    if action == "finalize" and plan["pypi_state"] != "already-published":
        raise ValueError("finalization requires exact PyPI publication first")
    # No creates/uploads are allowed in existing-only mode. All remote evidence
    # is checked again inside the shared orchestrator before any mutation.
    release.run(finalize=action == "finalize", existing_id=RELEASE_ID)
    return plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        nargs="?",
        default="inspect",
        choices=[
            "inspect",
            "validate",
            "plan",
            "reconcile",
            "finalize",
            "workflow-authorize",
            "workflow-verify",
            "workflow-finish",
        ],
    )
    parser.add_argument("--repository", required=False)
    parser.add_argument("--version")
    parser.add_argument("--source-sha")
    parser.add_argument("--release-id", type=int)
    parser.add_argument("--main-sha")
    parser.add_argument("--dist", type=Path, default=Path("recovery-dist"))
    parser.add_argument("--confirm")
    args = parser.parse_args()
    api = GitHub()
    if args.action.startswith("workflow-"):
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        main_sha = authorize_workflow(api, event, os.environ)
        if args.action != "workflow-authorize":
            _, plan = inspect_recovery(api, args.dist, main_sha, require_tag=True)
            if not plan["draft"]:
                raise ValueError("one-time publication requires the existing draft")
            if args.action == "workflow-finish":
                if plan["pypi_state"] != "already-published":
                    raise ValueError("PyPI publication is not complete")
                print(
                    "PyPI verified. Maintainer must now finalize "
                    "release 448693106 locally."
                )
            else:
                with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                    output.write(f"state={plan['pypi_state']}\n")
        return
    # Explicit identity is mandatory for writes; read-only defaults are pinned.
    if args.action in {"inspect", "validate", "plan"}:
        args.repository = args.repository or REPOSITORY
        args.version = args.version or VERSION
        args.source_sha = args.source_sha or SOURCE
        args.release_id = args.release_id or RELEASE_ID
        if not args.main_sha:
            args.main_sha = api.request(f"repos/{REPOSITORY}/git/ref/heads/main")[
                "object"
            ]["sha"]
    if not args.main_sha:
        raise ValueError(
            "mutation requires explicit --main-sha and pinned identity flags"
        )
    execute_operator(args.action, args, api)


if __name__ == "__main__":
    main()
