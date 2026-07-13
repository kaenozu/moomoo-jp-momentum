# Local AI agent prompt: moomoo read-only discovery handoff v1.2.2

Use this prompt only on the candidate Windows PC after extracting the authoritative master-push GitHub Actions artifact.

## Objective

Collect read-only evidence for the production host, active launch source, working directory, config candidates, database/WAL/SHM candidates, writer candidates, storage, WSL, Docker, sessions, Services, Scheduled Tasks, Startup entries, and repository identity before considering Issue #27 preflight.

## Prohibited actions

- Do not connect to SQLite.
- Do not start, stop, restart, or modify a process, writer, Scheduled Task, Service, or Startup entry.
- Do not modify DB, WAL, SHM, config, repository files, or Git state.
- Do not checkout, pull, fetch, reset, stash, clean, switch, merge, or create a junction or symlink.
- Do not run `-PreflightOnly`.
- Do not run backup, restore, `-ConfirmProductionExecution`, or cutover.
- Do not instantiate OpenD trade context or call real-order APIs.
- Do not upload unredacted evidence.

## Required method

1. Run `verify-handoff.ps1`. Stop on any result other than PASS.
2. Read `HANDOFF_MANIFEST.json` and record the exact expected checkout HEAD and remote.
3. Identify the verified checkout, protected checkout, config search roots, production working directory, and direct evidence for that working directory.
4. If any value is ambiguous, contradictory, or inferred rather than evidenced, report `BLOCKED BEFORE DISCOVERY` and stop.
5. Prepare an existing empty output root that does not overlap the package or either checkout.
6. Run `run-readonly-discovery.ps1` exactly once.
7. Confirm the process exit code matches `05-operator-result.json`.
8. Confirm all authorization fields remain fail-closed.
9. Visually inspect only the three shareable evidence files.
10. Do not proceed to preflight or the recovery drill.

## Required report format

### Result

Use one of:

- `completed_readonly_discovery`
- `completed_with_corrections_required`
- `blocked`
- `BLOCKED BEFORE DISCOVERY`

### Confirmed facts

Only directly observed command, process, task, service, file, storage, and repository evidence.

### Unconfirmed information

List each unresolved item and why it could not be confirmed.

### Inferences

Separate all inferences from facts. An inferred value cannot authorize preflight.

### Inputs used

Record the verified checkout, protected checkout, config search roots, working directories, evidence reference, output root, manifest expected HEAD, and operator bundle SHA-256.

### Authorization boundary

Copy the four authorization values from `05-operator-result.json` and confirm they remain blocked/false.

### Shareable files

Return only visually reviewed copies of:

```text
03-discovery-redacted.json
04-discovery-summary.md
05-operator-result.json
```

## Version contract

- operator version: `1.2.2`
- handoff package version: `1.2.2`
- handoff format version: `1`

これらは別々のversion軸です。機械検証PASSでもpreflightは承認されません。
