from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REF = "origin/agent/master-bound-readonly-handoff"
COPY_PATHS = [
    ".github/workflows/moomoo-handoff-windows-validation.yml",
    "scripts/build_moomoo_readonly_discovery_handoff.py",
    "scripts/compare_moomoo_readonly_discovery_handoffs.py",
    "scripts/test_moomoo_readonly_discovery_handoff_builder.py",
    "tools/production_discovery/handoff/EVIDENCE_REVIEW_CHECKLIST.md",
    "tools/production_discovery/handoff/LOCAL_AGENT_PROMPT.md",
    "tools/production_discovery/handoff/README_FIRST.md",
    "tools/production_discovery/handoff/run-readonly-discovery.ps1",
    "tools/production_discovery/handoff/verify-handoff.ps1",
    "tools/production_discovery/handoff_test_python_shim.py",
    "tools/production_discovery/run_moomoo_readonly_discovery_handoff_tests.ps1",
]


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def copy_from_source(path: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    data = subprocess.run(
        ["git", "show", f"{SOURCE_REF}:{path}"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    target.write_bytes(data)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_gitattributes() -> None:
    path = ROOT / ".gitattributes"
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    additions = [
        "tools/production_discovery/handoff/*.ps1 text eol=lf",
        "tools/production_discovery/handoff/*.md text eol=lf",
        "scripts/build_moomoo_readonly_discovery_handoff.py text eol=lf",
        "scripts/compare_moomoo_readonly_discovery_handoffs.py text eol=lf",
        "scripts/test_moomoo_readonly_discovery_handoff_builder.py text eol=lf",
        ".github/workflows/moomoo-handoff-windows-validation.yml text eol=lf",
    ]
    for line in additions:
        if line not in text.splitlines():
            if text and not text.endswith("\n"):
                text += "\n"
            text += line + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_builder() -> None:
    path = ROOT / "scripts/build_moomoo_readonly_discovery_handoff.py"
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    text = replace_once(text, "import shutil\n", "import shutil\nimport stat\n", "builder import stat")
    text = replace_once(
        text,
        'HANDOFF_VERSION = "1.2.2"\nOPERATOR_VERSION = "1.2.2"\n',
        'HANDOFF_VERSION = "1.2.2"\nHANDOFF_FORMAT_VERSION = 1\nOPERATOR_VERSION = "1.2.2"\n',
        "builder format version",
    )
    text = replace_once(
        text,
        '''def sha256_file(path: Path) -> str:\n    return sha256_bytes(path.read_bytes())\n\n\n''',
        '''def sha256_file(path: Path) -> str:\n    digest = hashlib.sha256()\n    with path.open("rb") as handle:\n        for block in iter(lambda: handle.read(1024 * 1024), b""):\n            digest.update(block)\n    return digest.hexdigest()\n\n\ndef remove_tree(path: Path) -> None:\n    if not path.exists():\n        return\n\n    def clear_readonly_and_retry(function, target, _exc_info) -> None:\n        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)\n        function(target)\n\n    shutil.rmtree(path, onerror=clear_readonly_and_retry)\n\n\ndef validate_top_level_zip_infos(\n    infos: list[zipfile.ZipInfo],\n    label: str,\n) -> list[zipfile.ZipInfo]:\n    files: list[zipfile.ZipInfo] = []\n    seen: dict[str, str] = {}\n    for info in infos:\n        name = info.filename\n        normalized = name.replace("\\\\", "/")\n        if (\n            not name\n            or "\\x00" in name\n            or info.is_dir()\n            or normalized.startswith("/")\n            or normalized.startswith("//")\n            or re.match(r"^[A-Za-z]:", normalized)\n            or any(part in {"", ".", ".."} for part in normalized.split("/"))\n            or "/" in normalized\n        ):\n            raise BuildError(f"{label} contains unsafe ZIP entry: {name!r}")\n        key = normalized.casefold()\n        if key in seen:\n            raise BuildError(\n                f"{label} contains duplicate or case-colliding entries: "\n                f"{seen[key]!r}, {name!r}"\n            )\n        seen[key] = name\n        files.append(info)\n    return files\n\n\n''',
        "builder hashing and safe ZIP helpers",
    )
    text = replace_once(text, "import os\n", "import os\nimport re\n", "builder import re")
    text = replace_once(
        text,
        '''            infos = [info for info in archive.infolist() if not info.is_dir()]\n            names = [info.filename for info in infos]\n            duplicates = sorted(\n                {name for name in names if names.count(name) > 1}\n            )\n            if duplicates:\n                raise BuildError(\n                    f"Operator bundle contains duplicate entries: {duplicates}"\n                )\n''',
        '''            infos = validate_top_level_zip_infos(\n                archive.infolist(), "Operator bundle"\n            )\n            names = [info.filename for info in infos]\n''',
        "builder safe operator ZIP inspection",
    )
    text = replace_once(text, "        shutil.rmtree(stage)\n", "        remove_tree(stage)\n", "builder readonly cleanup")
    text = replace_once(
        text,
        '''        "schema_version": 1,\n        "handoff_version": HANDOFF_VERSION,\n''',
        '''        "schema_version": HANDOFF_FORMAT_VERSION,\n        "handoff_format_version": HANDOFF_FORMAT_VERSION,\n        "handoff_package_version": HANDOFF_VERSION,\n        "handoff_version": HANDOFF_VERSION,\n''',
        "builder manifest versions",
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_comparer() -> None:
    path = ROOT / "scripts/compare_moomoo_readonly_discovery_handoffs.py"
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    text = replace_once(text, "import json\n", "import json\nimport re\n", "comparer import re")
    text = replace_once(
        text,
        'HANDOFF_VERSION = "1.2.2"\nOPERATOR_VERSION = "1.2.2"\n',
        'HANDOFF_VERSION = "1.2.2"\nHANDOFF_FORMAT_VERSION = 1\nOPERATOR_VERSION = "1.2.2"\n',
        "comparer format version",
    )
    helper = '''\n\ndef validate_top_level_zip_infos(\n    infos: list[zipfile.ZipInfo],\n    label: str,\n) -> list[zipfile.ZipInfo]:\n    files: list[zipfile.ZipInfo] = []\n    seen: dict[str, str] = {}\n    for info in infos:\n        name = info.filename\n        normalized = name.replace("\\\\", "/")\n        if (\n            not name\n            or "\\x00" in name\n            or info.is_dir()\n            or normalized.startswith("/")\n            or normalized.startswith("//")\n            or re.match(r"^[A-Za-z]:", normalized)\n            or any(part in {"", ".", ".."} for part in normalized.split("/"))\n            or "/" in normalized\n        ):\n            raise ValueError(f"{label} contains unsafe ZIP entry: {name!r}")\n        key = normalized.casefold()\n        if key in seen:\n            raise ValueError(\n                f"{label} contains duplicate or case-colliding entries: "\n                f"{seen[key]!r}, {name!r}"\n            )\n        seen[key] = name\n        files.append(info)\n    return files\n'''
    text = replace_once(text, "\n\ndef parse_sums", helper + "\n\ndef parse_sums", "comparer safe ZIP helper")
    text = replace_once(
        text,
        '''            names = [\n                info.filename\n                for info in archive.infolist()\n                if not info.is_dir()\n            ]\n            duplicates = sorted(\n                {name for name in names if names.count(name) > 1}\n            )\n            result["duplicate_entries"] = duplicates\n''',
        '''            infos = validate_top_level_zip_infos(\n                archive.infolist(), "Handoff"\n            )\n            names = [info.filename for info in infos]\n            duplicates: list[str] = []\n            result["duplicate_entries"] = duplicates\n''',
        "comparer outer ZIP inspection",
    )
    text = replace_once(
        text,
        '''        if manifest.get("schema_version") != 1:\n            result["errors"].append("unexpected handoff schema_version")\n        if manifest.get("handoff_version") != HANDOFF_VERSION:\n''',
        '''        if manifest.get("schema_version") != HANDOFF_FORMAT_VERSION:\n            result["errors"].append("unexpected handoff schema_version")\n        if manifest.get("handoff_format_version") != HANDOFF_FORMAT_VERSION:\n            result["errors"].append("unexpected handoff format version")\n        if manifest.get("handoff_package_version") != HANDOFF_VERSION:\n            result["errors"].append("unexpected handoff package version")\n        if manifest.get("handoff_version") != HANDOFF_VERSION:\n''',
        "comparer manifest versions",
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def safe_extract_function(name: str = "Expand-SafeTopLevelZip") -> str:
    return f'''function {name} {{\n    param(\n        [Parameter(Mandatory = $true)][string]$ZipPath,\n        [Parameter(Mandatory = $true)][string]$DestinationPath\n    )\n    Add-Type -AssemblyName System.IO.Compression.FileSystem\n    $DestinationFull = [IO.Path]::GetFullPath($DestinationPath)\n    [IO.Directory]::CreateDirectory($DestinationFull) | Out-Null\n    $DestinationPrefix = $DestinationFull.TrimEnd(\n        [IO.Path]::DirectorySeparatorChar,\n        [IO.Path]::AltDirectorySeparatorChar\n    ) + [IO.Path]::DirectorySeparatorChar\n    $Archive = [IO.Compression.ZipFile]::OpenRead($ZipPath)\n    try {{\n        $Seen = @{{}}\n        $Validated = @()\n        foreach ($Entry in $Archive.Entries) {{\n            $Name = [string]$Entry.FullName\n            $Normalized = $Name.Replace('\\', '/')\n            if ([string]::IsNullOrWhiteSpace($Name) -or\n                [IO.Path]::IsPathRooted($Name) -or\n                $Normalized.StartsWith('/') -or\n                $Normalized -match '^[A-Za-z]:' -or\n                $Normalized -match '(^|/)\\.\\.(/|$)' -or\n                $Normalized.Contains('/') -or\n                [string]::IsNullOrEmpty([string]$Entry.Name)) {{\n                throw "ZIP contains unsafe or non-top-level entry: $Name"\n            }}\n            $Key = $Normalized.ToLowerInvariant()\n            if ($Seen.ContainsKey($Key)) {{\n                throw "ZIP contains duplicate or case-colliding entries: $($Seen[$Key]), $Name"\n            }}\n            $Seen[$Key] = $Name\n            $Target = [IO.Path]::GetFullPath([IO.Path]::Combine($DestinationFull, $Name))\n            if (-not $Target.StartsWith($DestinationPrefix, [StringComparison]::OrdinalIgnoreCase)) {{\n                throw "ZIP entry escapes extraction root: $Name"\n            }}\n            $Validated += [pscustomobject]@{{ entry = $Entry; target = $Target }}\n        }}\n        foreach ($Row in $Validated) {{\n            [IO.Compression.ZipFileExtensions]::ExtractToFile(\n                $Row.entry,\n                $Row.target,\n                $false\n            )\n        }}\n    }} finally {{\n        $Archive.Dispose()\n    }}\n}}\n'''


def patch_verifier() -> None:
    path = ROOT / "tools/production_discovery/handoff/verify-handoff.ps1"
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    text = replace_once(
        text,
        '''if ([int]$Manifest.schema_version -ne 1) {\n    throw "Unexpected handoff manifest schema_version: $($Manifest.schema_version)"\n}\nif ([string]$Manifest.handoff_version -ne "1.2.2") {\n''',
        '''if ([int]$Manifest.schema_version -ne 1 -or\n    [int]$Manifest.handoff_format_version -ne 1) {\n    throw "Unexpected handoff manifest format version"\n}\nif ([string]$Manifest.handoff_package_version -ne "1.2.2" -or\n    [string]$Manifest.handoff_version -ne "1.2.2") {\n''',
        "verifier manifest versions",
    )
    start = text.index("Add-Type -AssemblyName System.IO.Compression.FileSystem\n")
    end = text.index('\nWrite-Host "Handoff verification PASS"', start)
    replacement = safe_extract_function() + '''\n$ExtractionRoot = Join-Path ([IO.Path]::GetTempPath()) (\n    "moomoo-handoff-verify-" + [Guid]::NewGuid().ToString("N")\n)\ntry {\n    Expand-SafeTopLevelZip -ZipPath $BundlePath -DestinationPath $ExtractionRoot\n\n    $OperatorManifestPath = Join-Path $ExtractionRoot "bundle-manifest.json"\n    $OperatorSumsPath = Join-Path $ExtractionRoot "SHA256SUMS.txt"\n    if (-not (Test-Path -LiteralPath $OperatorManifestPath -PathType Leaf) -or\n        -not (Test-Path -LiteralPath $OperatorSumsPath -PathType Leaf)) {\n        throw "Operator bundle manifest or checksums are missing"\n    }\n    $OperatorManifest = Get-Content -LiteralPath $OperatorManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json\n    if ([string]$OperatorManifest.operator_version -ne "1.2.2") {\n        throw "Operator bundle manifest version mismatch"\n    }\n    if (([string]$OperatorManifest.source_commit).ToLowerInvariant() -ne $ExpectedHead) {\n        throw "Operator bundle manifest source_commit mismatch"\n    }\n    if ([string]$OperatorManifest.authorization.production_readiness -ne "BLOCKED" -or\n        [bool]$OperatorManifest.authorization.preflight_authorized -or\n        [bool]$OperatorManifest.authorization.production_drill_authorized -or\n        [bool]$OperatorManifest.authorization.cutover_authorized) {\n        throw "Operator bundle authorization boundary is invalid"\n    }\n\n    $OperatorExpected = @{}\n    foreach ($Line in Get-Content -LiteralPath $OperatorSumsPath -Encoding UTF8) {\n        if ([string]::IsNullOrWhiteSpace($Line)) { continue }\n        if ($Line -notmatch '^([0-9a-fA-F]{64})  (.+)$') {\n            throw "Invalid operator checksum line: $Line"\n        }\n        $Name = $Matches[2]\n        Assert-SafeRelativeFilename -Name $Name\n        $Key = $Name.ToLowerInvariant()\n        if ($OperatorExpected.ContainsKey($Key)) {\n            throw "Duplicate operator checksum entry: $Name"\n        }\n        $OperatorExpected[$Key] = [pscustomobject]@{ name = $Name; digest = $Matches[1].ToLowerInvariant() }\n    }\n    $ActualOperatorFiles = @(Get-ChildItem -LiteralPath $ExtractionRoot -File | Sort-Object Name)\n    $ExpectedOperatorNames = @(\n        @($OperatorExpected.Values | ForEach-Object { $_.name }) +\n        @("SHA256SUMS.txt", "bundle-manifest.json") |\n        Sort-Object\n    )\n    $ActualOperatorNames = @($ActualOperatorFiles | ForEach-Object { $_.Name } | Sort-Object)\n    if (($ExpectedOperatorNames -join "`n") -ne ($ActualOperatorNames -join "`n")) {\n        throw "Operator checksum coverage does not exactly match extracted files"\n    }\n    foreach ($Row in $OperatorExpected.Values) {\n        $OperatorPath = Join-Path $ExtractionRoot $Row.name\n        $Actual = (Get-FileHash -LiteralPath $OperatorPath -Algorithm SHA256).Hash.ToLowerInvariant()\n        if ($Actual -ne $Row.digest) {\n            throw "Operator internal SHA-256 mismatch: $($Row.name)"\n        }\n    }\n} finally {\n    if (Test-Path -LiteralPath $ExtractionRoot) {\n        Remove-Item -LiteralPath $ExtractionRoot -Recurse -Force -ErrorAction SilentlyContinue\n    }\n}\n'''
    text = text[:start] + replacement + text[end:]
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_runner() -> None:
    path = ROOT / "tools/production_discovery/handoff/run-readonly-discovery.ps1"
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    text = replace_once(
        text,
        '''    $PreviousPreference = $ErrorActionPreference\n    $Raw = @()\n    $ExitCode = $null\n    try {\n        $ErrorActionPreference = "Continue"\n        $Raw = @(& $FilePath @Arguments 2>&1)\n        $ExitCode = $LASTEXITCODE\n''',
        '''    $Command = Get-Command $FilePath -ErrorAction SilentlyContinue\n    if ($null -eq $Command) {\n        throw "$Description failed: command '$FilePath' was not found"\n    }\n    $PreviousPreference = $ErrorActionPreference\n    $Raw = @()\n    $ExitCode = $null\n    try {\n        $ErrorActionPreference = "Continue"\n        $global:LASTEXITCODE = $null\n        $Raw = @(& $Command.Source @Arguments 2>&1)\n        $ExitCode = $LASTEXITCODE\n''',
        "runner command preflight",
    )
    text = replace_once(
        text,
        '''    if ($ExitCode -ne 0) {\n        throw "$Description failed with exit code $ExitCode. Output: $Text"\n    }\n''',
        '''    if ($null -eq $ExitCode) {\n        throw "$Description did not produce a native exit code. Output: $Text"\n    }\n    if ($ExitCode -ne 0) {\n        throw "$Description failed with exit code $ExitCode. Output: $Text"\n    }\n''',
        "runner null exit code",
    )
    marker = "\nif ($env:OS -ne \"Windows_NT\") {"
    text = replace_once(text, marker, "\n" + safe_extract_function() + marker, "runner safe extraction helper")
    text = replace_once(
        text,
        "    Expand-Archive -LiteralPath $BundleZip -DestinationPath $ExtractionRoot -Force\n",
        "    Expand-SafeTopLevelZip -ZipPath $BundleZip -DestinationPath $ExtractionRoot\n",
        "runner safe operator extraction",
    )
    text = replace_once(
        text,
        '''        $ErrorActionPreference = "Continue"\n        $OperatorRaw = @(& $PythonExecutable @Arguments 2>&1)\n        $OperatorExitCode = $LASTEXITCODE\n''',
        '''        $ErrorActionPreference = "Continue"\n        $PythonCommand = Get-Command $PythonExecutable -ErrorAction SilentlyContinue\n        if ($null -eq $PythonCommand) {\n            throw "Operator execution failed: command '$PythonExecutable' was not found"\n        }\n        $global:LASTEXITCODE = $null\n        $OperatorRaw = @(& $PythonCommand.Source @Arguments 2>&1)\n        $OperatorExitCode = $LASTEXITCODE\n        if ($null -eq $OperatorExitCode) {\n            throw "Operator execution did not produce a native exit code"\n        }\n''',
        "runner operator command",
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_test_harness() -> None:
    path = ROOT / "tools/production_discovery/run_moomoo_readonly_discovery_handoff_tests.ps1"
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    text = replace_once(
        text,
        '''    $Saved = @{}\n    foreach ($Key in $Environment.Keys) {\n''',
        '''    $Command = Get-Command $FilePath -ErrorAction SilentlyContinue\n    if ($null -eq $Command) {\n        throw "Command '$FilePath' was not found"\n    }\n    $Saved = @{}\n    foreach ($Key in $Environment.Keys) {\n''',
        "test harness command preflight",
    )
    text = replace_once(
        text,
        '''        $ErrorActionPreference = "Continue"\n        $Raw = @(& $FilePath @Arguments 2>&1)\n        $ExitCode = $LASTEXITCODE\n''',
        '''        $ErrorActionPreference = "Continue"\n        $global:LASTEXITCODE = $null\n        $Raw = @(& $Command.Source @Arguments 2>&1)\n        $ExitCode = $LASTEXITCODE\n''',
        "test harness reset exit code",
    )
    text = replace_once(
        text,
        '''    $Lines = @(\n''',
        '''    if ($null -eq $ExitCode) {\n        throw "Command '$FilePath' did not produce a native exit code"\n    }\n    $Lines = @(\n''',
        "test harness null exit code",
    )
    marker = "\nfunction Rewrite-HandoffChecksums {"
    text = replace_once(text, marker, "\n" + safe_extract_function("Expand-SafeTestZip") + marker, "test harness safe extraction helper")
    text = replace_once(
        text,
        "    Expand-Archive -LiteralPath $HandoffZip -DestinationPath $Package -Force\n",
        "    Expand-SafeTestZip -ZipPath $HandoffZip -DestinationPath $Package\n",
        "test harness safe outer extraction",
    )
    text = text.replace(
        "moomoo_production_discovery_operator_v4_v1.2.1.zip",
        "moomoo_production_discovery_operator_v4_v1.2.2.zip",
    )
    insert_after = '''    Assert-Success -Result $VerifyResult -Name "positive handoff verification"\n'''
    addition = '''\n    $MissingCommandRejected = $false\n    try {\n        Invoke-NativeCapture -Name "negative-missing-command" `\n            -FilePath "moomoo-command-that-does-not-exist.exe" | Out-Null\n    } catch {\n        $MissingCommandRejected = $true\n    }\n    if (-not $MissingCommandRejected) {\n        throw "Missing native command was not rejected"\n    }\n\n'''
    text = replace_once(text, insert_after, insert_after + addition, "test harness missing command test")
    nested_marker = '''    Assert-Failure -Result $TamperedBundleResult -Name "tampered operator bundle"\n'''
    nested_addition = '''\n    $TraversalPackage = Copy-Package -Source $Package -Destination (Join-Path $TempRoot "nested-traversal")\n    $TraversalBundlePath = Join-Path $TraversalPackage "moomoo_production_discovery_operator_v4_v1.2.2.zip"\n    Add-Type -AssemblyName System.IO.Compression.FileSystem\n    $TraversalArchive = [IO.Compression.ZipFile]::Open($TraversalBundlePath, [IO.Compression.ZipArchiveMode]::Update)\n    try {\n        $TraversalEntry = $TraversalArchive.CreateEntry("../escape.txt")\n        $Writer = New-Object IO.StreamWriter($TraversalEntry.Open())\n        try { $Writer.Write("escape") } finally { $Writer.Dispose() }\n    } finally {\n        $TraversalArchive.Dispose()\n    }\n    $TraversalManifestPath = Join-Path $TraversalPackage "HANDOFF_MANIFEST.json"\n    $TraversalManifest = Get-Content -LiteralPath $TraversalManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json\n    $TraversalManifest.operator_bundle.sha256 = (\n        Get-FileHash -LiteralPath $TraversalBundlePath -Algorithm SHA256\n    ).Hash.ToLowerInvariant()\n    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)\n    [IO.File]::WriteAllText(\n        $TraversalManifestPath,\n        (($TraversalManifest | ConvertTo-Json -Depth 20) + "`n"),\n        $Utf8NoBom\n    )\n    Rewrite-HandoffChecksums -Package $TraversalPackage\n    $TraversalResult = Invoke-NativeCapture -Name "negative-nested-traversal" `\n        -FilePath $PowerShellExecutable `\n        -Arguments @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $TraversalPackage "verify-handoff.ps1"))\n    Assert-Failure -Result $TraversalResult -Name "nested ZIP traversal"\n'''
    text = replace_once(text, nested_marker, nested_marker + nested_addition, "test harness nested traversal")
    text = replace_once(
        text,
        '''                "tampered_operator_bundle",\n                "authorization_boundaries",\n''',
        '''                "tampered_operator_bundle",\n                "nested_zip_traversal",\n                "missing_native_command",\n                "authorization_boundaries",\n''',
        "test harness negative list",
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_builder_tests() -> None:
    path = ROOT / "scripts/test_moomoo_readonly_discovery_handoff_builder.py"
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    text = replace_once(text, "import json\n", "import json\nimport os\nimport stat\n", "builder tests imports")
    text = replace_once(
        text,
        '''        corrupt_sum: bool = False,\n    ) -> None:\n''',
        '''        corrupt_sum: bool = False,\n        unsafe_entry: str | None = None,\n    ) -> None:\n''',
        "builder tests unsafe argument",
    )
    text = replace_once(
        text,
        '''            if duplicate:\n                archive.writestr(source_members[0], b"duplicate")\n''',
        '''            if duplicate:\n                archive.writestr(source_members[0], b"duplicate")\n            if unsafe_entry:\n                archive.writestr(unsafe_entry, b"unsafe")\n''',
        "builder tests unsafe operator entry",
    )
    after_duplicate = '''    def test_operator_bundle_rejects_duplicate_entries(self) -> None:\n        with tempfile.TemporaryDirectory() as tmp:\n            path = Path(tmp) / "operator.zip"\n            self.make_operator_bundle(path, "a" * 40, duplicate=True)\n            with self.assertRaises(builder.BuildError):\n                builder.inspect_operator_bundle(path, "a" * 40)\n\n'''
    extra_tests = '''    def test_operator_bundle_rejects_traversal_entry(self) -> None:\n        with tempfile.TemporaryDirectory() as tmp:\n            path = Path(tmp) / "operator.zip"\n            self.make_operator_bundle(\n                path, "a" * 40, unsafe_entry="../escape.txt"\n            )\n            with self.assertRaises(builder.BuildError):\n                builder.inspect_operator_bundle(path, "a" * 40)\n\n    def test_remove_tree_handles_readonly_files(self) -> None:\n        with tempfile.TemporaryDirectory() as tmp:\n            root = Path(tmp) / "stage"\n            root.mkdir()\n            target = root / "readonly.txt"\n            target.write_text("readonly", encoding="utf-8")\n            os.chmod(target, stat.S_IREAD)\n            builder.remove_tree(root)\n            self.assertFalse(root.exists())\n\n'''
    text = replace_once(text, after_duplicate, after_duplicate + extra_tests, "builder tests security cases")
    text = replace_once(
        text,
        '''    def test_handoff_compare_rejects_tampered_member(self) -> None:\n''',
        '''    def test_handoff_compare_rejects_traversal_entry(self) -> None:\n        with tempfile.TemporaryDirectory() as tmp:\n            path = Path(tmp) / "unsafe.zip"\n            self.make_minimal_handoff(path)\n            with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as archive:\n                archive.writestr("../escape.txt", b"escape")\n            report = comparer.inspect_handoff(path)\n            self.assertFalse(report["valid"])\n            self.assertTrue(report["errors"])\n\n    def test_handoff_compare_rejects_tampered_member(self) -> None:\n''',
        "builder tests outer traversal",
    )
    text = replace_once(
        text,
        '''            "schema_version": 1,\n            "handoff_version": comparer.HANDOFF_VERSION,\n''',
        '''            "schema_version": comparer.HANDOFF_FORMAT_VERSION,\n            "handoff_format_version": comparer.HANDOFF_FORMAT_VERSION,\n            "handoff_package_version": comparer.HANDOFF_VERSION,\n            "handoff_version": comparer.HANDOFF_VERSION,\n''',
        "builder tests manifest versions",
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_docs() -> None:
    for relative in [
        "tools/production_discovery/handoff/README_FIRST.md",
        "tools/production_discovery/handoff/LOCAL_AGENT_PROMPT.md",
    ]:
        path = ROOT / relative
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        marker = "handoff version"
        if marker not in text.lower():
            text += (\n                "\n## Version contract\n\n"\n                "- operator version: `1.2.2`\n"\n                "- handoff package version: `1.2.2`\n"\n                "- handoff format version: `1`\n"\n                "\nこれらは別々のversion軸です。機械検証PASSでもpreflightは承認されません。\n"\n            )
        path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    run("git", "fetch", "origin", "agent/master-bound-readonly-handoff")
    for relative in COPY_PATHS:
        copy_from_source(relative)
    patch_gitattributes()
    patch_builder()
    patch_comparer()
    patch_verifier()
    patch_runner()
    patch_test_harness()
    patch_builder_tests()
    patch_docs()

    (ROOT / "scripts/_apply_verified_handoff_split.py").unlink()
    (ROOT / ".github/workflows/_apply-verified-handoff-split.yml").unlink()

    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("git", "add", "-A")
    run("git", "commit", "-m", "ops: add verified readonly discovery handoff")

    run(\n        sys.executable,\n        "-m",\n        "py_compile",\n        "scripts/build_moomoo_readonly_discovery_handoff.py",\n        "scripts/compare_moomoo_readonly_discovery_handoffs.py",\n        "scripts/test_moomoo_readonly_discovery_handoff_builder.py",\n        "tools/production_discovery/handoff_test_python_shim.py",\n    )
    run(sys.executable, "scripts/test_moomoo_readonly_discovery_handoff_builder.py", "-v")
    run("git", "push", "origin", "HEAD:agent/verified-readonly-handoff")


if __name__ == "__main__":
    main()
