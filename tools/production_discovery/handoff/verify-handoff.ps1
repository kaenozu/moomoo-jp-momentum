[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = [IO.Path]::GetFullPath($PSScriptRoot)
$ChecksumPath = Join-Path $Root "HANDOFF_SHA256SUMS.txt"
$ManifestPath = Join-Path $Root "HANDOFF_MANIFEST.json"

function Assert-SafeRelativeFilename {
    param([Parameter(Mandatory = $true)][string]$Name)
    if ([IO.Path]::IsPathRooted($Name)) {
        throw "Checksum path must be relative: $Name"
    }
    if ($Name -match '(^|[\\/])\.\.([\\/]|$)') {
        throw "Checksum path contains parent traversal: $Name"
    }
    if ($Name -match '[\\/]') {
        throw "Handoff package members must be top-level files: $Name"
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

if (-not (Test-Path -LiteralPath $ChecksumPath -PathType Leaf)) {
    throw "Checksum file is missing: $ChecksumPath"
}
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "Manifest file is missing: $ManifestPath"
}

$ExpectedByName = @{}
foreach ($Line in Get-Content -LiteralPath $ChecksumPath -Encoding UTF8) {
    if ([string]::IsNullOrWhiteSpace($Line)) { continue }
    if ($Line -notmatch '^([0-9a-fA-F]{64})  (.+)$') {
        throw "Invalid checksum line: $Line"
    }
    $Digest = $Matches[1].ToLowerInvariant()
    $RelativePath = $Matches[2]
    Assert-SafeRelativeFilename -Name $RelativePath
    if ($ExpectedByName.ContainsKey($RelativePath)) {
        throw "Duplicate checksum entry: $RelativePath"
    }
    $ExpectedByName[$RelativePath] = $Digest
}

$ActualFiles = @(
    Get-ChildItem -LiteralPath $Root -File -ErrorAction Stop |
        Where-Object { $_.Name -ne "HANDOFF_SHA256SUMS.txt" } |
        Sort-Object Name
)
$ActualNames = @($ActualFiles | ForEach-Object { $_.Name })
$ExpectedNames = @($ExpectedByName.Keys | Sort-Object)
if (($ActualNames -join "`n") -ne ($ExpectedNames -join "`n")) {
    throw "Checksum coverage does not exactly match package files. Expected='$($ExpectedNames -join ',')' Actual='$($ActualNames -join ',')'"
}

$Failures = @()
foreach ($File in $ActualFiles) {
    $Expected = [string]$ExpectedByName[$File.Name]
    $Actual = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected) {
        $Failures += "MISMATCH $($File.Name) expected=$Expected actual=$Actual"
    }
}
if ($Failures.Count -ne 0) {
    $Failures | ForEach-Object { Write-Error $_ }
    throw "Handoff checksum verification failed"
}

$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$Manifest.report_type -ne "moomoo_readonly_discovery_handoff_manifest") {
    throw "Unexpected handoff manifest report_type: $($Manifest.report_type)"
}
if ([int]$Manifest.schema_version -ne 1) {
    throw "Unexpected handoff manifest schema_version: $($Manifest.schema_version)"
}
if ([string]$Manifest.handoff_version -ne "1.2.2") {
    throw "Unexpected handoff version: $($Manifest.handoff_version)"
}
if ([string]$Manifest.operator_version -ne "1.2.1") {
    throw "Unexpected operator version: $($Manifest.operator_version)"
}
$ExpectedHead = ([string]$Manifest.expected_checkout_head).ToLowerInvariant()
if ($ExpectedHead -notmatch '^[0-9a-f]{40}$') {
    throw "Manifest expected_checkout_head is not an exact Git SHA: $ExpectedHead"
}
if (([string]$Manifest.source_commit).ToLowerInvariant() -ne $ExpectedHead) {
    throw "Manifest source_commit does not match expected_checkout_head"
}
if ([string]::IsNullOrWhiteSpace([string]$Manifest.expected_remote)) {
    throw "Manifest expected_remote is missing"
}

if ([string]$Manifest.authorization.production_readiness -ne "BLOCKED" -or
    [bool]$Manifest.authorization.preflight_authorized -or
    [bool]$Manifest.authorization.production_drill_authorized -or
    [bool]$Manifest.authorization.cutover_authorized) {
    throw "Handoff manifest authorization boundary is invalid"
}
if (-not [bool]$Manifest.distribution_policy.production_use_requires_master_push_artifact -or
    -not [bool]$Manifest.distribution_policy.pull_request_artifact_is_test_only) {
    throw "Handoff distribution policy is not fail-closed"
}

$PayloadProperties = @($Manifest.payload_files.PSObject.Properties)
if ($PayloadProperties.Count -eq 0) {
    throw "Manifest payload_files is empty"
}
foreach ($Property in $PayloadProperties) {
    $Name = [string]$Property.Name
    Assert-SafeRelativeFilename -Name $Name
    $Path = Join-Path $Root $Name
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Manifest payload file is missing: $Name"
    }
    $Expected = ([string]$Property.Value).ToLowerInvariant()
    $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Expected -notmatch '^[0-9a-f]{64}$' -or $Actual -ne $Expected) {
        throw "Manifest payload SHA-256 mismatch: $Name"
    }
}

$BundleName = [string]$Manifest.operator_bundle.name
Assert-SafeRelativeFilename -Name $BundleName
if ($BundleName -ne "moomoo_production_discovery_operator_v4_v1.2.1.zip") {
    throw "Unexpected operator bundle name: $BundleName"
}
$BundlePath = Join-Path $Root $BundleName
if (-not (Test-Path -LiteralPath $BundlePath -PathType Leaf)) {
    throw "Operator bundle is missing: $BundlePath"
}
$ActualBundleSha256 = (Get-FileHash -LiteralPath $BundlePath -Algorithm SHA256).Hash.ToLowerInvariant()
$ExpectedBundleSha256 = ([string]$Manifest.operator_bundle.sha256).ToLowerInvariant()
if ($ExpectedBundleSha256 -notmatch '^[0-9a-f]{64}$' -or
    $ActualBundleSha256 -ne $ExpectedBundleSha256) {
    throw "Operator bundle SHA-256 mismatch"
}
if (([string]$Manifest.operator_bundle.source_commit).ToLowerInvariant() -ne $ExpectedHead) {
    throw "Operator bundle source_commit does not match expected_checkout_head"
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$Archive = [IO.Compression.ZipFile]::OpenRead($BundlePath)
$ExtractionRoot = Join-Path ([IO.Path]::GetTempPath()) (
    "moomoo-handoff-verify-" + [Guid]::NewGuid().ToString("N")
)
try {
    $EntryNames = @($Archive.Entries | Where-Object { $_.Name } | ForEach-Object { $_.FullName })
    $DuplicateNames = @(
        $EntryNames | Group-Object | Where-Object { $_.Count -gt 1 } | ForEach-Object { $_.Name }
    )
    if ($DuplicateNames.Count -ne 0) {
        throw "Operator bundle contains duplicate ZIP entries: $($DuplicateNames -join ', ')"
    }
    [IO.Directory]::CreateDirectory($ExtractionRoot) | Out-Null
    $Archive.Dispose()
    $Archive = $null
    Expand-Archive -LiteralPath $BundlePath -DestinationPath $ExtractionRoot -Force

    $OperatorManifestPath = Join-Path $ExtractionRoot "bundle-manifest.json"
    $OperatorSumsPath = Join-Path $ExtractionRoot "SHA256SUMS.txt"
    if (-not (Test-Path -LiteralPath $OperatorManifestPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $OperatorSumsPath -PathType Leaf)) {
        throw "Operator bundle manifest or checksums are missing"
    }
    $OperatorManifest = Get-Content -LiteralPath $OperatorManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$OperatorManifest.operator_version -ne "1.2.1") {
        throw "Operator bundle manifest version mismatch"
    }
    if (([string]$OperatorManifest.source_commit).ToLowerInvariant() -ne $ExpectedHead) {
        throw "Operator bundle manifest source_commit mismatch"
    }
    if ([string]$OperatorManifest.authorization.production_readiness -ne "BLOCKED" -or
        [bool]$OperatorManifest.authorization.preflight_authorized -or
        [bool]$OperatorManifest.authorization.production_drill_authorized -or
        [bool]$OperatorManifest.authorization.cutover_authorized) {
        throw "Operator bundle authorization boundary is invalid"
    }

    foreach ($Line in Get-Content -LiteralPath $OperatorSumsPath -Encoding UTF8) {
        if ([string]::IsNullOrWhiteSpace($Line)) { continue }
        if ($Line -notmatch '^([0-9a-fA-F]{64})  (.+)$') {
            throw "Invalid operator checksum line: $Line"
        }
        $Name = $Matches[2]
        Assert-SafeRelativeFilename -Name $Name
        $Path = Join-Path $ExtractionRoot $Name
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            throw "Operator checksum lists a missing file: $Name"
        }
        $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Actual -ne $Matches[1].ToLowerInvariant()) {
            throw "Operator internal SHA-256 mismatch: $Name"
        }
    }
} finally {
    if ($null -ne $Archive) {
        $Archive.Dispose()
    }
    if (Test-Path -LiteralPath $ExtractionRoot) {
        Remove-Item -LiteralPath $ExtractionRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Handoff verification PASS"
Write-Host "Handoff version: $($Manifest.handoff_version)"
Write-Host "Expected checkout HEAD: $ExpectedHead"
$NormalizedExpectedRemote = Get-NormalizedRemote -Value ([string]$Manifest.expected_remote)
Write-Host "Expected remote: $NormalizedExpectedRemote"
Write-Host "Operator bundle SHA-256: $ActualBundleSha256"
