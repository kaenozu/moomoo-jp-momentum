[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepoPath,
    [Parameter(Mandatory = $true)][string]$ProtectedCheckoutPath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$ExpectedHead,
    [Parameter(Mandatory = $true)][string]$ExpectedRemote,
    [Parameter(Mandatory = $true)][string[]]$ConfigSearchRoots,
    [string[]]$ProductionWorkingDirectoryCandidates = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:DiscoveryScriptVersion = "4.0.0"
$script:DiscoveryScriptPath = $PSCommandPath

# Read-only contract:
# - no SQLite connection
# - no repository Python-module import
# - no process/task/service mutation
# - no Git mutation
# - no file creation by this script
# - JSON is emitted to stdout only

$writerPattern = '(?i)(scheduler\.py|run_daily_cycle\.py|streamlit(?:\.exe)?\s+run\s+.*app\.py|app\.py|daily_update\.py|screen_candidates\.py|virtual_order\.py|process_virtual_fills\.py|record_trade\.py|paper_trade_daily\.py|database_backup\.py|yf_daily_update\.py|yf_supplement\.py|recalc_indicators\.py|moomoo-jp-momentum|\\moomoo(?:-preflight)?\\)'


$script:DiscoveryBundleFiles = @(
    (Join-Path $PSScriptRoot "moomoo_discovery_v4_common.ps1"),
    (Join-Path $PSScriptRoot "moomoo_discovery_v4_runtime.ps1"),
    (Join-Path $PSScriptRoot "moomoo_discovery_v4_storage.ps1"),
    $PSCommandPath
)

foreach ($ModulePath in $script:DiscoveryBundleFiles) {
    if ($ModulePath -eq $PSCommandPath) { continue }
    if (-not (Test-Path -LiteralPath $ModulePath -PathType Leaf)) {
        throw "Discovery module is missing: $ModulePath"
    }
    . $ModulePath
}

$scriptIdentity = Invoke-Safe { Get-ScriptIdentity }
$commandAvailability = @(Invoke-Safe { Get-CommandAvailability })
$repoSnapshot = Invoke-Safe { Get-RepositorySnapshot $RepoPath }
$protectedSnapshot = Invoke-Safe { Get-RepositorySnapshot $ProtectedCheckoutPath }
$configCandidates = @(Invoke-Safe { Get-ConfigCandidates })
$writerProcesses = @(Invoke-Safe { Get-WriterProcesses })
$scheduledTasks = @(Invoke-Safe { Get-RelevantScheduledTasks })
$services = @(Invoke-Safe { Get-RelevantServices })
$startupCommands = @(Invoke-Safe { Get-RelevantStartupCommands })
$startupFiles = @(Invoke-Safe { Get-RelevantStartupFiles })
$externalRuntime = Invoke-Safe { Get-ExternalRuntimeEvidence }
$runtimeCandidates = @(Invoke-Safe { Get-RuntimeWorkingDirectoryCandidates -ScheduledTasks $scheduledTasks -ConfigCandidates $configCandidates })
$runtimePathEvidence = @(Invoke-Safe { Resolve-ConfigRuntimePaths -ConfigCandidates $configCandidates -RuntimeCandidates $runtimeCandidates })
$dbCandidates = @(Invoke-Safe { Get-DatabaseFileCandidates })
$storage = Invoke-Safe { Get-StorageSnapshot }

$result = [pscustomobject]@{
    report_type = "moomoo_production_readonly_discovery"
    schema_version = 4
    script_identity = $scriptIdentity
    invocation_parameters = [pscustomobject]@{
        repo_path = $RepoPath
        protected_checkout_path = $ProtectedCheckoutPath
        expected_head = $ExpectedHead
        expected_remote = $ExpectedRemote
        config_search_roots = @($ConfigSearchRoots)
        production_working_directory_candidates = @($ProductionWorkingDirectoryCandidates)
    }
    safety = [pscustomobject]@{
        read_only_intent = $true
        sqlite_connection_performed = $false
        repository_module_import_performed = $false
        opend_connection_performed = $false
        process_or_task_state_changed = $false
        git_mutation_performed = $false
        output_file_created_by_script = $false
        preflight_executed = $false
        production_drill_executed = $false
        cutover_executed = $false
    }
    captured_at = (Get-Date).ToString("o")
    machine = [pscustomobject]@{
        computer_name = $env:COMPUTERNAME
        user_domain = $env:USERDOMAIN
        user_name = $env:USERNAME
        current_directory = (Get-Location).Path
        timezone = Invoke-Safe { (Get-TimeZone).Id }
        powershell = [pscustomobject]@{
            version = $PSVersionTable.PSVersion.ToString()
            edition = if ($PSVersionTable.PSObject.Properties.Name -contains "PSEdition") { [string]$PSVersionTable.PSEdition } else { "Desktop" }
            is_64_bit_process = [Environment]::Is64BitProcess
        }
        operating_system = Invoke-Safe {
            Get-CimInstance Win32_OperatingSystem |
                Select-Object Caption, Version, BuildNumber, OSArchitecture, LastBootUpTime
        }
    }
    command_availability = $commandAvailability
    repositories = [pscustomobject]@{
        preflight_candidate = $repoSnapshot
        protected_dirty_checkout = $protectedSnapshot
    }
    config_candidates = $configCandidates
    runtime_writer_candidates = [pscustomobject]@{
        processes = $writerProcesses
        scheduled_tasks = $scheduledTasks
        services = $services
        startup_commands = $startupCommands
        startup_files = $startupFiles
        external_runtime = $externalRuntime
        limitation = "Empty local evidence does not prove no writer exists. Manual commands, another user session, remote hosts, WSL, containers, and unlisted launch mechanisms require confirmation."
    }
    runtime_working_directory_candidates = $runtimeCandidates
    runtime_path_evidence = $runtimePathEvidence
    database_file_candidates = $dbCandidates
    storage = $storage
    authorization = [pscustomobject]@{
        production_readiness = "BLOCKED"
        preflight_authorized = $false
        production_drill_authorized = $false
        cutover_authorized = $false
    }
}

$result | ConvertTo-Json -Depth 14
