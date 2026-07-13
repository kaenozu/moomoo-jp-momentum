# moomoo read-only discovery handoff v1.2.2

## Purpose

This package inventories a candidate production Windows PC without modifying the runtime. It is a delivery wrapper for discovery operator v1.2.1 and does not complete Issue #27.

The package never authorizes or performs:

- SQLite connections
- writer, Scheduled Task, Service, or Startup changes
- Git checkout, reset, clean, merge, or configuration changes
- `-PreflightOnly`
- production backup or restore
- `-ConfirmProductionExecution`
- cutover
- OpenD trade context or real-order APIs

A successful exit still requires:

```text
production_readiness        = BLOCKED
preflight_authorized        = false
production_drill_authorized = false
cutover_authorized          = false
```

## Artifact trust boundary

Read `HANDOFF_MANIFEST.json` for the exact build identity:

- `source_commit`
- `expected_checkout_head`
- `expected_remote`
- operator bundle filename and SHA-256
- fixed authorization boundary

Only the GitHub Actions artifact produced by the **master push** run after cross-shell comparison is eligible for production use. Pull-request artifacts are test-only. A locally rebuilt ZIP is not the authoritative distribution artifact.

## 1. Verify the extracted package

Run in Windows PowerShell 5.1 or PowerShell 7:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\verify-handoff.ps1
```

Stop unless the command prints `Handoff verification PASS`.

The verifier checks:

- exact package file coverage
- SHA-256 of every package member
- handoff manifest schema and versions
- fixed non-authorization boundary
- operator bundle SHA-256
- operator ZIP duplicate entries and extraction integrity
- operator internal checksums and manifest source commit

## 2. Establish inputs from direct evidence

Do not guess any path. Confirm all of the following from Git, Scheduled Tasks, Services, Startup entries, process command lines, launch scripts, or another direct source:

- clean verified checkout at the manifest's exact HEAD
- protected production checkout
- config search root or roots
- production process working directory or directories
- a redacted evidence reference supporting the working directory
- an existing empty output root

The package directory, verified checkout, protected checkout, and output root must be mutually non-overlapping.

## 3. Run discovery once

Example syntax only:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-readonly-discovery.ps1 `
  -VerifiedCheckout "C:\verified-checkout" `
  -ProtectedCheckout "C:\production-checkout" `
  -ConfigSearchRoot "C:\production-runtime" `
  -ProductionWorkingDirectory "C:\production-runtime" `
  -ProductionWorkingDirectorySource "scheduled-task-review" `
  -ProductionWorkingDirectoryEvidence "Scheduled Task <redacted>, reviewed YYYY-MM-DD" `
  -OutputRoot "D:\moomoo-discovery-evidence"
```

The runner repeats package verification, checks the exact Git top-level, HEAD, origin, and clean working tree, validates Python 3.11+ and PyYAML, extracts the operator to a temporary directory, performs static validation, and runs only read-only discovery.

## 4. Review evidence

Only these three files are candidates for sharing, and only after visual inspection:

```text
03-discovery-redacted.json
04-discovery-summary.md
05-operator-result.json
```

Do not share:

```text
*.bin
00-manifest.json
01-gated-discovery.json
02-discovery-review.json
```

Exit codes:

- `0`: machine checks passed; human review remains required
- `1`: blocked
- `2`: correction required

Exit code `0` does not authorize preflight, backup, restore, writer shutdown, or cutover.
