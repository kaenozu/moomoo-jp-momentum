# Production discovery release and human-evidence gate

This document defines the gate between the read-only production discovery and
any separately approved recovery preflight for Issue #27.

## Artifact provenance

Use `moomoo_production_discovery_release_v1.2.1.zip`.

The release manifest is authoritative for distribution status:

- `MASTER_RELEASE_CANDIDATE`: built by a `push` workflow from
  `refs/heads/master`;
- `VALIDATION_ONLY`: built from a pull request, workflow dispatch, or any other
  source.

A `VALIDATION_ONLY` package must not be used to qualify production preflight.

The package keeps these values fixed:

```text
production_readiness        = BLOCKED
preflight_authorized        = false
production_drill_authorized = false
cutover_authorized          = false
```

When repository variable `ENABLE_ARTIFACT_ATTESTATION` is set to `true` and the
repository plan supports private-repository attestations, the master workflow
also runs `actions/attest@v4`. The release workflow remains valid without this
optional step; the release manifest and SHA-256 chain are always required.

## Verify the release ZIP

Retain the original release ZIP after extraction and run the included verifier:

```powershell
python .\compare_moomoo_discovery_releases.py `
  --left .\moomoo_production_discovery_release_v1.2.1.zip `
  --output .\release-verification.json
```

The verifier rejects corrupt or duplicate ZIP entries, unexpected or missing
members, incomplete `SHA256SUMS.txt` coverage, release-manifest inconsistencies,
and nested operator member, hash, manifest, or source inconsistencies.

## Required read-only evidence

Run the operator from the verified release package and retain:

```text
03-discovery-redacted.json
05-operator-result.json
```

Copy `human-validation.template.json` to a separate working file and complete
all checks using direct evidence. Do not mark a check `CONFIRMED` without both a
non-empty value and at least one evidence reference.

When `production_working_directory`, `active_config_path`, and
`resolved_live_database` are all `CONFIRMED`, the validator requires the three
values to identify one supported, existing mapping in
`03-discovery-redacted.json`. It does not accept values combined from different
runtime/config candidates.

## Human gate

Run:

```powershell
python .\validate_moomoo_human_validation.py `
  --human-validation .\human-validation.json `
  --operator-result .\05-operator-result.json `
  --discovery-redacted .\03-discovery-redacted.json `
  --release-manifest .\release-manifest.json `
  --output-dir .\evidence
```

The output directory must already exist. Existing output files are never
overwritten.

Outputs:

```text
06-human-validation.json
07-preflight-eligibility.json
```

`06-human-validation.json` can contain production paths and must not be
published without a separate redaction review.

Possible eligibility states:

- `ELIGIBLE_FOR_SEPARATE_PREFLIGHT_APPROVAL`
- `INCONCLUSIVE`
- `BLOCKED`
- `CORRECTION_REQUIRED`

Even the eligible state is not approval. A separate explicit instruction is
required before invoking `-PreflightOnly`. A further separate instruction is
required before the full recovery drill. Cutover requires another independent
authorization.
