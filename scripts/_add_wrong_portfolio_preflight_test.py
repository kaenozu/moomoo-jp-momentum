from pathlib import Path

path = Path(__file__).resolve().parents[1] / "tests" / "run_database_backup_drill_windows.ps1"
text = path.read_text(encoding="utf-8")
old = '''Assert-PathsAbsent @(
    $MissingContextEvidence,
    $MissingContextSecondary,
    $MissingContextRestore
)

# Wrong SHA must fail before any drill path is created.
'''
new = '''Assert-PathsAbsent @(
    $MissingContextEvidence,
    $MissingContextSecondary,
    $MissingContextRestore
)

# Selecting an empty portfolio while another portfolio has history must fail
# before any drill output path is created.
$WrongPortfolioEvidence = Join-Path $Root "wrong portfolio evidence"
$WrongPortfolioSecondary = Join-Path $Root "wrong portfolio secondary"
$WrongPortfolioRestore = Join-Path $Root "wrong portfolio restore\\restored.db"
$wrongPortfolioResult = Invoke-DrillProcess -Arguments @(
    "-File", $DrillScript,
    "-ExpectedHead", $HeadSha,
    "-ProductionConfig", $Config,
    "-ProductionWorkingDirectory", $ProductionWorkingDirectory,
    "-EvidenceDir", $WrongPortfolioEvidence,
    "-SecondaryDir", $WrongPortfolioSecondary,
    "-RestorePath", $WrongPortfolioRestore,
    "-Portfolio", "momentum",
    "-PreflightOnly"
)
Assert-Failure $wrongPortfolioResult "wrong-portfolio preflight guard"
Assert-PathsAbsent @(
    $WrongPortfolioEvidence,
    $WrongPortfolioSecondary,
    $WrongPortfolioRestore
)

# Wrong SHA must fail before any drill path is created.
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one insertion point, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
