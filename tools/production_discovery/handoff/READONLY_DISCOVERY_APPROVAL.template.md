# Separate approval: production read-only discovery only

## Target and immutable inputs

```text
candidate production host:
authoritative workflow run ID:
authoritative artifact ID:
actions wrapper SHA-256:
inner handoff SHA-256:
manifest source commit:
verified checkout:
protected checkout:
config search root(s):
production working directory/direct evidence:
existing empty output root:
execution packet path/SHA-256:
```

Every field above must be completed from direct evidence. An ambiguous or
inferred value keeps the request `BLOCKED BEFORE DISCOVERY`.

## Requested authorization

The requested authorization is limited to:

1. Run `verify-handoff.ps1` against the extracted authoritative handoff.
2. If and only if verification prints PASS, run
   `run-readonly-discovery.ps1` exactly once with the immutable inputs above.
3. Permit read-only inventory and evidence creation only under the approved
   existing empty output root.
4. Stop after generating and visually reviewing these shareable candidates:

```text
03-discovery-redacted.json
04-discovery-summary.md
05-operator-result.json
```

## Explicitly prohibited

This approval does not permit:

- Git clone, fetch, pull, checkout, switch, reset, clean, stash, merge, or config
  changes;
- SQLite connections;
- `-PreflightOnly` or `-ConfirmProductionExecution`;
- starting, stopping, restarting, or modifying any writer, process, Scheduled
  Task, Service, or Startup entry;
- backup, secondary copy, restore, prune, corruption drill, or cutover;
- OpenD trade context or any real-order API;
- uploading unredacted evidence;
- closing Issue #27.

## Required fail-closed result

```text
production_readiness        = BLOCKED
preflight_authorized        = false
production_drill_authorized = false
cutover_authorized          = false
separate_approval_required  = true
```

## Decision

Select exactly one decision and complete the identity and timestamp fields.

```text
[ ] APPROVED_FOR_READONLY_DISCOVERY_EXACTLY_ONCE
[ ] NOT_APPROVED
[ ] CORRECTION_REQUIRED
[ ] BLOCKED

approved/reviewed by:
role:
RFC3339 timestamp with timezone:
approval statement:
```

An empty checkbox, multiple selections, missing identity, missing timestamp, or
an approval statement broader than the requested authorization means
`NOT_APPROVED`.
