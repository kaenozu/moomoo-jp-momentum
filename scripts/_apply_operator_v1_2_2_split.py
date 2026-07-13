from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REF = "origin/agent/master-bound-readonly-handoff"
COPY_PATHS = [
    ".github/workflows/moomoo-operator-windows-validation.yml",
    "scripts/build_moomoo_discovery_operator_bundle.py",
    "scripts/compare_moomoo_discovery_operator_bundles.py",
    "tools/production_discovery/README_moomoo_discovery_operator_ja.md",
    "tools/production_discovery/moomoo_operator_common.py",
    "tools/production_discovery/test_bundle_builder.py",
    "tools/production_discovery/test_moomoo_operator_common_errors.py",
    "tools/production_discovery/validate_moomoo_discovery_operator.py",
]
RUNTIME = ROOT / "tools/production_discovery/moomoo_discovery_v4_runtime.ps1"
COMMON = ROOT / "tools/production_discovery/moomoo_operator_common.py"


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


def replace_scheduled_task_function() -> None:
    text = RUNTIME.read_text(encoding="utf-8")
    replacement = r'''function Get-OptionalPropertyValue {
    param(
        [AllowNull()]$InputObject,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ($null -eq $InputObject) {
        return $null
    }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    $property.Value
}

function Get-RelevantScheduledTasks {
    $rows = @()
    foreach ($task in (Get-ScheduledTask -ErrorAction Stop)) {
        $taskPath = [string](Get-OptionalPropertyValue -InputObject $task -Name "TaskPath")
        $taskName = [string](Get-OptionalPropertyValue -InputObject $task -Name "TaskName")
        $taskState = [string](Get-OptionalPropertyValue -InputObject $task -Name "State")
        $principal = Get-OptionalPropertyValue -InputObject $task -Name "Principal"
        foreach ($action in @(Get-OptionalPropertyValue -InputObject $task -Name "Actions")) {
            if ($null -eq $action) {
                continue
            }
            $execute = [string](Get-OptionalPropertyValue -InputObject $action -Name "Execute")
            $arguments = [string](Get-OptionalPropertyValue -InputObject $action -Name "Arguments")
            $workingDirectory = [string](Get-OptionalPropertyValue -InputObject $action -Name "WorkingDirectory")
            $text = "$taskPath $taskName $execute $arguments $workingDirectory"
            if ($text -notmatch $writerPattern) {
                continue
            }
            $rows += [pscustomobject]@{
                task_path = $taskPath
                task_name = $taskName
                state = $taskState
                execute = $execute
                arguments = $arguments
                working_directory = if ($workingDirectory) { $workingDirectory } else { $null }
                principal_user_id = if ($principal) { [string](Get-OptionalPropertyValue -InputObject $principal -Name "UserId") } else { $null }
                principal_logon_type = if ($principal) { [string](Get-OptionalPropertyValue -InputObject $principal -Name "LogonType") } else { $null }
                triggers = @(
                    @(Get-OptionalPropertyValue -InputObject $task -Name "Triggers") |
                        ForEach-Object { [string]$_ }
                )
            }
        }
    }
    @($rows)
}

'''
    updated, count = re.subn(
        r"\Afunction Get-RelevantScheduledTasks \{.*?\n\}\n\n(?=function Get-WriterProcesses)",
        replacement,
        text,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise RuntimeError("Could not replace Get-RelevantScheduledTasks")
    RUNTIME.write_text(updated, encoding="utf-8", newline="\n")


def update_runtime_hash() -> None:
    runtime_hash = hashlib.sha256(RUNTIME.read_bytes()).hexdigest()
    text = COMMON.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'("moomoo_discovery_v4_runtime\.ps1": ")[0-9a-f]{64}("[,])',
        rf"\g<1>{runtime_hash}\g<2>",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("Could not update frozen runtime SHA-256")
    COMMON.write_text(updated, encoding="utf-8", newline="\n")


def main() -> None:
    run("python", "-m", "pip", "install", "PyYAML")
    run("git", "fetch", "origin", "agent/master-bound-readonly-handoff")
    for path in COPY_PATHS:
        copy_from_source(path)
    replace_scheduled_task_function()
    update_runtime_hash()

    common = COMMON.read_text(encoding="utf-8")
    if 'VERSION = "1.2.2"' not in common:
        raise RuntimeError("Operator version was not updated to 1.2.2")

    (ROOT / "scripts/_apply_operator_v1_2_2_split.py").unlink()
    (ROOT / ".github/workflows/_apply-operator-v1.2.2-split.yml").unlink()

    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("git", "add", "-A")
    run("git", "commit", "-m", "fix: harden readonly discovery operator on Windows")

    run("python", "-m", "py_compile", "scripts/build_moomoo_discovery_operator_bundle.py", "scripts/compare_moomoo_discovery_operator_bundles.py")
    run("python", "tools/production_discovery/validate_moomoo_discovery_operator.py")
    run("python", "-m", "unittest", "discover", "-s", "tools/production_discovery", "-p", "test_*.py")

    run("git", "push", "origin", "HEAD:agent/operator-v1.2.2-windows-hardening")


if __name__ == "__main__":
    main()
