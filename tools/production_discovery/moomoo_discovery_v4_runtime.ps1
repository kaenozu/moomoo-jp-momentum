function Get-RelevantScheduledTasks {
    $rows = @()
    foreach ($task in (Get-ScheduledTask -ErrorAction Stop)) {
        foreach ($action in @($task.Actions)) {
            $workingDirectory = $null
            if ($action.PSObject.Properties.Name -contains "WorkingDirectory") {
                $workingDirectory = [string]$action.WorkingDirectory
            }
            $text = "$($task.TaskPath) $($task.TaskName) $($action.Execute) $($action.Arguments) $workingDirectory"
            if ($text -notmatch $writerPattern) {
                continue
            }
            $principal = $task.Principal
            $rows += [pscustomobject]@{
                task_path = $task.TaskPath
                task_name = $task.TaskName
                state = [string]$task.State
                execute = [string]$action.Execute
                arguments = [string]$action.Arguments
                working_directory = $workingDirectory
                principal_user_id = if ($principal) { [string]$principal.UserId } else { $null }
                principal_logon_type = if ($principal) { [string]$principal.LogonType } else { $null }
                triggers = @($task.Triggers | ForEach-Object { [string]$_ })
            }
        }
    }
    @($rows)
}

function Get-WriterProcesses {
    @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object {
                "$($_.Name) $($_.ExecutablePath) $($_.CommandLine)" -match $writerPattern
            } |
            Select-Object ProcessId, ParentProcessId, SessionId, Name,
                ExecutablePath, CommandLine, CreationDate
    )
}

function Get-RelevantServices {
    @(
        Get-CimInstance Win32_Service -ErrorAction Stop |
            Where-Object {
                "$($_.Name) $($_.DisplayName) $($_.PathName)" -match $writerPattern
            } |
            Select-Object Name, DisplayName, State, StartMode, StartName,
                PathName, ProcessId
    )
}

function Get-RelevantStartupCommands {
    @(
        Get-CimInstance Win32_StartupCommand -ErrorAction Stop |
            Where-Object {
                "$($_.Name) $($_.Command) $($_.Location)" -match $writerPattern
            } |
            Select-Object Name, Command, Location, User
    )
}

function Get-RelevantStartupFiles {
    $roots = @(
        "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\Startup",
        "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
    )
    $items = @()
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) {
            continue
        }
        $items += Get-ChildItem -LiteralPath $root -File -ErrorAction SilentlyContinue |
            Where-Object { "$($_.Name) $($_.FullName)" -match $writerPattern } |
            Select-Object Name, FullName, Length, LastWriteTime
    }
    @($items)
}

function Get-ExternalRuntimeEvidence {
    $wsl = $null
    if (Get-Command "wsl.exe" -ErrorAction SilentlyContinue) {
        $wsl = Invoke-NativeRead -FilePath "wsl.exe" -Arguments @("--list", "--verbose")
    }
    $docker = $null
    if (Get-Command "docker.exe" -ErrorAction SilentlyContinue) {
        $docker = Invoke-NativeRead -FilePath "docker.exe" -Arguments @(
            "ps", "--no-trunc", "--format",
            "{{json .}}"
        )
    }
    $sessions = $null
    if (Get-Command "quser.exe" -ErrorAction SilentlyContinue) {
        $sessions = Invoke-NativeRead -FilePath "quser.exe" -Arguments @()
    }
    [pscustomobject]@{
        wsl = $wsl
        docker = $docker
        user_sessions = $sessions
        remote_host_limitation = "This host cannot prove that no other PC accesses the SQLite database. Operator confirmation remains mandatory."
    }
}

function Get-ConfigCandidates {
    $candidateMap = @{}
    foreach ($candidate in @(
        (Join-Path $RepoPath "config.yaml"),
        (Join-Path $ProtectedCheckoutPath "config.yaml")
    )) {
        try {
            $full = [IO.Path]::GetFullPath($candidate)
            $candidateMap[$full.ToLowerInvariant()] = $full
        } catch {}
    }
    foreach ($root in $ConfigSearchRoots) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) {
            continue
        }
        Get-ChildItem -LiteralPath $root -Filter "config.yaml" -File -Recurse -ErrorAction SilentlyContinue |
            ForEach-Object {
                $candidateMap[$_.FullName.ToLowerInvariant()] = $_.FullName
            }
    }

    @(
        foreach ($path in ($candidateMap.Values | Sort-Object)) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                continue
            }
            $item = Get-Item -LiteralPath $path
            [pscustomobject]@{
                path = $item.FullName
                directory = $item.DirectoryName
                length_bytes = $item.Length
                last_write_time = $item.LastWriteTime.ToString("o")
                sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
                selected_values = Read-SelectedYamlValues $path
            }
        }
    )
}

function Add-RuntimeCandidate {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Map,
        [AllowNull()][string]$Path,
        [Parameter(Mandatory = $true)][string]$SourceType,
        [AllowNull()][string]$SourceId,
        [Parameter(Mandatory = $true)][bool]$Authoritative
    )
    if (-not $Path -or -not $Path.Trim()) {
        return
    }
    try {
        $full = [IO.Path]::GetFullPath($Path)
    } catch {
        return
    }
    $key = $full.TrimEnd('\').ToLowerInvariant()
    if (-not $Map.ContainsKey($key)) {
        $Map[$key] = [pscustomobject]@{
            path = $full
            exists = Test-Path -LiteralPath $full -PathType Container
            evidence = @()
            authoritative = $false
        }
    }
    $row = $Map[$key]
    $row.evidence += [pscustomobject]@{
        source_type = $SourceType
        source_id = $SourceId
        authoritative = $Authoritative
    }
    if ($Authoritative) {
        $row.authoritative = $true
    }
}

function Get-RuntimeWorkingDirectoryCandidates {
    param(
        [Parameter(Mandatory = $true)]$ScheduledTasks,
        [Parameter(Mandatory = $true)]$ConfigCandidates
    )
    $map = @{}
    foreach ($path in $ProductionWorkingDirectoryCandidates) {
        Add-RuntimeCandidate -Map $map -Path $path -SourceType "operator_explicit" -SourceId $path -Authoritative $true
    }
    foreach ($task in @($ScheduledTasks)) {
        if ($task -and -not ($task.PSObject.Properties.Name -contains "error_type")) {
            Add-RuntimeCandidate -Map $map -Path $task.working_directory `
                -SourceType "scheduled_task_working_directory" `
                -SourceId "$($task.task_path)$($task.task_name)" `
                -Authoritative $true
        }
    }
    Add-RuntimeCandidate -Map $map -Path (Get-Location).Path -SourceType "discovery_invocation_directory" -SourceId $null -Authoritative $false
    Add-RuntimeCandidate -Map $map -Path $RepoPath -SourceType "verified_checkout" -SourceId $RepoPath -Authoritative $false
    Add-RuntimeCandidate -Map $map -Path $ProtectedCheckoutPath -SourceType "protected_checkout" -SourceId $ProtectedCheckoutPath -Authoritative $false
    foreach ($config in @($ConfigCandidates)) {
        if ($config -and -not ($config.PSObject.Properties.Name -contains "error_type")) {
            Add-RuntimeCandidate -Map $map -Path $config.directory -SourceType "config_directory" -SourceId $config.path -Authoritative $false
        }
    }
    @($map.Values | Sort-Object path)
}

function Resolve-ConfigRuntimePaths {
    param(
        [Parameter(Mandatory = $true)]$ConfigCandidates,
        [Parameter(Mandatory = $true)]$RuntimeCandidates
    )
    $rows = @()
    foreach ($config in @($ConfigCandidates)) {
        if (-not $config -or $config.PSObject.Properties.Name -contains "error_type") {
            continue
        }
        $selected = $config.selected_values
        if (-not $selected -or $selected.PSObject.Properties.Name -contains "parse_error") {
            continue
        }
        $rawDb = $selected.database_path_raw
        $rawBackup = $selected.database_backup_directory_raw
        if (-not ($rawDb -is [string]) -or -not $rawDb.Trim()) {
            continue
        }
        foreach ($runtime in @($RuntimeCandidates)) {
            $dbPath = $null
            $backupPath = $null
            try {
                $dbPath = Convert-ToFullPath -Path $rawDb -BasePath $runtime.path
                if ($rawBackup -is [string] -and $rawBackup.Trim()) {
                    $backupPath = Convert-ToFullPath -Path $rawBackup -BasePath $runtime.path
                }
            } catch {
                $rows += [pscustomobject]@{
                    config_path = $config.path
                    production_working_directory = $runtime.path
                    runtime_authoritative = $runtime.authoritative
                    runtime_evidence = $runtime.evidence
                    configured_database_path = $rawDb
                    configured_backup_directory = $rawBackup
                    resolution_error = $_.Exception.Message
                }
                continue
            }
            $dbExists = Test-Path -LiteralPath $dbPath -PathType Leaf
            $dbItem = if ($dbExists) { Get-Item -LiteralPath $dbPath } else { $null }
            $rows += [pscustomobject]@{
                config_path = $config.path
                config_sha256 = $config.sha256
                production_working_directory = $runtime.path
                working_directory_exists = $runtime.exists
                runtime_authoritative = $runtime.authoritative
                runtime_evidence = $runtime.evidence
                configured_database_path = $rawDb
                resolved_database_path = $dbPath
                database_exists = $dbExists
                database_length_bytes = if ($dbItem) { $dbItem.Length } else { $null }
                database_last_write_time = if ($dbItem) { $dbItem.LastWriteTime.ToString("o") } else { $null }
                wal_exists = Test-Path -LiteralPath ($dbPath + "-wal") -PathType Leaf
                shm_exists = Test-Path -LiteralPath ($dbPath + "-shm") -PathType Leaf
                configured_backup_directory = $rawBackup
                resolved_backup_directory = $backupPath
                backup_directory_exists = if ($backupPath) {
                    Test-Path -LiteralPath $backupPath -PathType Container
                } else {
                    $null
                }
                resolution_error = $null
            }
        }
    }
    @($rows)
}
