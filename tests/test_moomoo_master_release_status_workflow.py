from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "moomoo-master-release-status.yml"


def load_workflow() -> dict[str, object]:
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    payload: dict[Any, Any] = dict(raw)
    # PyYAML follows YAML 1.1 and may parse the GitHub Actions key `on`
    # as boolean true. Normalize only that top-level compatibility case.
    if "on" not in payload and True in payload:
        payload["on"] = payload.pop(True)
    return cast(dict[str, object], payload)


def test_master_release_status_workflow_contract() -> None:
    workflow = load_workflow()
    trigger = workflow["on"]
    assert isinstance(trigger, dict)
    workflow_run = trigger["workflow_run"]
    assert isinstance(workflow_run, dict)
    assert workflow_run["workflows"] == [
        "moomoo discovery master-bound release"
    ]
    assert workflow_run["types"] == ["completed"]

    permissions = workflow["permissions"]
    assert isinstance(permissions, dict)
    assert permissions == {"contents": "read", "statuses": "write"}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["publish-master-release-status"]
    assert isinstance(job, dict)
    condition = str(job["if"])
    assert "workflow_run.event == 'push'" in condition
    assert "workflow_run.head_branch == 'master'" in condition

    steps = job["steps"]
    assert isinstance(steps, list) and len(steps) == 1
    step = steps[0]
    assert isinstance(step, dict)
    assert step["uses"] == "actions/github-script@v7"
    script = str(step["with"]["script"])
    assert "createCommitStatus" in script
    assert "sha: run.head_sha" in script
    assert "target_url: run.html_url" in script
    assert "context: 'moomoo/master-release'" in script
    assert "run.conclusion === 'success'" in script
