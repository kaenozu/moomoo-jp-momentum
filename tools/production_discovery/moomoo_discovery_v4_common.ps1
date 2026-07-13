function Invoke-Safe {
    param([Parameter(Mandatory = $true)][scriptblock]$Action)
    try {
        & $Action
    } catch {
        [pscustomobject]@{
            error_type = $_.Exception.GetType().FullName
            error = $_.Exception.Message
        }
    }
}

function Invoke-NativeRead {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [AllowNull()][string]$InputText = $null
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        if ($null -eq $InputText) {
            $raw = & $FilePath @Arguments 2>&1
        } else {
            $raw = $InputText | & $FilePath @Arguments 2>&1
        }
        $exitCode = $LASTEXITCODE
        [pscustomobject]@{
            exit_code = $exitCode
            output = @($raw | ForEach-Object { [string]$_ })
            invocation_error = $null
        }
    } catch {
        [pscustomobject]@{
            exit_code = $null
            output = @()
            invocation_error = $_.Exception.Message
        }
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Invoke-GitRead {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    Invoke-NativeRead -FilePath "git" -Arguments (@("-C", $Repository) + $Arguments)
}

function Convert-ToFullPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$BasePath
    )
    if ([IO.Path]::IsPathRooted($Path)) {
        return [IO.Path]::GetFullPath($Path)
    }
    return [IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}

function Get-CommandAvailability {
    $names = @(
        "git", "python", "Get-CimInstance", "Get-ScheduledTask",
        "Get-Disk", "Get-Partition", "Get-Volume", "Get-SmbMapping",
        "Get-SmbShare", "Get-SmbOpenFile", "Get-FileHash", "wsl.exe",
        "docker.exe", "quser.exe"
    )
    @(
        foreach ($name in $names) {
            $command = Get-Command $name -ErrorAction SilentlyContinue |
                Select-Object -First 1
            [pscustomobject]@{
                name = $name
                available = ($null -ne $command)
                command_type = if ($command) { [string]$command.CommandType } else { $null }
                source = if ($command) { $command.Source } else { $null }
                path = if ($command -and $command.PSObject.Properties.Name -contains "Path") {
                    $command.Path
                } else {
                    $null
                }
            }
        }
    )
}

function Get-ScriptIdentity {
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $script:DiscoveryScriptPath,
        [ref]$tokens,
        [ref]$parseErrors
    )
    $hash = (Get-FileHash -LiteralPath $script:DiscoveryScriptPath -Algorithm SHA256).Hash
    [pscustomobject]@{
        version = $script:DiscoveryScriptVersion
        path = $script:DiscoveryScriptPath
        sha256 = $hash
        microsoft_parser = [pscustomobject]@{
            parser_type = "System.Management.Automation.Language.Parser"
            parsed = ($null -ne $ast)
            error_count = @($parseErrors).Count
            errors = @(
                $parseErrors | ForEach-Object {
                    [pscustomobject]@{
                        message = $_.Message
                        start_line = $_.Extent.StartLineNumber
                        start_column = $_.Extent.StartColumnNumber
                        text = $_.Extent.Text
                    }
                }
            )
        }
    }
}

function Get-RepositorySnapshot {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return [pscustomobject]@{
            path = $Path
            exists = $false
            status = "BLOCKED"
            reason = "repository directory does not exist"
        }
    }

    $remote = Invoke-GitRead $Path @("remote", "get-url", "origin")
    $branch = Invoke-GitRead $Path @("branch", "--show-current")
    $head = Invoke-GitRead $Path @("rev-parse", "HEAD")
    $top = Invoke-GitRead $Path @("rev-parse", "--show-toplevel")
    $working = Invoke-GitRead $Path @("status", "--porcelain")

    $remoteValue = if ($remote.exit_code -eq 0) { ($remote.output -join "").Trim() } else { $null }
    $branchValue = if ($branch.exit_code -eq 0) { ($branch.output -join "").Trim() } else { $null }
    $headValue = if ($head.exit_code -eq 0) { ($head.output -join "").Trim() } else { $null }
    $topValue = if ($top.exit_code -eq 0) { ($top.output -join "").Trim() } else { $null }
    $dirtyLines = @(
        if ($working.exit_code -eq 0) {
            $working.output | Where-Object { $_ }
        }
    )

    $reads = @($remote, $branch, $head, $top, $working)
    $allGitReadsSucceeded = $true
    foreach ($read in $reads) {
        if ($read.invocation_error -or $read.exit_code -ne 0) {
            $allGitReadsSucceeded = $false
        }
    }
    $clean = ($working.exit_code -eq 0 -and $dirtyLines.Count -eq 0)
    $identityMatches = (
        $allGitReadsSucceeded -and
        $remoteValue -eq $ExpectedRemote -and
        $headValue -eq $ExpectedHead
    )
    $status = if (-not $allGitReadsSucceeded) {
        "INCONCLUSIVE"
    } elseif ($identityMatches -and $clean) {
        "PASS"
    } elseif (-not $clean) {
        "BLOCKED"
    } else {
        "FAIL"
    }

    [pscustomobject]@{
        path = [IO.Path]::GetFullPath($Path)
        exists = $true
        git_reads_succeeded = $allGitReadsSucceeded
        git_invocation_errors = @($reads | ForEach-Object { $_.invocation_error } | Where-Object { $_ })
        top_level = $topValue
        origin = $remoteValue
        expected_origin = $ExpectedRemote
        origin_matches = ($remoteValue -eq $ExpectedRemote)
        branch = $branchValue
        head = $headValue
        expected_head = $ExpectedHead
        head_matches = ($headValue -eq $ExpectedHead)
        clean = $clean
        working_tree_entries = $dirtyLines
        drill_script_exists = Test-Path -LiteralPath (
            Join-Path $Path "scripts\sqlite_backup_recovery_drill.ps1"
        ) -PathType Leaf
        status = $status
    }
}

function Read-SelectedYamlValues {
    param([Parameter(Mandatory = $true)][string]$ConfigPath)

    $pythonScript = @'
from __future__ import annotations
import json
import sys
from pathlib import Path

try:
    import yaml
except Exception as exc:
    print(json.dumps({"parse_error": f"PyYAML unavailable: {exc}"}, ensure_ascii=False))
    raise SystemExit(0)

path = Path(sys.argv[1]).resolve()
try:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(json.dumps({"parse_error": str(exc)}, ensure_ascii=False))
    raise SystemExit(0)

if not isinstance(payload, dict):
    print(json.dumps({"parse_error": "top-level YAML value is not a mapping"}, ensure_ascii=False))
    raise SystemExit(0)

def section(name):
    value = payload.get(name, {})
    return value if isinstance(value, dict) else {}

database = section("database")
backup = section("database_backup")
cycle = section("cycle_control")
scheduler = section("scheduler")
virtual_trade = section("virtual_trade")
paper_trade = section("paper_trade")

result = {
    "database_path_raw": database.get("path"),
    "database_backup_enabled": backup.get("enabled"),
    "database_backup_directory_raw": backup.get("directory"),
    "cycle_control_enabled": cycle.get("enabled"),
    "scheduler_enabled": scheduler.get("enabled"),
    "virtual_trade_enabled": virtual_trade.get("enabled"),
    "paper_trade_enabled": paper_trade.get("enabled"),
}
print(json.dumps(result, ensure_ascii=False))
'@

    $native = Invoke-NativeRead -FilePath "python" -Arguments @("-", $ConfigPath) -InputText $pythonScript
    if ($native.invocation_error) {
        return [pscustomobject]@{ parse_error = $native.invocation_error }
    }
    if ($native.exit_code -ne 0) {
        return [pscustomobject]@{
            parse_error = "python exited with code $($native.exit_code)"
            diagnostic = ($native.output -join "`n")
        }
    }
    try {
        return (($native.output -join "`n") | ConvertFrom-Json)
    } catch {
        return [pscustomobject]@{ parse_error = $_.Exception.Message }
    }
}
