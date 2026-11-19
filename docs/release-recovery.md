# Release reliability and one-time v1.3.3 recovery

Normal releases use `GITHUB_TOKEN` and PyPI Trusted Publishing. Historical recovery is an explicit maintainer operation; no GitHub App, private key, Actions PAT secret, ruleset bypass, or automatic historical-source selection is required.

## Failures and current evidence

[Run 44074765875](https://github.com/Skulldorom/flask-session.manager.sk/actions/runs/44074765875) queried an absent tag through the commits API and received 422. [Run 44096173397](https://github.com/Skulldorom/flask-session.manager.sk/actions/runs/44096173397) created the draft and uploaded its assets, then failed because draft creation had not created the Git ref. The previous fake incorrectly created that ref and returned drafts from REST release-by-tag. The corrected API fixtures and executable lifecycle tests remain.

Read-only inspection on **19 November 2026** reconfirmed:

| Property | Identity |
| --- | --- |
| Existing draft | `448693106`, still unpublished |
| Version/tag | `1.3.3` / `v1.3.3` |
| Original source/target | `06b73bebaae8fa397db17a8aab2304ce3a9896f6` |
| Exact-source trusted main push CI | [44095831330](https://github.com/Skulldorom/flask-session.manager.sk/actions/runs/44095831330), successful |
| Tag ref | Absent (exact refs API returned 404) |
| PyPI version | No files; publication remains pending |

| Asset | Existing ID | SHA-256 |
| --- | --- | --- |
| `flask_session_manager_sk-1.3.3-py3-none-any.whl` | 759240723 | `137e73f89f675698b2d1aa7c2dff228265ea88f1ab2019171dd1467c3b56896d` |
| `flask_session_manager_sk-1.3.3.tar.gz` | 759240717 | `b7c28b506fbc4369f8dc401a888f2a8b4283dd031ff52c2fb7939c77b74f8189` |
| `release-manifest.json` | 759240718 | `2c35539d92581341b05a7df3a3ea77598dd65ac6081ad3c81b5c69a765e644d2` |

The original Actions artifact `14332109762` has archive digest `b466d765610b8e9c151cdae033df85c2937ce4dbc43902f4747a53857b30779f`. Its three files match the live draft's recorded digests. Both package hashes were independently reproduced from a regular checkout of the original commit using locked build tools and commit-derived `SOURCE_DATE_EPOCH`. An authentic, small archive fixture preserves these bytes for offline handoff tests. Operator validation also downloads every live draft asset by ID and hashes its bytes; API digests alone are insufficient.

## Permission audit

[Git refs](https://docs.github.com/en/rest/git/refs), [Releases](https://docs.github.com/en/rest/releases/releases), [release assets](https://docs.github.com/en/rest/releases/assets), and [GITHUB_TOKEN](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token) document different requirements:

| Operation | Ordinary current-main release | Historical v1.3.3 recovery |
| --- | --- | --- |
| Read refs, releases, asset bytes, CI | Contents/Actions read as applicable | Same; no workflow-write permission needed |
| Create immutable tag ref | Contents write | Maintainer uses Contents write; authorize Workflows write too for GitHub's workflow-sensitive ref restrictions |
| Create a release | Contents write when target matches current main | Not used: the existing draft must be reused |
| Upload draft assets | Contents write; endpoint has no blanket historical workflow-write requirement | Not used: all three original files must already match; no uploads or replacements |
| PATCH draft to publish | Contents write at current main | Workflows write additionally required when target workflows differ from current main |
| Upload package files to PyPI | OIDC Trusted Publishing, no GitHub workflow-write scope | Same OIDC publisher, GitHub read-only token; no maintainer PyPI credential |

GitHub explicitly documents workflow-write checks on release creation/update when the target changes workflow files relative to main, including possible 403/404 permission errors. `GITHUB_TOKEN` cannot receive that permission. The refs documentation lists both Contents-only and Contents+Workflows permission sets but does not exhaustively specify the workflow-sensitive condition; the one-time maintainer credential therefore covers both, without claiming every tag/asset operation requires Workflows write. API errors never mean missing releases. Collection discovery is paginated; drafts are read by ID because release-by-tag 404 does not exclude a draft.

Use an existing **human maintainer/admin** account with a temporary, repository-scoped fine-grained token: **Contents: write**, **Workflows: write**, **Actions: write** (dispatch/read runs), and implicit **Metadata: read** (role checks). A classic token with `repo` and `workflow` scopes also covers these operations but is broader. Existing rulesets still apply; if they forbid the exact tag operation, stop and obtain ordinary authorized maintainer access without weakening protections.

Keep credentials in your normal secure local credential manager, or supply a temporary `GH_TOKEN` through a hidden terminal prompt. Never put credentials in a command argument, repository, generated file, log, or Actions secret. Revoke the temporary token afterwards. The tool checks the authenticated human's current `admin`/`maintain` repository role before writes; workflow dispatch checks both the initiating and rerunning actors using the metadata-only [permissions endpoint](https://docs.github.com/en/rest/collaborators/collaborators#get-repository-permissions-for-a-user).

## Normal automatic release path

Successful exact-source main push CI authorizes only that current SHA. Existing patch default and minor/major labels select the version. The shared Python orchestrator inspects/peels tags, discovers a unique draft, verifies metadata and existing bytes, explicitly creates a missing tag at the authorized commit, uploads only missing assets without clobbering, and verifies the complete reservation. OIDC publishes the verified packages; the same identity/tag/asset checks and exact PyPI hashes gate finalization. No writes to main occur.

Retries at the same source preserve the version, draft and hashes. Conflicts, duplicate records, malformed API responses, uncertain writes and moved main fail closed. Normal release jobs cannot select historical source through `MAIN_SHA`. A historical draft blocks automatic version selection with a clear one-time recovery message; it never silently becomes `1.3.4` or automatically mutates the old release. The merge's normal release attempt will therefore stop safely until this reservation is recovered.

## After merge: exact one-time procedure

**Nothing below has been executed against production during development.** Merge only after reviewing the PR and successful hosted CI. Wait for successful **main push CI** for the merged commit. Do not retry either old failed Release run: reruns use their original workflow definitions.

### 1. Authenticate locally and build the original source

Use a regular clone, not a linked worktree (a worktree's `.git` file can enter the sdist). Start with new directories:

```bash
git clone https://github.com/Skulldorom/flask-session.manager.sk.git release-recovery-policy
cd release-recovery-policy
git switch main
POLICY_DIR="$PWD"
REVIEWED_MAIN_SHA="$(gh api repos/Skulldorom/flask-session.manager.sk/git/ref/heads/main --jq .object.sha)"
test "$(git rev-parse HEAD)" = "$REVIEWED_MAIN_SHA"

git clone https://github.com/Skulldorom/flask-session.manager.sk.git ../v1.3.3-source
git -C ../v1.3.3-source checkout --detach 06b73bebaae8fa397db17a8aab2304ce3a9896f6
uv --directory ../v1.3.3-source sync --frozen --dev
test ! -e recovery-dist
export SETUPTOOLS_SCM_PRETEND_VERSION=1.3.3
export SOURCE_DATE_EPOCH="$(git -C ../v1.3.3-source show -s --format=%ct HEAD)"
uv --directory ../v1.3.3-source build --no-build-isolation --out-dir "$POLICY_DIR/recovery-dist"
python -m scripts.validate_release_artifacts \
  --dist recovery-dist --source-sha 06b73bebaae8fa397db17a8aab2304ce3a9896f6 \
  --version 1.3.3 --manifest recovery-dist/release-manifest.json
```

Use Python 3.11–3.13; the workflow uses 3.13. The operator commands need only Python's standard library and `gh`. This builds once from the original source. Keep the same verified directory for retries; do not rerun manifest generation into a populated directory.

### 2. Read-only inspect, validate and plan

```bash
python -m scripts.release_recovery inspect --main-sha "$REVIEWED_MAIN_SHA"
python -m scripts.release_recovery validate --main-sha "$REVIEWED_MAIN_SHA"
python -m scripts.release_recovery plan --main-sha "$REVIEWED_MAIN_SHA"
```

All three commands are read-only and perform the same full validation; `inspect` is the default action. They require trusted CI for both original source and reviewed main, original-source ancestry, a complete diff containing only enumerated release infrastructure paths, exact existing ID/source/version, original local package and manifest hashes, every remote asset's bytes and API digest, tag resolution, and exact PyPI state. Unknown application/build/dependency changes block.

Expected output includes `release_id: 448693106`, original `source_sha`, `draft: true`, `assets: "all three original hashes verified"`, `tag: "absent"`, and `pypi_state: "publish"`. The only reconciliation change is:

```text
create refs/tags/v1.3.3 at 06b73bebaae8fa397db17a8aab2304ce3a9896f6
```

There is no release creation, asset upload, deletion, overwrite or local PyPI publication. If the exact tag already exists, reconciliation is a no-op. A different tag target blocks.

### 3. Explicitly authorize tag reconciliation

Only after reviewing the read-only plan:

```bash
python -m scripts.release_recovery reconcile \
  --repository Skulldorom/flask-session.manager.sk --version 1.3.3 \
  --source-sha 06b73bebaae8fa397db17a8aab2304ce3a9896f6 \
  --release-id 448693106 --main-sha "$REVIEWED_MAIN_SHA" \
  --confirm 'reconcile Skulldorom/flask-session.manager.sk v1.3.3 06b73bebaae8fa397db17a8aab2304ce3a9896f6 448693106'
```

The command prints the plan, verifies human maintainer authorization, and reuses the tested release orchestrator in existing-only mode. Missing assets or a missing/wrong draft abort rather than triggering upload or creation. Every write rechecks remote evidence and current main. A concurrent matching tag is accepted only after verification; a conflicting tag is never moved. After interruption, rerun inspect/plan before the same explicit operation.

### 4. Hand off only package publication to Actions OIDC

Confirm the project's existing PyPI Trusted Publisher identifies **Skulldorom / flask-session.manager.sk / release.yml**, with no environment (the existing normal job uses none). The recovery job stays in that same workflow identity; it needs no new publisher, credential or elevated GitHub token. Publisher configuration cannot be inspected from this repository and remains an operator prerequisite.

```bash
gh workflow run release.yml --repo Skulldorom/flask-session.manager.sk --ref main \
  -f repository=Skulldorom/flask-session.manager.sk -f version=1.3.3 \
  -f source_sha=06b73bebaae8fa397db17a8aab2304ce3a9896f6 \
  -f release_id=448693106 -f main_sha="$REVIEWED_MAIN_SHA" \
  -f confirmation=publish-v1.3.3-448693106
```

This is a fixed one-time publication request, not an arbitrary tag/source/manual publishing bypass. The helper rejects every other repository, version, source, ID, confirmation, branch, event and workflow identity. It requires the workflow's own SHA and current main to equal the reviewed SHA, current maintainer/admin roles for both actors, and successful exact-source trusted main CI for both commits. It independently rebuilds only the pinned original source, validates package metadata, requires all three hard-coded original hashes, and verifies the repaired live reservation again immediately before publication. The job has **Contents: read**, **Actions: read**, **id-token: write**; it cannot create tags, upload assets or finalize releases. Its only uploader is the pinned Trusted Publishing action.

Already-published identical PyPI files skip uploading. Partial/conflicting PyPI files block without attempting repair. On success the job re-verifies PyPI and reports that local maintainer finalization remains. If interrupted before uploading, repeat the exact fixed request; do not rerun an old workflow definition. If main moved, stop and re-review current main/CI and infrastructure-only diff before a new request. Published releases are rejected by this handoff, making the fixed entry point inert after completion.

### 5. Verify PyPI, then finalize the same GitHub Release locally

```bash
python -m scripts.release_recovery validate --main-sha "$REVIEWED_MAIN_SHA"
python -m scripts.release_recovery finalize \
  --repository Skulldorom/flask-session.manager.sk --version 1.3.3 \
  --source-sha 06b73bebaae8fa397db17a8aab2304ce3a9896f6 \
  --release-id 448693106 --main-sha "$REVIEWED_MAIN_SHA" \
  --confirm 'finalize Skulldorom/flask-session.manager.sk v1.3.3 06b73bebaae8fa397db17a8aab2304ce3a9896f6 448693106'
```

Validation must now show `pypi_state: "already-published"`. Finalization requires exact PyPI hashes, all original assets, existing exact tag, unchanged reviewed main, CI evidence and a current authorized maintainer credential before PATCHing only `draft: false` on `448693106`. It never uploads to PyPI. If publication succeeded but this PATCH failed, retry the explicit local finalize operation after validation; already-finalized matching state is idempotent. Revoke the temporary credential afterwards. Future feature main CI releases run automatically with normal versioning.

## Validation and remaining limits

The original normal-release regression coverage remains, including first release, missing/annotated/conflicting tags, partial uploads, duplicate drafts, API failures and interrupted writes. New tests execute local commands and the actual YAML authorization, metadata validation, staging, reservation checks and post-publication checks against realistic GitHub/PyPI fixtures using the authentic original artifact bytes. They reject wrong maintainers, identities, main changes, missing/failed CI, conflicting assets and unverified dispatch contexts. PyPI upload itself is simulated in tests, never executed.

Hosted CI validates the final PR SHA on Python 3.11–3.13, lint, release policy, advisory policy and pinned React Chromium/Firefox compatibility. Passing these checks does not prove live publication. Live tag mutations, maintainer token permissions, effective tag rulesets, PyPI Trusted Publisher configuration, OIDC exchange and historical finalization remain unexecuted operator steps. If existing permissions or publisher configuration block them, stop and fix that precise configuration without storing extra credentials or weakening protections. There is a complete supported handoff; no personal PyPI upload fallback is required. Future historical releases other than this exact v1.3.3 are deliberately not authorized by this one-time interface.
