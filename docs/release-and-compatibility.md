# Release and compatibility policy

## Current release state (audited 17 November 2026)

- The default branch is `main`; verify the current SHA rather than relying on this historical audit snapshot.
- The latest published GitHub Release and PyPI version are `v1.3.2` / `1.3.2`.
- The prior `Release` workflow accepted arbitrary `v*.*.*` tag pushes and could build/publish them without CI evidence. Its automatic path also reset a previously tested SHA to the then-current `main`, and pushed the version commit/tag before it built the package.
- Before this change, Flask had two publishing entry points: `workflow_run` after `CI` completed and `workflow_dispatch` with a selected version part; the tag-triggered `publish-existing-tag` job was a third bypass. The automatic job used `GITHUB_TOKEN` with repository write permission to push a version commit and tag, then PyPI OIDC to upload, then `GITHUB_TOKEN` to create a GitHub Release. The tag job also used PyPI OIDC and accepted any matching tag. Both used `skip-existing: true`.
- The React companion has its own separate npm `Release` workflow: main `Tests` completion and manual dispatch run the versioning/publish job, and any `v*.*.*` tag also reaches `publish-existing-tag` and `npm publish --provenance`. It has write-capable GitHub permissions. This Flask PR does not change it; the companion should receive a separate release-hardening review as well as the browser-harness follow-up below.
- The active `main` ruleset requires a PR, linear history, and `Lint`, `Python 3.11`, `Python 3.12`, and `Python 3.13`. Repository settings currently leave squash merge enabled and merge/rebase disabled. This change does not edit rulesets.
- PyPI's project JSON does not expose the account's Trusted Publisher configuration. Confirm the entry described below in the PyPI project settings.

## Release flow

Normal automatic releases start only after a completed `CI` workflow run. The release gate reloads the run from the GitHub Actions API and checks the repository, default branch, push event, workflow ID/name/path, completed-success conclusion, event SHA, run SHA, and current `main` SHA. A PR run, tag event, failed/cancelled/missing run, stale SHA, or mismatched workflow identity cannot authorize normal publication. No manual-dispatch publisher exists. A historical draft blocks normal automatic version selection until explicitly resolved; the abandoned unpublished `v1.3.3` draft was deleted so fresh current-main releases may reuse `1.3.3` if PyPI does not already contain it.

The gate passes the exact tested SHA to a build job. That job selects a version from the latest PyPI version and the single merged PR associated with that exact merge commit. `version:major` wins over `version:minor`; no label means patch. Direct main pushes without an associated PR use patch. A commit containing `[skip-version]` is intentionally ignored. Ambiguous PR associations and mismatched merge SHAs fail closed.

`hatch-vcs` supplies package versions from Git tags for normal source builds. For the release artifact, `SETUPTOOLS_SCM_PRETEND_VERSION` supplies the selected version without modifying `main`. The `Release Source` package URL records the full source SHA. Build tools are pinned in `pyproject.toml` and `uv.lock`; CI sets `SOURCE_DATE_EPOCH` from the tested commit and validates wheel/sdist metadata and hashes.

After validating the artifacts, the workflow creates or verifies a **draft** GitHub Release tagged at the tested source SHA. It attaches the wheel, sdist, and manifest. Only then does a separate job publish the exact artifact handoff to PyPI using Trusted Publishing. It grants `id-token: write` only to this job. The final job confirms that every PyPI file hash exactly matches the build and then makes the GitHub Release public.

The draft release and the embedded `Release Source` URL make retries deterministic after partial failure. A retry with the same source SHA reuses the reserved version. If a draft release has only some expected assets, the workflow verifies every existing asset and uploads only missing files, without clobbering. Unexpected or conflicting assets block recovery. A public release must already contain the complete matching set. PyPI is treated as immutable: no existing files means publish; a complete matching set means continue without publishing; any partial or conflicting set blocks automatic recovery and requires operator reconciliation. `skip-existing` is not used. A PyPI upload that succeeds while GitHub finalization fails remains recoverable by rerunning CI for the same current source SHA; the job verifies the uploaded hashes before completing the draft release.

The release workflow never writes to protected `main`. Release tags point at the exact tested source commit, while `hatch-vcs` uses the `vX.Y.Z` tag as the package version after publication. The artifact itself carries the version and source commit in its metadata.

## CI coverage

- Existing stable required job names remain unchanged: `Lint`, `Python 3.11`, `Python 3.12`, and `Python 3.13`.
- `Release Policy` tests exact-SHA authorization, failed/cancelled/missing/stale CI, event and workflow identity, version labels, direct pushes, duplicate/recovery behavior, and artifact identity. Integration tests execute the workflow's authorization, selection, draft reservation, PyPI-state, asset verification, and finalization commands against local fixtures and a fake `gh` client; no real GitHub or package publication writes occur.
- The same job builds a test artifact with the `1.3.3` VCS version override and validates its wheel/sdist metadata and embedded source SHA. It creates no tag and publishes nothing.
- `Dependency Audit` exports the resolved runtime and development dependency sets from `uv.lock`. It uses pinned `pip-audit==2.9.0`. Exceptions live in `security/pip-audit-exceptions.json` and require an exact package and advisory ID, rationale, and expiry date. There are currently no exceptions.
- The resolved-dependency audit found PyJWT advisories fixed in 2.14.0 and a Werkzeug Windows/NTFS path-hang advisory fixed in 3.1.9. The supported dependency floors now require `PyJWT>=2.14.0` and `Werkzeug>=3.1.9`; `uv.lock` resolves PyJWT 2.15.1 and Werkzeug 3.1.9. No advisory was suppressed.
- `Flask ↔ React Compatibility` installs the wheel built from the Flask PR into an isolated Python environment, verifies the imported module path and installed version, then runs the pinned React companion's real browser harness. The reviewed companion revision is `8569b734306edb49de9d714b3d5a0e4cd0d06fbf`; update it only after reviewing the new harness and changing the guarded patch in `scripts/patch_companion_harness.py` if its expected block moved.
- Chromium covers both same-origin and cross-site lifecycles. Firefox covers the same-origin cookie/session lifecycle. This CI does not claim that cross-site cookies or CHIPS work in Firefox, WebKit, or real mobile browsers. A focused React-side follow-up should add an upstream browser selector and negative browser-cookie tests; related companion issue: [react-session.manager.sk#71](https://github.com/Skulldorom/react-session.manager.sk/issues/71).
- Flask characterization coverage adds rejected missing/incorrect CSRF, missing device ID, and invalid bearer with a valid cookie. Existing tests already cover untrusted/missing origins, expired tokens, revoked records, and refresh refusal. This PR does not change authentication behavior; security behavior changes stay scoped to issue #27.

## PyPI Trusted Publisher configuration

In PyPI, open the `flask-session.manager.sk` project settings and verify the Trusted Publisher entry has:

| Field | Value |
|---|---|
| Owner | `Skulldorom` |
| Repository | `flask-session.manager.sk` |
| Workflow | `.github/workflows/release.yml` |
| Environment | blank |

No PyPI API token or GitHub secret is needed. If the configured workflow or owner differs, update the PyPI Trusted Publisher entry before relying on automatic releases. Only the normal publisher obtains OIDC tokens; tests, compatibility, build, and release-reservation jobs do not. No GitHub App or stored maintainer credential is used.

## Branch protection setup after this PR runs successfully

No ruleset was changed. Keep the four current required checks. Once this PR has a successful run and the new jobs have stable results, consider adding these exact contexts to the `main` ruleset:

- `Release Policy`
- `Dependency Audit`
- `Flask ↔ React Compatibility`

All three run for pull requests, have no path filters, and report a failure if their work fails. Do not add them as required until GitHub has observed successful runs for each context.

## Remaining boundaries

- GitHub and PyPI publication cannot be atomic. The draft-release reservation, embedded source SHA, checksums, and fail-closed retry states prevent a partial upload from being reported as complete, but a partially uploaded version may need operator reconciliation because PyPI files are immutable.
- Release authorization depends on GitHub's Actions and commit-to-PR APIs being available. API failures stop the release.
- The browser test covers a pinned React revision. Updating the pin is deliberate compatibility maintenance, not a floating dependency update.
- A GitHub administrator can still alter workflows, labels, tags, PyPI publisher settings, or rulesets. This design prevents accidental workflow bypasses; it cannot defend against a compromised repository/PyPI administrator.
