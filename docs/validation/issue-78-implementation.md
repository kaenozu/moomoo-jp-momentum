# Issue #78 implementation baseline

## Source

- Base branch: `master`
- Base SHA: `e15458ec58091bdc69b44f9a8eb95749571d5b4a`
- Implementation branch: `agent/78-reproducible-ci`

## Successful environment used to establish pins

The initial constraints were derived from GitHub Actions run `31061940941`, which completed successfully for PR #74 before this implementation.

- Runner: Ubuntu 24.04
- CPython: 3.11.15
- pip: 26.1.2
- Tests: 198 passed, 20 skipped, 1 deselected
- Ruff: passed
- Pyright: 0 errors, 0 warnings
- Dry-run: passed
- Isolated skip-fetch acceptance: passed
- Artifact hygiene: passed

The Windows locked-install job added by this change is the cross-platform acceptance gate for the committed constraints. A successful Linux baseline alone is not treated as proof of Windows compatibility.

## Pinned external Actions

- `actions/checkout` v4.3.1: `34e114876b0b11c390a56381ad16ebd13914f8d5`
- `actions/setup-python` v5.6.0: `a26af69be951a213d495a4c3e4e4022e16d87065`

## Safety boundary

This implementation only changes dependency installation, CI verification, tests, and documentation. It does not connect to OpenD trade context, send REAL orders, use live databases, read Secrets, create releases, or perform Production operations.
