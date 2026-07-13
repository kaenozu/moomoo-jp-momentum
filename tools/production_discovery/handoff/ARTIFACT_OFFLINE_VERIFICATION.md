# Offline verification of the master handoff artifact

## Purpose

`scripts/verify_moomoo_master_handoff_offline.py` independently validates the
v1.2.2 GitHub Actions artifact without extracting or executing package content.
It accepts either:

- the GitHub Actions artifact wrapper ZIP; or
- the inner `moomoo-readonly-discovery-handoff-v1.2.2.zip`.

The verifier checks the exact member sets, compressed-data integrity, duplicate
and case-collision rejection, traversal/rooted/nested-path rejection, checksum
coverage, manifests, source chain, versions, origin contract, distribution
policy, nested operator, and the blocked authorization boundary.

It performs no production discovery, SQLite connection, writer operation,
backup, restore, or cutover.

## Authoritative artifact selection

Use the artifact attached to the workflow run referenced by the
`moomoo/master-handoff` commit status on the exact master commit. A pull-request
artifact or locally rebuilt ZIP is validation-only.

GitHub Actions artifact wrappers and inner handoff ZIPs have different SHA-256
values. Record and verify both values rather than treating them as
interchangeable.

## Command

```powershell
python .\scripts\verify_moomoo_master_handoff_offline.py `
  .\moomoo-readonly-discovery-handoff-v1.2.2-actions-artifact.zip `
  --expected-input-sha256 <actions-wrapper-sha256> `
  --expected-handoff-sha256 <inner-handoff-sha256> `
  --expected-source-commit <40-character-master-sha> `
  --output .\handoff-offline-verification-report.json
```

The output path must not already exist. A successful report contains:

```text
status                           = PASS
production_execution_performed  = false
actions_wrapper                  = true or false
handoff_sha256                   = verified inner ZIP hash
operator.sha256                  = verified nested operator hash
```

## Validation-only CI use

Pull-request builds use a merge ref rather than `refs/heads/master`. CI may test
the verifier with `--allow-validation-ref`; production artifact verification
must not use that option.

## Decision rule

Any failure, unexpected member, mismatch, ambiguity, or unsupported version is
`CORRECTION_REQUIRED` or `BLOCKED`. Do not proceed to the candidate production
PC.
