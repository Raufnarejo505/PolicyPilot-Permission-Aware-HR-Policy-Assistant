# 0004 — Git repository and contribution safeguards

Date: 2026-10-19
Status: implemented locally on a topic branch; hosting/merge review pending

## Authorization and history

The user explicitly requested Git best practices, commits, and branches. The project was not a Git repository. Initialize `main`, record the existing tested project as one honest baseline snapshot, and create `chore/git-workflow` for this change. Do not fabricate historical commits, configure an invented remote, push, or change the user's global Git identity. Retain the topic branch unmerged for human review.

## Choices

- Use a lightweight main/topic-branch and pull-request workflow, documented in `CONTRIBUTING.md`. Conventional Commit subjects are a convention, not a validator. No redundant long-lived develop branch, arbitrary release tag, or empty feature branches are needed.
- Preserve source PDFs byte-for-byte using Git binary attributes. Preserve the historical understanding document's original encoding, line endings, and existing whitespace without editing it.
- Extend `.gitignore` for databases/sidecars, private keys, local backups, logs, and workstation files. A pre-commit filename guard independently rejects common sensitive filenames even if force-added. These are defense-in-depth safeguards, not exhaustive credential classification.
- Pin development-only `pre-commit==4.3.0` to run repository-owned hooks without downloading external hook repositories. Install pre-commit and pre-push hooks via `make hooks`; installation is per clone.
- Pin development-only `detect-secrets==1.5.0` for maintained heuristic secret scanning. Disable network verification to avoid sending candidate values to providers. The exact five initial findings were inspected and found to be synthetic test fixtures; `.secrets.baseline` records their hashes and false-positive classifications. No test contents or authorization semantics change. New baseline entries require review, not automatic acceptance.
- Add `make git-check`, pre-commit lint/format/typing/scanning, and a pre-push full `make check` plus repository checks. Keep application checks runnable separately.
- Add a pull-request checklist and monthly GitHub Actions dependency update configuration. Pin the existing checkout v4, setup-python v5, and setup-uv v6 aliases to upstream-verified immutable commit SHAs. Preserve uv 0.11.25, least-privilege CI permissions, and the Python 3.12/3.14 matrix; disable persisted checkout credentials and cancel superseded runs.
- Exclude `.env` and `.env.*` from source distributions: the previous manifest explicitly included the unreviewed local `.env.example`. Package metadata/file-name inspection exposed this; no configuration contents were inspected. Rebuild artifacts and verify their member names after correcting the manifest.
- The protected existing `.env.example` is intentionally neither inspected nor committed. Human placeholder review is required before tracking it. No local databases, environment secrets, credentials, or generated caches/build output are staged.

## Validation and limits

Run `make check`, `make git-check`, configuration validation, and hook negative controls using disposable synthetic files. Verify the source PDFs and historical document still match their committed bytes. Remote CI, required reviews, branch protections, hosted secret scanning/push protection, and off-device backups cannot be established without a real remote; document them rather than claiming they exist.

Local hooks can be bypassed and secret scanners can miss credentials. Reviewed baseline hashes ignore exact known fixture values in those files, not arbitrary future secrets or entire test directories. These safeguards do not establish universal security. Existing corpus/model/evaluation approval gates remain unchanged.
