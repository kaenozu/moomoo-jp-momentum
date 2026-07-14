from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


root = Path(__file__).resolve().parents[1]
drill = root / "scripts" / "sqlite_backup_recovery_drill.ps1"
windows_test = root / "tests" / "run_database_backup_drill_windows.ps1"

replace_once(
    drill,
    '''$JournalMode = ($journalRaw -join "").Trim()
$ProductionConfigHash = (
''',
    '''$JournalMode = ($journalRaw -join "").Trim()

$PortfolioInventoryScript = @'
from pathlib import Path
import json
import sqlite3
import sys

path = Path(sys.argv[1]).resolve()
selected = sys.argv[2]
tables = (
    "virtual_orders",
    "virtual_fills",
    "virtual_positions",
    "virtual_equity_curve",
)
inventory = {}
with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
    existing = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    for table in tables:
        if table not in existing:
            continue
        for portfolio, row_count in connection.execute(
            f"SELECT strategy_name, COUNT(*) FROM {table} GROUP BY strategy_name"
        ):
            name = str(portfolio)
            inventory.setdefault(name, {})[table] = int(row_count)

selected_rows = sum(inventory.get(selected, {}).values())
total_rows = sum(sum(counts.values()) for counts in inventory.values())
print(json.dumps({
    "selected": selected,
    "selected_rows": selected_rows,
    "total_rows": total_rows,
    "portfolios": inventory,
}, ensure_ascii=False, sort_keys=True))
'@

$portfolioInventoryRaw = $PortfolioInventoryScript | & $Python - `
    $LiveDb $Portfolio 2>&1
$portfolioInventoryExit = $LASTEXITCODE
if ($portfolioInventoryExit -ne 0) {
    throw ($portfolioInventoryRaw -join "`n")
}
$PortfolioInventory = ($portfolioInventoryRaw -join "`n") | ConvertFrom-Json
if (
    [int]$PortfolioInventory.selected_rows -eq 0 -and
    [int]$PortfolioInventory.total_rows -gt 0
) {
    throw (
        "selected virtual portfolio has no history while other portfolios do: " +
        "selected=$Portfolio available=" +
        (($PortfolioInventory.portfolios | ConvertTo-Json -Compress -Depth 6))
    )
}

$ProductionConfigHash = (
''',
)

replace_once(
    drill,
    '''    journal_mode = $JournalMode
    virtual_portfolio = $Portfolio
    filesystem_space = @(
''',
    '''    journal_mode = $JournalMode
    virtual_portfolio = $Portfolio
    selected_virtual_portfolio_rows = [int]$PortfolioInventory.selected_rows
    total_virtual_portfolio_rows = [int]$PortfolioInventory.total_rows
    virtual_portfolio_inventory = $PortfolioInventory.portfolios
    filesystem_space = @(
''',
)

replace_once(
    windows_test,
    '''if ([string]$preflight.live_db -ne (Resolve-Path $LiveDb).Path) {
    throw "preflight resolved the wrong live DB"
}
if (
''',
    '''if ([string]$preflight.live_db -ne (Resolve-Path $LiveDb).Path) {
    throw "preflight resolved the wrong live DB"
}
if ([string]$preflight.virtual_portfolio -ne "default") {
    throw "preflight reported the wrong virtual portfolio"
}
if ([int]$preflight.selected_virtual_portfolio_rows -ne 4) {
    throw "preflight reported the wrong selected portfolio row count"
}
if ([int]$preflight.total_virtual_portfolio_rows -ne 4) {
    throw "preflight reported the wrong total portfolio row count"
}
$DefaultInventory = $preflight.virtual_portfolio_inventory.default
if (
    [int]$DefaultInventory.virtual_orders -ne 1 -or
    [int]$DefaultInventory.virtual_fills -ne 1 -or
    [int]$DefaultInventory.virtual_positions -ne 1 -or
    [int]$DefaultInventory.virtual_equity_curve -ne 1
) {
    throw "preflight virtual portfolio inventory is incomplete"
}
if (
''',
)

print("portfolio preflight inventory applied")
