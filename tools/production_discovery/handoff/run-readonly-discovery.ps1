[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$VerifiedCheckout,

    [Parameter(Mandatory = $true)]
    [string]$ProtectedCheckout,

    [Parameter(Mandatory = $true)]
    [string[]]$ConfigSearchRoot,

    [Parameter(Mandatory = $true)]
    [string[]]$ProductionWorkingDirectory,

    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "manual-command",
        "startup-script",
        "service-runbook",
        "scheduled-task-review",
        "other-direct-evidence"
    )]
    [string]$ProductionWorkingDirectorySource,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ProductionWorkingDirectoryEvidence,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [string]$PythonExecutable = "python",

    [ValidateSet("powershell.exe", "pwsh.exe")]
    [string]$PowerShellExecutable = "powershell.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:PYTHONDONTWRITEBYTECODE = "1"

$PackageRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$ManifestPath = Join-Path $PackageRoot "HANDOFF_MANIFEST.json"
$VerifyScript = Join-Path $PackageRoot "verify-handoff.ps1"

function Assert-AbsolutePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if (-not [IO.Path]::IsPathRooted($Path)) {
        throw "$Name must be an absolute path: $Path"
    }
}

function Resolve-ExistingDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )
    Assert-AbsolutePath -Path $Path -Name $Name
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Name does not exist or is not a directory: $Path"
    }
    return [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Path).Path)
}

function Normalize-ComparablePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Full = [IO.Path]::GetFullPath($Path)
    $Root = [IO.Path]::GetPathRoot($Full)
    if ($Full.Equals($Root, [StringComparison]::OrdinalIgnoreCase)) {
        return $Full
    }
    return $Full.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
}

function Test-SameOrChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Parent
    )
    $CandidateNormalized = Normalize-ComparablePath $Candidate
    $ParentNormalized = Normalize-ComparablePath $Parent
    if ($CandidateNormalized.Equals(
        $ParentNormalized,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        return $true
    }
    $Prefix = $ParentNormalized + [IO.Path]::DirectorySeparatorChar
    return $CandidateNormalized.StartsWith(
        $Prefix,
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Assert-NoPathOverlap {
    param(
        [Parameter(Mandatory = $true)][string]$Left,
        [Parameter(Mandatory = $true)][string]$LeftName,
        [Parameter(Mandatory = $true)][string]$Right,
        [Parameter(Mandatory = $true)][string]$RightName
    )
    if ((Test-SameOrChildPath -Candidate $Left -Parent $Right) -or
        (Test-SameOrChildPath -Candidate $Right -Parent $Left)) {
        throw "$LeftName and $RightName must not overlap: '$Left' / '$Right'"
    }
}

function Get-NormalizedRemote {
    param([Parameter(Mandatory = $true)][string]$Value)
    $Result = $Value.Trim().TrimEnd('/').ToLowerInvariant()
    if ($Result.EndsWith('.git')) {
        $Result = $Result.Substring(0, $Result.Length - 4)
    }
    return $Result
}

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )
    $PreviousPreference = $ErrorActionPreference
    $Raw = @()
    $ExitCode = $null
    try {
        $ErrorActionPreference = "Continue"
        $Raw = @(& $FilePath @Arguments 2>&1)
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    $Text = @(
        $Raw | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.ToString()
            } else {
                [string]$_
            }
        }
    ) -join [Environment]::NewLine
    if ($ExitCode -ne 0) {
        throw "$Description failed with exit code $ExitCode. Output: $Text"
    }
    return @($Raw | ForEach-Object { $_.ToString() })
}

if ($env:OS -ne "Windows_NT") {
    throw "This handoff runner must be executed on Windows."
}
if (-not (Test-Path -LiteralPath $VerifyScript -PathType Leaf)) {
    throw "Handoff verifier is missing: $VerifyScript"
}
& $VerifyScript
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "Handoff manifest is missing: $ManifestPath"
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$ExpectedHead = ([string]$Manifest.expected_checkout_head).ToLowerInvariant()
$ExpectedRemote = [string]$Manifest.expected_remote
$BundleName = [string]$Manifest.operator_bundle.name
$ExpectedBundleSha256 = ([string]$Manifest.operator_bundle.sha256).ToLowerInvariant()
$BundleZip = Join-Path $PackageRoot $BundleName

if ($ExpectedHead -notmatch '^[0-9a-f]{40}$') {
    throw "Manifest expected_checkout_head is invalid: $ExpectedHead"
}
if ($ExpectedBundleSha256 -notmatch '^[0-9a-f]{64}$') {
    throw "Manifest operator bundle SHA-256 is invalid"
}
if ([string]$Manifest.authorization.production_readiness -ne "BLOCKED" -or
    [bool]$Manifest.authorization.preflight_authorized -or
    [bool]$Manifest.authorization.production_drill_authorized -or
    [bool]$Manifest.authorization.cutover_authorized) {
    throw "Manifest authorization boundary is invalid"
}
if (-not (Test-Path -LiteralPath $BundleZip -PathType Leaf)) {
    throw "Verified operator bundle is missing: $BundleZip"
}
$ActualBundleSha256 = (Get-FileHash -LiteralPath $BundleZip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualBundleSha256 -ne $ExpectedBundleSha256) {
    throw "Operator bundle SHA-256 mismatch. Expected $ExpectedBundleSha256, got $ActualBundleSha256"
}

$VerifiedCheckout = Resolve-ExistingDirectory -Path $VerifiedCheckout -Name "VerifiedCheckout"
$ProtectedCheckout = Resolve-ExistingDirectory -Path $ProtectedCheckout -Name "ProtectedCheckout"
$OutputRoot = Resolve-ExistingDirectory -Path $OutputRoot -Name "OutputRoot"
$ConfigSearchRoot = @($ConfigSearchRoot | ForEach-Object {
    Resolve-ExistingDirectory -Path $_ -Name "ConfigSearchRoot"
})
$ProductionWorkingDirectory = @($ProductionWorkingDirectory | ForEach-Object {
    Resolve-ExistingDirectory -Path $_ -Name "ProductionWorkingDirectory"
})

$ProtectedPairs = @(
    @($PackageRoot, "PackageRoot", $VerifiedCheckout, "VerifiedCheckout"),
    @($PackageRoot, "PackageRoot", $ProtectedCheckout, "ProtectedCheckout"),
    @($PackageRoot, "PackageRoot", $OutputRoot, "OutputRoot"),
    @($VerifiedCheckout, "VerifiedCheckout", $ProtectedCheckout, "ProtectedCheckout"),
    @($VerifiedCheckout, "VerifiedCheckout", $OutputRoot, "OutputRoot"),
    @($ProtectedCheckout, "ProtectedCheckout", $OutputRoot, "OutputRoot")
)
foreach ($Pair in $ProtectedPairs) {
    Assert-NoPathOverlap `
        -Left $Pair[0] `
        -LeftName $Pair[1] `
        -Right $Pair[2] `
        -RightName $Pair[3]
}

$OutputItems = @(Get-ChildItem -LiteralPath $OutputRoot -Force -ErrorAction Stop)
if ($OutputItems.Count -ne 0) {
    throw "OutputRoot must exist and be empty before discovery: $OutputRoot"
}

$PythonVersion = Invoke-NativeChecked -FilePath $PythonExecutable -Arguments @(
    "-c",
    "import sys; assert sys.version_info >= (3, 11); print(sys.version.split()[0])"
) -Description "Python 3.11+ check"
Invoke-NativeChecked -FilePath $PythonExecutable -Arguments @(
    "-c",
    "import yaml; print(yaml.__version__)"
) -Description "PyYAML import check" | Out-Null

$ActualTop = (@(Invoke-NativeChecked -FilePath "git" -Arguments @(
    "-C", $VerifiedCheckout, "rev-parse", "--show-toplevel"
) -Description "Verified checkout top-level check"))[0].Trim()
$ActualTop = [IO.Path]::GetFullPath($ActualTop)
if (-not $ActualTop.Equals(
    $VerifiedCheckout,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "VerifiedCheckout must be the exact Git top-level. Expected $VerifiedCheckout, got $ActualTop"
}

$ActualHead = (@(Invoke-NativeChecked -FilePath "git" -Arguments @(
    "-C", $VerifiedCheckout, "rev-parse", "HEAD"
) -Description "Verified checkout HEAD check"))[0].Trim().ToLowerInvariant()
if ($ActualHead -ne $ExpectedHead) {
    throw "Verified checkout HEAD mismatch. Expected $ExpectedHead, got $ActualHead"
}

$ActualRemote = (@(Invoke-NativeChecked -FilePath "git" -Arguments @(
    "-C", $VerifiedCheckout, "remote", "get-url", "origin"
) -Description "Verified checkout origin check"))[0].Trim()
if ((Get-NormalizedRemote $ActualRemote) -ne (Get-NormalizedRemote $ExpectedRemote)) {
    throw "Verified checkout origin mismatch. Expected $ExpectedRemote, got $ActualRemote"
}

$GitStatus = @(Invoke-NativeChecked -FilePath "git" -Arguments @(
    "-C", $VerifiedCheckout, "status", "--porcelain"
) -Description "Verified checkout cleanliness check")
$GitStatus = @($GitStatus | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
if ($GitStatus.Count -ne 0) {
    throw "Verified checkout is not clean: $($GitStatus -join '; ')"
}

$ExtractionRoot = Join-Path ([IO.Path]::GetTempPath()) (
    "moomoo-readonly-discovery-" + [Guid]::NewGuid().ToString("N")
)
[IO.Directory]::CreateDirectory($ExtractionRoot) | Out-Null

try {
    Expand-Archive -LiteralPath $BundleZip -DestinationPath $ExtractionRoot -Force
    $OperatorScript = Join-Path $ExtractionRoot "moomoo_discovery_operator.py"
    $ValidatorScript = Join-Path $ExtractionRoot "validate_moomoo_discovery_operator.py"
    if (-not (Test-Path -LiteralPath $OperatorScript -PathType Leaf) -or
        -not (Test-Path -LiteralPath $ValidatorScript -PathType Leaf)) {
        throw "Operator or validator script is missing after extraction"
    }

    Invoke-NativeChecked -FilePath $PythonExecutable -Arguments @($ValidatorScript) `
        -Description "Extracted bundle static validation" | Out-Null

    $Arguments = @(
        $OperatorScript,
        "run",
        "--bundle-dir", $ExtractionRoot,
        "--output-root", $OutputRoot,
        "--repo-path", $VerifiedCheckout,
        "--protected-checkout-path", $ProtectedCheckout,
        "--expected-head", $ExpectedHead,
        "--expected-remote", $ActualRemote,
        "--production-working-directory-source", $ProductionWorkingDirectorySource,
        "--production-working-directory-evidence", $ProductionWorkingDirectoryEvidence,
        "--powershell", $PowerShellExecutable
    )
    foreach ($Root in $ConfigSearchRoot) {
        $Arguments += @("--config-search-root", $Root)
    }
    foreach ($RuntimeDirectory in $ProductionWorkingDirectory) {
        $Arguments += @("--production-working-directory", $RuntimeDirectory)
    }

    Write-Host "Verified handoff version: $($Manifest.handoff_version)"
    Write-Host "Verified operator bundle SHA-256: $ActualBundleSha256"
    Write-Host "Verified checkout HEAD: $ActualHead"
    Write-Host "Python: $($PythonVersion -join '')"
    Write-Host "Starting read-only discovery. This does not authorize preflight or a recovery drill."

    $PreviousPreference = $ErrorActionPreference
    $OperatorRaw = @()
    $OperatorExitCode = $null
    try {
        $ErrorActionPreference = "Continue"
        $OperatorRaw = @(& $PythonExecutable @Arguments 2>&1)
        $OperatorExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    $OperatorRaw | ForEach-Object { Write-Host $_.ToString() }

    $EvidenceDirectories = @(
        Get-ChildItem -LiteralPath $OutputRoot -Directory -ErrorAction Stop |
            Sort-Object CreationTimeUtc
    )
    if ($EvidenceDirectories.Count -ne 1) {
        throw "Expected exactly one evidence directory, found $($EvidenceDirectories.Count). Operator exit code=$OperatorExitCode"
    }

    $EvidenceDirectory = $EvidenceDirectories[0].FullName
    $ResultPath = Join-Path $EvidenceDirectory "05-operator-result.json"
    $ReviewPath = Join-Path $EvidenceDirectory "02-discovery-review.json"
    $RedactedPath = Join-Path $EvidenceDirectory "03-discovery-redacted.json"
    $SummaryPath = Join-Path $EvidenceDirectory "04-discovery-summary.md"
    foreach ($RequiredPath in @($ResultPath, $RedactedPath, $SummaryPath)) {
        if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
            throw "Required shareable evidence file is missing: $RequiredPath"
        }
    }

    $Result = Get-Content -LiteralPath $ResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($OperatorExitCode -ne 0 -and (Test-Path -LiteralPath $ReviewPath -PathType Leaf)) {
        $Review = Get-Content -LiteralPath $ReviewPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $BlockingFindings = @(
            $Review.findings | Where-Object {
                [string]$_.status -in @("FAIL", "CONFLICT")
            }
        )
        if ($BlockingFindings.Count -ne 0) {
            Write-Host "Blocking discovery findings:"
            foreach ($Finding in $BlockingFindings) {
                Write-Host "  $($Finding.status) / $($Finding.severity) / $($Finding.code): $($Finding.message)"
            }
        }
    }
    if ([string]$Result.production_readiness -ne "BLOCKED" -or
        [bool]$Result.preflight_authorized -or
        [bool]$Result.production_drill_authorized -or
        [bool]$Result.cutover_authorized) {
        throw "Safety authorization boundary is invalid in 05-operator-result.json"
    }
    if ([int]$Result.operator_exit_code -ne [int]$OperatorExitCode) {
        throw "Operator exit-code evidence mismatch. Process=$OperatorExitCode evidence=$($Result.operator_exit_code)"
    }

    Write-Host ""
    Write-Host "READ-ONLY DISCOVERY FINISHED"
    Write-Host "Operator exit code: $OperatorExitCode"
    Write-Host "Status: $($Result.status)"
    Write-Host "Machine validation: $($Result.machine_validation_status)"
    Write-Host "Human validation: $($Result.human_validation_status)"
    Write-Host "Operational validation: $($Result.operational_validation_status)"
    Write-Host "Production readiness: $($Result.production_readiness)"
    Write-Host "Evidence directory: $EvidenceDirectory"
    Write-Host "Share only after visual review:"
    Write-Host "  $RedactedPath"
    Write-Host "  $SummaryPath"
    Write-Host "  $ResultPath"
    Write-Host "Do not run -PreflightOnly or the full recovery drill from this script."

    exit ([int]$OperatorExitCode)
} finally {
    if (Test-Path -LiteralPath $ExtractionRoot) {
        Remove-Item -LiteralPath $ExtractionRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
