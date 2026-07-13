function Get-DatabaseFileCandidates {
    $roots = @($ConfigSearchRoots + @($RepoPath, $ProtectedCheckoutPath))
    $map = @{}
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) {
            continue
        }
        Get-ChildItem -LiteralPath $root -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '(?i)(\.db|\.sqlite|\.sqlite3)(-wal|-shm)?$' } |
            ForEach-Object {
                $map[$_.FullName.ToLowerInvariant()] = [pscustomobject]@{
                    path = $_.FullName
                    length_bytes = $_.Length
                    last_write_time = $_.LastWriteTime.ToString("o")
                    extension = $_.Extension
                }
            }
    }
    @($map.Values | Sort-Object path)
}

function Get-StorageSnapshot {
    [pscustomobject]@{
        disks = @(Get-Disk -ErrorAction Stop | Select-Object Number, FriendlyName, SerialNumber, BusType, OperationalStatus, PartitionStyle, Size)
        partitions = @(Get-Partition -ErrorAction Stop | Select-Object DiskNumber, PartitionNumber, DriveLetter, Type, Size)
        volumes = @(Get-Volume -ErrorAction Stop | Select-Object DriveLetter, FileSystemLabel, FileSystem, HealthStatus, Size, SizeRemaining, Path)
        smb_mappings = Invoke-Safe { @(Get-SmbMapping -ErrorAction Stop | Select-Object LocalPath, RemotePath, Status) }
        smb_shares = Invoke-Safe { @(Get-SmbShare -ErrorAction Stop | Select-Object Name, Path, Description, CurrentUsers, Special) }
        smb_open_files = Invoke-Safe {
            @(Get-SmbOpenFile -ErrorAction Stop |
                Where-Object { "$($_.Path)" -match '(?i)moomoo|\.db$|\.sqlite|\.sqlite3' } |
                Select-Object FileId, SessionId, ClientComputerName, ClientUserName, Path, Permissions)
        }
    }
}
