# Git and contribution workflow

## Current repository

- `main` holds the initial tested project snapshot.
- `chore/git-workflow` contains the repository safeguards for review.
- The initial snapshot records existing work honestly; it does not reconstruct or backdate historical feature commits.
- No remote is configured or pushed by this setup. Local commits are not an off-device backup.

## Set up a working copy

```sh
make setup
make hooks
make check
make git-check
```

`make hooks` installs both pre-commit and pre-push hooks for this clone. Repeat it after cloning elsewhere. Hooks use the locked development dependencies; they do not download third-party hook repositories. Git uses your existing configured identity; the setup does not change global Git settings.

## Branches and commits

Use a short-lived topic branch from an up-to-date `main`:

```sh
git switch main
git switch -c feat/short-description
```

Use `feat/`, `fix/`, `docs/`, `test/`, or `chore/` prefixes. Do not create empty placeholder branches or a permanent `develop` branch for this small project. Once a remote exists, update `main` with `git pull --ff-only` before branching.

1. Keep changes focused. Add tests first for authorization, retrieval, citation, cache, and logging boundaries.
2. Review changes with `git --no-pager diff`.
3. Stage specific files with `git add -- path/to/file`; avoid blind bulk staging.
4. Review the actual commit with `git --no-pager diff --cached` and `git --no-pager diff --cached --check`.
5. Run `make check` and `make git-check`.
6. Commit with an imperative Conventional Commit subject, for example `feat: validate ingestion metadata` or `docs: explain local startup`. This is a documented convention, not a commit-message validator.
7. Push the topic branch only when a remote is configured and you intend to publish it. Open a pull request, obtain human review, and merge only after required CI passes.
8. Delete merged topic branches after review/merge. Never rewrite shared history or force-push `main`.

The first baseline commit is the bootstrap exception to topic-branch development. Keep `chore/git-workflow` unmerged until its changes receive human review.

## Local checks and their limits

Pre-commit runs:

- A filename guard for environment secrets, private keys, credential files, and local databases—even if force-added past `.gitignore`.
- `detect-secrets` against text files with network verification disabled.
- Ruff lint/format checks for staged application/test Python files.
- Strict application typing when application Python or dependency configuration changes.

Pre-push runs `make check` and `make git-check`. CI repeats repository checks, full tests, and package builds on Python 3.12 and 3.14. There is no automatic formatting or test weakening to make a commit pass.

Hooks are local safeguards, not an authorization/security boundary: Git permits bypassing them, and clones do not install them automatically. CI and server-side branch rules must enforce the shared policy. Secret scanners are heuristic and do not prove the absence of credentials; review diffs yourself. Binary PDFs are preserved, not content-scanned by the text hook.

## Secret baseline policy

`.secrets.baseline` contains hashes and reviewed false-positive classifications, not secret values. Its five original entries are synthetic test inputs:

- Three `tests/test_auth_api.py` entries: generated test-password prefixes and an invalid-request sentinel.
- One `tests/test_content.py` entry: intentionally rejected credentials in an `example.invalid` URL.
- One `tests/test_identity_store.py` entry: an intentionally corrupt password-hash sentinel.

The frontend milestone adds three individually inspected synthetic findings in `tests/test_ui_client.py`: the expected JSON test password, credentials in a deliberately rejected localhost URL, and the prefix of a randomly generated integration-test password. Only these exact hashes are marked false positives; no detector or test-file exclusion was added. Review these additions with the frontend branch.

Do not regenerate/approve a baseline automatically just to clear a failure. Review each new finding. If it is real, remove and rotate/revoke the credential; deleting the current line does not remove it from Git history. Any necessary history cleanup must be coordinated before rewriting shared commits.

To inspect a newly generated candidate baseline without overwriting approved exceptions:

```sh
uv run --frozen detect-secrets scan --no-verify > .cache/secrets-candidate.json
```

The scan defaults to Git-tracked files; do not add `--all-files` and scan local credentials or frozen evaluation material. Never disable broad detectors or exclude entire test directories to silence a finding. Baseline changes require explicit reviewer attention.

## Files that must stay local

`.gitignore` excludes environments, caches, build output, `data/`, database sidecars, private keys, local backups, and logs. It does not remove an already tracked file; inspect the staged diff before committing.

An `.env.example` is permitted only after a human verifies it contains placeholders, not real values. The existing protected `.env.example` was not read, staged, or committed during this setup and remains untracked pending that review. Do not force-add local `.env` files. Source distributions explicitly exclude `.env` and `.env.*`, including the unreviewed example.

Original PDFs and user-authored documentation must remain intact. `.gitattributes` marks PDFs binary and preserves the historical understanding document's encoding, line endings, and existing whitespace.

## Hosting setup still required

When the owner chooses a hosting service/repository:

1. Configure the real remote URL and publish branches intentionally; none is invented here.
2. Set `main` as the default branch.
3. Protect `main`: require pull requests, at least one human approval, resolved conversations, and successful matrix checks; block force pushes and deletion.
4. Enable hosting secret scanning/push protection and private vulnerability reporting where available.
5. Require review for security tests, baseline changes, authorization semantics, dependency/model changes, and ingestion licensing.

These hosting controls are recommendations until configured on the actual remote. This local setup cannot enable or verify them.

## Inspect progress

```sh
git --no-optional-locks status --short --branch
git --no-pager log --oneline --graph --decorate --all
git --no-pager diff --cached --stat
git --no-pager diff main...chore/git-workflow --stat
```

The cached diff shows staged, uncommitted work; the three-dot branch diff shows only committed changes.

Project security gates in `AGENTS.md` still apply. Passing tests does not establish universal security or production readiness.
