# Read-only discovery evidence review checklist

## Package and provenance

- [ ] The package came from the GitHub Actions **master push** artifact after cross-shell comparison.
- [ ] `verify-handoff.ps1` printed PASS.
- [ ] `HANDOFF_MANIFEST.json` has `handoff_version = 1.2.2`.
- [ ] `HANDOFF_MANIFEST.json` has `operator_version = 1.2.1`.
- [ ] `source_commit` equals `expected_checkout_head`.
- [ ] The verified checkout HEAD equals `expected_checkout_head`.
- [ ] The verified checkout is the exact Git top-level and is clean.
- [ ] The normalized origin matches `expected_remote`.
- [ ] The operator bundle SHA-256 matches the manifest.

## Path separation

- [ ] Package root and verified checkout do not overlap.
- [ ] Package root and protected checkout do not overlap.
- [ ] Package root and output root do not overlap.
- [ ] Verified and protected checkouts do not overlap.
- [ ] Output root does not overlap either checkout.
- [ ] Output root existed and was empty before execution.

## Read-only boundary

- [ ] No SQLite connection occurred.
- [ ] No writer, process, Scheduled Task, Service, or Startup state changed.
- [ ] No Git or config mutation occurred.
- [ ] `-PreflightOnly` was not executed.
- [ ] Production backup and restore were not executed.
- [ ] Cutover was not executed.
- [ ] No OpenD trade context or real-order API was used.

## Operator result

- [ ] Exactly one evidence directory was created.
- [ ] `03-discovery-redacted.json` exists.
- [ ] `04-discovery-summary.md` exists.
- [ ] `05-operator-result.json` exists.
- [ ] Process exit code equals `operator_exit_code`.
- [ ] `production_readiness = BLOCKED`.
- [ ] `preflight_authorized = false`.
- [ ] `production_drill_authorized = false`.
- [ ] `cutover_authorized = false`.

## False-success review

- [ ] The actual production host is directly evidenced.
- [ ] The active launch source is directly evidenced.
- [ ] The production working directory is directly evidenced.
- [ ] The active config is identified without guessing.
- [ ] Raw `database.path` and resolved database path correspond.
- [ ] DB/WAL/SHM candidates are not being confused with stale copies.
- [ ] Scheduled Task, Service, Startup, manual, and current-process writers were reviewed.
- [ ] Other-user sessions, WSL, Docker, and other-host writers were reviewed.
- [ ] Secondary storage failure domain and capacity are confirmed.
- [ ] Writer stop/restart procedure, single-host condition, and no-write window are confirmed.

Any unresolved item keeps preflight unauthorized.

## Sharing

- [ ] `03-discovery-redacted.json` was visually reviewed.
- [ ] `04-discovery-summary.md` was visually reviewed.
- [ ] `05-operator-result.json` was visually reviewed.
- [ ] No `*.bin` file is shared.
- [ ] `00-manifest.json` is not shared.
- [ ] `01-gated-discovery.json` is not shared.
- [ ] `02-discovery-review.json` is not shared.
