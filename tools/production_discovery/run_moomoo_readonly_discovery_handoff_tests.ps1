[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$HandoffZip,

    [string]$PythonExecutable = "python",

    [string]$PowerShellExecutable = "powershell.exe",

    [string]$DiagnosticsDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = [IO.Path]::GetFullPath($PSScriptRoot)
$RepoRoot = [IO.Path]::GetFullPath((
    & git -C $Root rev-parse --show-toplevel
).Trim())
if ($LASTEXITCODE -ne 0) {
    throw "Could not resolve repository root"
}
$ExpectedHead = (& git -C $RepoRoot rev-parse HEAD).Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0) {
    throw "Could not resolve repository HEAD"
}
$ExpectedRemote = (& git -C $RepoRoot remote get-url origin).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Could not resolve repository origin"
}
$HandoffZip = [IO.Path]::GetFullPath($HandoffZip)
if (-not (Test-Path -LiteralPath $HandoffZip -PathType Leaf)) {
    throw "Handoff ZIP does not exist: $HandoffZip"
}

$RequestedEngine = [IO.Path]::GetFileName($PowerShellExecutable).ToLowerInvariant()
if ($RequestedEngine -eq "powershell.exe" -and $PSVersionTable.PSVersion.Major -ne 5) {
    throw "powershell.exe validation must run inside Windows PowerShell 5.1"
}
if ($RequestedEngine -eq "pwsh.exe" -and $PSVersionTable.PSVersion.Major -lt 7) {
    throw "pwsh.exe validation must run inside PowerShell 7 or newer"
}

if ($DiagnosticsDir) {
    $DiagnosticsDir = [IO.Path]::GetFullPath($DiagnosticsDir)
    [IO.Directory]::CreateDirectory($DiagnosticsDir) | Out-Null
}

function Write-DiagnosticText {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowNull()][string]$Text
    )
    if (-not $DiagnosticsDir) { return }
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        (Join-Path $DiagnosticsDir $Name),
        [string]$Text,
        $Encoding
    )
}

function Invoke-NativeCapture {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $RepoRoot,
        [hashtable]$Environment = @{}
    )
    $Saved = @{}
    foreach ($Key in $Environment.Keys) {
        $Saved[$Key] = [Environment]::GetEnvironmentVariable($Key, "Process")
        [Environment]::SetEnvironmentVariable(
            $Key,
            [string]$Environment[$Key],
            "Process"
        )
    }
    $PreviousPreference = $ErrorActionPreference
    $Raw = @()
    $ExitCode = $null
    Push-Location $WorkingDirectory
    try {
        $ErrorActionPreference = "Continue"
        $Raw = @(& $FilePath @Arguments 2>&1)
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
        Pop-Location
        foreach ($Key in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable(
                $Key,
                $Saved[$Key],
                "Process"
            )
        }
    }
    $Lines = @(
        $Raw | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.ToString()
            } else {
                [string]$_
            }
        }
    )
    $Text = $Lines -join [Environment]::NewLine
    Write-DiagnosticText -Name ($Name + ".log") -Text $Text
    [pscustomobject]@{
        exit_code = [int]$ExitCode
        lines = $Lines
        text = $Text
    }
}

function Assert-Success {
    param(
        [Parameter(Mandatory = $true)]$Result,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ([int]$Result.exit_code -ne 0) {
        throw "$Name failed with exit code $($Result.exit_code). Output: $($Result.text)"
    }
}

function Assert-Failure {
    param(
        [Parameter(Mandatory = $true)]$Result,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ([int]$Result.exit_code -eq 0) {
        throw "$Name unexpectedly succeeded. Output: $($Result.text)"
    }
}

function New-EmptyDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    [IO.Directory]::CreateDirectory($Path) | Out-Null
    return [IO.Path]::GetFullPath($Path)
}

function Copy-Package {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse
    return [IO.Path]::GetFullPath($Destination)
}

function Rewrite-HandoffChecksums {
    param([Parameter(Mandatory = $true)][string]$Package)
    $Rows = @(
        Get-ChildItem -LiteralPath $Package -File |
            Where-Object { $_.Name -ne "HANDOFF_SHA256SUMS.txt" } |
            Sort-Object Name |
            ForEach-Object {
                $Hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                "$Hash  $($_.Name)"
            }
    )
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        (Join-Path $Package "HANDOFF_SHA256SUMS.txt"),
        (($Rows -join "`n") + "`n"),
        $Encoding
    )
}

function New-CheckoutClone {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Commit,
        [Parameter(Mandatory = $true)][string]$Remote
    )
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    $Clone = Invoke-NativeCapture -Name "clone-$([IO.Path]::GetFileName($Path))" `
        -FilePath "git" `
        -Arguments @("clone", "--no-hardlinks", "--no-checkout", $RepoRoot, $Path)
    Assert-Success -Result $Clone -Name "git clone"
    $Checkout = Invoke-NativeCapture -Name "checkout-$([IO.Path]::GetFileName($Path))" `
        -FilePath "git" `
        -Arguments @("-C", $Path, "checkout", "--detach", $Commit)
    Assert-Success -Result $Checkout -Name "git checkout"
    $SetRemote = Invoke-NativeCapture -Name "remote-$([IO.Path]::GetFileName($Path))" `
        -FilePath "git" `
        -Arguments @("-C", $Path, "remote", "set-url", "origin", $Remote)
    Assert-Success -Result $SetRemote -Name "git remote set-url"
    return [IO.Path]::GetFullPath($Path)
}

function New-RunnerArguments {
    param(
        [Parameter(Mandatory = $true)][string]$Runner,
        [Parameter(Mandatory = $true)][string]$Verified,
        [Parameter(Mandatory = $true)][string]$Protected,
        [Parameter(Mandatory = $true)][string]$Runtime,
        [Parameter(Mandatory = $true)][string]$Output,
        [string]$Python = $PythonExecutable
    )
    return @(
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $Runner,
        "-VerifiedCheckout", $Verified,
        "-ProtectedCheckout", $Protected,
        "-ConfigSearchRoot", $Runtime,
        "-ProductionWorkingDirectory", $Runtime,
        "-ProductionWorkingDirectorySource", "scheduled-task-review",
        "-ProductionWorkingDirectoryEvidence", "synthetic CI evidence",
        "-OutputRoot", $Output,
        "-PythonExecutable", $Python,
        "-PowerShellExecutable", $PowerShellExecutable
    )
}

$TempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    "moomoo-handoff-tests-" + [Guid]::NewGuid().ToString("N")
)
$Package = Join-Path $TempRoot "package"
$Protected = Join-Path $TempRoot "protected"
$Runtime = Join-Path $TempRoot "runtime"
$Output = Join-Path $TempRoot "output"
$ShimPath = Join-Path $Root "handoff_test_python_shim.py"

try {
    [IO.Directory]::CreateDirectory($TempRoot) | Out-Null
    Expand-Archive -LiteralPath $HandoffZip -DestinationPath $Package -Force
    $Protected = New-CheckoutClone `
        -Path $Protected `
        -Commit $ExpectedHead `
        -Remote $ExpectedRemote
    New-EmptyDirectory -Path (Join-Path $Runtime "data") | Out-Null
    New-EmptyDirectory -Path $Output | Out-Null
    [IO.File]::WriteAllBytes(
        (Join-Path $Runtime "data\moomoo.db"),
        [byte[]](1, 2, 3, 4)
    )
    @"
database:
  path: data/moomoo.db
database_backup:
  enabled: true
  directory: backups
cycle_control:
  enabled: true
virtual_trade:
  enabled: true
"@ | Set-Content -LiteralPath (Join-Path $Runtime "config.yaml") -Encoding UTF8

    $Verify = Join-Path $Package "verify-handoff.ps1"
    $Runner = Join-Path $Package "run-readonly-discovery.ps1"
    foreach ($Path in @($Verify, $Runner)) {
        $Tokens = $null
        $Errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $Path,
            [ref]$Tokens,
            [ref]$Errors
        ) | Out-Null
        if (@($Errors).Count -ne 0) {
            throw "PowerShell parser errors in ${Path}: $(@($Errors).Message -join '; ')"
        }
    }

    $VerifyResult = Invoke-NativeCapture -Name "verify-positive" `
        -FilePath $PowerShellExecutable `
        -Arguments @(
            "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", $Verify
        )
    Assert-Success -Result $VerifyResult -Name "positive handoff verification"

    $BeforeTemp = @(
        Get-ChildItem -LiteralPath ([IO.Path]::GetTempPath()) -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "moomoo-readonly-discovery-*" } |
            ForEach-Object { $_.FullName }
    )
    $Positive = Invoke-NativeCapture -Name "runner-positive" `
        -FilePath $PowerShellExecutable `
        -Arguments (New-RunnerArguments `
            -Runner $Runner `
            -Verified $RepoRoot `
            -Protected $Protected `
            -Runtime $Runtime `
            -Output $Output)
    Assert-Success -Result $Positive -Name "positive handoff runner"
    $EvidenceDirectories = @(Get-ChildItem -LiteralPath $Output -Directory)
    if ($EvidenceDirectories.Count -ne 1) {
        throw "Positive runner did not create exactly one evidence directory"
    }
    foreach ($Name in @(
        "03-discovery-redacted.json",
        "04-discovery-summary.md",
        "05-operator-result.json"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $EvidenceDirectories[0].FullName $Name) -PathType Leaf)) {
            throw "Positive runner missing shareable evidence: $Name"
        }
    }
    $AfterTemp = @(
        Get-ChildItem -LiteralPath ([IO.Path]::GetTempPath()) -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "moomoo-readonly-discovery-*" } |
            ForEach-Object { $_.FullName }
    )
    $Leaked = @($AfterTemp | Where-Object { $BeforeTemp -notcontains $_ })
    if ($Leaked.Count -ne 0) {
        throw "Runner leaked temporary extraction directories: $($Leaked -join ', ')"
    }

    $Tampered = Copy-Package -Source $Package -Destination (Join-Path $TempRoot "tampered-file")
    Add-Content -LiteralPath (Join-Path $Tampered "README_FIRST.md") -Value "tampered"
    $TamperedResult = Invoke-NativeCapture -Name "negative-tampered-file" `
        -FilePath $PowerShellExecutable `
        -Arguments @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Tampered "verify-handoff.ps1"))
    Assert-Failure -Result $TamperedResult -Name "tampered handoff file"

    $TamperedBundle = Copy-Package -Source $Package -Destination (Join-Path $TempRoot "tampered-bundle")
    Add-Content -LiteralPath (Join-Path $TamperedBundle "moomoo_production_discovery_operator_v4_v1.2.1.zip") -Value "tampered"
    $TamperedBundleResult = Invoke-NativeCapture -Name "negative-tampered-bundle" `
        -FilePath $PowerShellExecutable `
        -Arguments @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $TamperedBundle "verify-handoff.ps1"))
    Assert-Failure -Result $TamperedBundleResult -Name "tampered operator bundle"

    foreach ($BoundaryCase in @(
        @("production_readiness", "READY"),
        @("preflight_authorized", $true),
        @("production_drill_authorized", $true),
        @("cutover_authorized", $true)
    )) {
        $CaseName = [string]$BoundaryCase[0]
        $BoundaryPackage = Copy-Package -Source $Package -Destination (Join-Path $TempRoot ("boundary-" + $CaseName))
        $ManifestPath = Join-Path $BoundaryPackage "HANDOFF_MANIFEST.json"
        $Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $Manifest.authorization.PSObject.Properties[$CaseName].Value = $BoundaryCase[1]
        $Encoding = New-Object System.Text.UTF8Encoding($false)
        [IO.File]::WriteAllText(
            $ManifestPath,
            (($Manifest | ConvertTo-Json -Depth 20) + "`n"),
            $Encoding
        )
        Rewrite-HandoffChecksums -Package $BoundaryPackage
        $BoundaryResult = Invoke-NativeCapture -Name ("negative-boundary-" + $CaseName) `
            -FilePath $PowerShellExecutable `
            -Arguments @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $BoundaryPackage "verify-handoff.ps1"))
        Assert-Failure -Result $BoundaryResult -Name "authorization boundary $CaseName"
    }

    $WrongHead = (& git -C $RepoRoot rev-parse "${ExpectedHead}^").Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) {
        throw "Could not resolve parent commit for wrong-HEAD test"
    }
    $WrongCheckout = New-CheckoutClone `
        -Path (Join-Path $TempRoot "wrong-head-checkout") `
        -Commit $WrongHead `
        -Remote $ExpectedRemote
    $WrongOutput = New-EmptyDirectory -Path (Join-Path $TempRoot "wrong-head-output")
    $WrongHeadResult = Invoke-NativeCapture -Name "negative-wrong-head" `
        -FilePath $PowerShellExecutable `
        -Arguments (New-RunnerArguments `
            -Runner $Runner `
            -Verified $WrongCheckout `
            -Protected $Protected `
            -Runtime $Runtime `
            -Output $WrongOutput)
    Assert-Failure -Result $WrongHeadResult -Name "wrong checkout HEAD"

    $DirtyCheckout = New-CheckoutClone `
        -Path (Join-Path $TempRoot "dirty-checkout") `
        -Commit $ExpectedHead `
        -Remote $ExpectedRemote
    Set-Content -LiteralPath (Join-Path $DirtyCheckout "UNTRACKED_HANDOFF_TEST.txt") -Value "dirty"
    $DirtyOutput = New-EmptyDirectory -Path (Join-Path $TempRoot "dirty-output")
    $DirtyResult = Invoke-NativeCapture -Name "negative-dirty-checkout" `
        -FilePath $PowerShellExecutable `
        -Arguments (New-RunnerArguments `
            -Runner $Runner `
            -Verified $DirtyCheckout `
            -Protected $Protected `
            -Runtime $Runtime `
            -Output $DirtyOutput)
    Assert-Failure -Result $DirtyResult -Name "dirty checkout"

    $WrongRemoteCheckout = New-CheckoutClone `
        -Path (Join-Path $TempRoot "wrong-remote-checkout") `
        -Commit $ExpectedHead `
        -Remote "https://github.com/example/wrong.git"
    $WrongRemoteOutput = New-EmptyDirectory -Path (Join-Path $TempRoot "wrong-remote-output")
    $WrongRemoteResult = Invoke-NativeCapture -Name "negative-wrong-remote" `
        -FilePath $PowerShellExecutable `
        -Arguments (New-RunnerArguments `
            -Runner $Runner `
            -Verified $WrongRemoteCheckout `
            -Protected $Protected `
            -Runtime $Runtime `
            -Output $WrongRemoteOutput)
    Assert-Failure -Result $WrongRemoteResult -Name "wrong origin"

    $OverlapOutput = New-EmptyDirectory -Path (Join-Path $TempRoot "overlap-output")
    $OverlapResult = Invoke-NativeCapture -Name "negative-checkout-overlap" `
        -FilePath $PowerShellExecutable `
        -Arguments (New-RunnerArguments `
            -Runner $Runner `
            -Verified $RepoRoot `
            -Protected $RepoRoot `
            -Runtime $Runtime `
            -Output $OverlapOutput)
    Assert-Failure -Result $OverlapResult -Name "verified/protected overlap"

    $PackageOverlapResult = Invoke-NativeCapture -Name "negative-package-output-overlap" `
        -FilePath $PowerShellExecutable `
        -Arguments (New-RunnerArguments `
            -Runner $Runner `
            -Verified $RepoRoot `
            -Protected $Protected `
            -Runtime $Runtime `
            -Output $Package)
    Assert-Failure -Result $PackageOverlapResult -Name "package/output overlap"

    $ShimCmd = Join-Path $TempRoot "python-shim.cmd"
    $ShimContent = "@echo off`r`n`"%MOOMOO_HANDOFF_REAL_PYTHON%`" `"$ShimPath`" %*`r`n"
    [IO.File]::WriteAllText($ShimCmd, $ShimContent, [Text.Encoding]::ASCII)
    foreach ($ShimMode in @("missing_shareable", "exit_mismatch")) {
        $ShimOutput = New-EmptyDirectory -Path (Join-Path $TempRoot ("shim-output-" + $ShimMode))
        $Environment = @{
            MOOMOO_HANDOFF_REAL_PYTHON = $PythonExecutable
            MOOMOO_HANDOFF_SHIM_MODE = $ShimMode
        }
        $ShimResult = Invoke-NativeCapture -Name ("negative-" + $ShimMode) `
            -FilePath $PowerShellExecutable `
            -Arguments (New-RunnerArguments `
                -Runner $Runner `
                -Verified $RepoRoot `
                -Protected $Protected `
                -Runtime $Runtime `
                -Output $ShimOutput `
                -Python $ShimCmd) `
            -Environment $Environment
        Assert-Failure -Result $ShimResult -Name $ShimMode
    }

    $Status = @(& git -C $RepoRoot status --porcelain)
    if ($LASTEXITCODE -ne 0) {
        throw "git status failed after handoff tests"
    }
    if (@($Status | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -ne 0) {
        throw "Handoff tests modified the tracked repository: $($Status -join '; ')"
    }

    Write-DiagnosticText -Name "environment.json" -Text (
        [pscustomobject]@{
            powershell_version = $PSVersionTable.PSVersion.ToString()
            powershell_edition = if ($PSVersionTable.PSObject.Properties.Name -contains "PSEdition") {
                [string]$PSVersionTable.PSEdition
            } else {
                "Desktop"
            }
            nested_powershell_executable = $PowerShellExecutable
            python_executable = $PythonExecutable
            tested_head = $ExpectedHead
            expected_remote = $ExpectedRemote
            negative_cases = @(
                "tampered_file",
                "tampered_operator_bundle",
                "authorization_boundaries",
                "wrong_head",
                "dirty_checkout",
                "wrong_remote",
                "checkout_overlap",
                "package_output_overlap",
                "missing_shareable_evidence",
                "exit_code_mismatch"
            )
        } | ConvertTo-Json -Depth 8
    )
} finally {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "moomoo read-only discovery handoff validation PASS"
