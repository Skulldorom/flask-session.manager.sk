# Release reservation and recovery

## Production failures investigated

* [44074765875](https://github.com/Skulldorom/flask-session.manager.sk/actions/runs/44074765875): the commits endpoint returned 422 for the nonexistent `v1.3.3` tag. The reservation did not reach creation.
* [44096173397](https://github.com/Skulldorom/flask-session.manager.sk/actions/runs/44096173397): draft creation and three uploads succeeded, then the refs lookup returned 404. A draft's pending tag is not evidence that a Git ref exists.

Both runs stopped before PyPI. The previous fake assigned `tag_sha` inside draft creation and returned drafts from the REST release-by-tag endpoint. These inaccurate behaviors masked the actual lifecycle. Previous retry tests also kept main at the original source, missing recovery after a workflow-fix merge.

Read-only inspection on 17 November 2026 found draft ID `448693106`, pending tag `v1.3.3`, target/source `06b73bebaae8fa397db17a8aab2304ce3a9896f6`, no Git ref, and these uploaded identities:

| Asset | ID | SHA-256 |
| --- | --- | --- |
| flask_session_manager_sk-1.3.3-py3-none-any.whl | 759240723 | 137e73f89f675698b2d1aa7c2dff228265ea88f1ab2019171dd1467c3b56896d |
| flask_session_manager_sk-1.3.3.tar.gz | 759240717 | b7c28b506fbc4369f8dc401a888f2a8b4283dd031ff52c2fb7939c77b74f8189 |
| release-manifest.json | 759240718 | 2c35539d92581341b05a7df3a3ea77598dd65ac6081ad3c81b5c69a765e644d2 |

The original Actions artifact `14332109762` was downloaded read-only: all three hashes matched these API digests. Independently rebuilding the original commit in a regular Git checkout with the locked build tools, version override `1.3.3`, and commit-derived `SOURCE_DATE_EPOCH` reproduced both package hashes exactly. This is not a claim that a newly built package from a different source matches the draft.

## API contracts

[GitHub's releases API](https://docs.github.com/en/rest/releases/releases) distinguishes a release ID, a pending tag name, and the Git ref. The releases collection includes drafts for authorized callers; it is paginated. Release-by-tag retrieves a published release and a 404 does not exclude a draft. Release-by-ID reliably retrieves the known draft with an authorized token.

[`gh release view` implementation](https://github.com/cli/cli/blob/trunk/pkg/cmd/release/shared/fetch.go) separately discovers drafts using GraphQL and then reads their REST IDs; it does not establish that REST release-by-tag sees drafts. This workflow therefore uses collection discovery and explicit IDs for all asset and release operations.

[Git refs](https://docs.github.com/en/rest/git/refs) and [tag objects](https://docs.github.com/en/rest/git/tags) establish tag identity. Only an explicit 404 from the exact tag-ref lookup represents absence. Every other status, CLI failure, malformed/empty response and timeout stops the invocation. No write request is blindly retried after an uncertain result.

## State transitions

1. Authenticate the exact successful main push CI run against GitHub. Verify the current main ref still equals that run's SHA.
2. Discover any pending reservation before choosing a version. Without one, preserve patch default and existing minor/major label precedence. With one, preserve its version and source.
3. For recovery across an infrastructure fix, require the old source to be an ancestor, the complete Git diff to contain only explicitly enumerated release infrastructure paths, and a separate successful trusted main push CI run for the exact old SHA. Package, build and dependency changes block recovery. Guard the new main SHA throughout, while building and tagging the original source SHA.
4. Check the Git ref (peeling bounded annotated-tag chains). Discover the unique release through every collection page and re-read its ID. Verify exact tag, target, source/version metadata and boolean draft/prerelease status. Duplicate or contradictory records block.
5. Download each existing asset by numeric ID and verify size, bytes and SHA-256 against the independently built candidate. Check any API digest against those bytes. Perform these checks before creating a missing tag. Unexpected, conflicting or incomplete `starter` assets block; nothing is deleted or overwritten.
6. Explicitly create the absent ref at the authorized commit, never update it. A concurrent 422 is re-read; a different commit blocks. If no draft had been discovered, stop rather than race the winner to create another draft. The next invocation rediscovers state.
7. Reuse the matching draft or create a draft with exact immutable metadata after the verified tag exists. Upload only missing names by release ID, re-reading state before each upload. Verify the entire reservation again. Workflow concurrency serializes this workflow's release events.
8. Publish only verified wheel/sdist through the existing OIDC Trusted Publisher. Recheck current-main eligibility and hashes immediately before publication. Identical existing PyPI files skip uploading; partial/conflicting PyPI sets block.
9. Recheck PyPI hashes, release identity, assets and tag with the same helper before finalizing by release ID. A retry after PyPI publication or lost finalization response verifies existing state rather than creating a new version.

Interruption after draft creation or any successful asset upload leaves a recoverable reservation. A failed process with a successful HTTP response is still an error; a later authorized invocation reconciles its durable effects. There is no clobber, force-push, main write, manual-publish event or ruleset bypass.

## Existing v1.3.3 recovery and required configuration

Merging a workflow fix changes `.github/workflows/` relative to the old draft's source. GitHub explicitly requires **Workflows: write** to update a release targeting such an older commit, and documents that `GITHUB_TOKEN` cannot receive this permission. Treat a 403/404 on this mutation as a permission error, never as absence.

Before this historical recovery can run, configure a GitHub App installed on **only this repository**, with repository **Contents: write** and **Workflows: write**. Add repository variable `RELEASE_RECOVERY_APP_ID` and Actions secret `RELEASE_RECOVERY_APP_PRIVATE_KEY`. Do not grant administration, a main-ruleset bypass, or access to other repositories. The pinned token action requests only those two permissions for this repository and revokes the token at job completion. Only the reservation/finalization jobs mint it, and only when historical recovery was authorized. PyPI remains OIDC-only. Normal current-source releases use `GITHUB_TOKEN`.

Missing App ID fails authorization before mutations. Invalid/missing credentials fail token creation before mutations. Configuration is intentionally not created by this PR. Do not merge expecting historical recovery to work until it is configured. No existing failed release workflow needs to be retried: after merge, successful CI for the new main triggers the normal release workflow, which authorizes and recovers the original source/version under the narrow checks above.

For the observed state, recovery will reuse `448693106`, rebuild **the original** source as `1.3.3`, download and verify its three assets, create only the absent `refs/tags/v1.3.3` at the original source, perform no asset uploads, publish/verify exactly those package hashes on PyPI, and finalize the same release ID. Any changed identity, bytes, main ref, ancestry or CI evidence blocks before publication. No draft deletion, artifact overwrite or `1.3.4` is needed.

## Verification boundaries

Tests execute the actual Python orchestration and actual YAML shell steps against local REST/CLI contracts. They model draft/tag separation, ID downloads, pagination, interrupted writes, conflicting identities, annotated tags, duplicate events, concurrent tag claims, API errors and moved main. No test publishes live resources.

Live mutation behavior, App installation permissions, tag rulesets, OIDC upload and finalization cannot be fully proven without an authorized release or a disposable integration repository. Passing local/hosted tests is not production publication verification. A remote `starter` asset or partially published PyPI version still requires manual reconciliation; the helper deliberately refuses to delete or overwrite either. Multiple drafts or non-infrastructure changes after the original source require separate review.
