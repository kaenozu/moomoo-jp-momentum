# Issue #27 next action gate

## Current decision

```text
repository_implementation     = PASS
master_release_status         = SUCCESS
master_handoff_status         = SUCCESS
production_readonly_discovery = NOT_RUN
human_validation              = NOT_RUN
production_preflight          = BLOCKED
full_recovery_drill           = BLOCKED
cutover                       = NOT_AUTHORIZED
issue_27                      = OPEN / PENDING
```

CI success and artifact verification do not identify the production host and do
not authorize any production operation.

## Correct evidence contract

Read-only discovery produces exactly these three shareable candidates:

```text
03-discovery-redacted.json
04-discovery-summary.md
05-operator-result.json
```

`04-validation.json` is not part of the v1.2.2 handoff contract. Treat any
handoff note that names it as `CORRECTION_REQUIRED`.

Human validation is a later and separate stage. Its outputs are:

```text
06-human-validation.json
07-preflight-eligibility.json
```

Even a successful human-validation result reaches only:

```text
ELIGIBLE_FOR_SEPARATE_PREFLIGHT_APPROVAL
```

It does not authorize `-PreflightOnly`.

## Next permitted preparation work

Before touching a candidate production Windows PC:

1. Download the authoritative artifact from the exact master-push workflow run.
2. Verify the GitHub Actions wrapper and inner handoff ZIP with
   `scripts/verify_moomoo_master_handoff_offline.py` outside production.
3. Copy and complete `DISCOVERY_EXECUTION_PACKET.template.json` using only direct
   evidence. Do not guess a host, path, launch source, or writer state.
4. Keep the packet at `BLOCKED_BEFORE_DISCOVERY` while any required field is
   `PENDING`, `INCONCLUSIVE`, `BLOCKED`, or `CORRECTION_REQUIRED`.
5. Obtain a separate, explicit approval using
   `READONLY_DISCOVERY_APPROVAL.template.md`.

## Read-only discovery approval boundary

A discovery approval may authorize only:

```text
verify-handoff.ps1
run-readonly-discovery.ps1 exactly once
read-only inventory collection
creation of evidence under the approved empty output root
```

It must not authorize:

```text
Git mutation
SQLite connections
-PreflightOnly
-ConfirmProductionExecution
writer stop/start
Scheduled Task, Service, or Startup changes
backup, copy, restore, or prune
cutover
OpenD trade context
real-order APIs
Issue #27 close
```

## Stop conditions

Return `BLOCKED BEFORE DISCOVERY` without running the discovery script when:

- the artifact or nested operator fails independent verification;
- the exact verified checkout HEAD or origin is not confirmed;
- the verified checkout is not clean;
- the production host, protected checkout, launch source, working directory, or
  config search root is ambiguous;
- the output root does not already exist and remain empty;
- any protected path overlaps another protected path;
- explicit read-only discovery approval is absent or broader than the allowed
  scope.

No later gate is implied by completion of an earlier gate.
