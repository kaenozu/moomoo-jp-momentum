## Summary

<!-- Explain why this change is needed. -->

## Validation

- [ ] Locked install completed with CPython 3.11.15 and pip 26.1.2
- [ ] `python -m pip check`
- [ ] `python scripts/verify_locked_requirements.py`
- [ ] Fast pytest result and skip count recorded
- [ ] Ruff
- [ ] Pyright
- [ ] Compileall
- [ ] Dry-run
- [ ] Skip-fetch acceptance
- [ ] Artifact hygiene
- [ ] `git diff --check`

## Dependency or Action updates

- [ ] Update is isolated in a dedicated PR
- [ ] Ubuntu and Windows resolution were compared
- [ ] All external Actions use immutable full commit SHAs with version comments
- [ ] Rollback commit is recorded
- [ ] Auto-merge is disabled

## Safety

- [ ] No REAL order, trade unlock, live DB, Secret, scheduler cutover, release, or Production operation
