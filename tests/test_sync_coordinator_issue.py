from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.sync_coordinator_issue import (
    END_MARKER,
    START_MARKER,
    collect_state,
    render_block,
    replace_block,
)

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/sync-coordinator-issue.yml"
)


def make_runner(
    *,
    default_branch: str = "master",
    head: dict[str, Any] | None = None,
    open_prs: list[dict[str, Any]] | None = None,
    merged: list[dict[str, Any]] | None = None,
    closed: list[dict[str, Any]] | None = None,
):
    head = head or {
        "sha": "a" * 40,
        "commit": {
            "message": "feat: sample (#1)\n\nbody text",
            "committer": {"date": "2026-08-16T12:24:00Z"},
        },
    }

    def run(command: list[str]) -> str:
        joined = " ".join(command)
        if "--jq .default_branch" in joined:
            return default_branch
        if "/commits/" in joined:
            return json.dumps(head)
        if "pr list" in joined:
            if "--state open" in joined:
                return json.dumps(open_prs or [])
            if "--state merged" in joined:
                return json.dumps(merged or [])
            if "--state closed" in joined:
                return json.dumps(closed or [])
        raise AssertionError(f"unexpected gh command: {command}")

    return run


def test_collect_state_sorts_and_shortens_sha() -> None:
    runner = make_runner(
        open_prs=[
            {
                "number": 2,
                "title": "second",
                "headRefName": "feat/b",
                "baseRefName": "master",
                "isDraft": False,
                "mergeable": "MERGEABLE",
                "updatedAt": "2026-08-16T00:00:00Z",
            },
            {
                "number": 1,
                "title": "first",
                "headRefName": "feat/a",
                "baseRefName": "master",
                "isDraft": True,
                "mergeable": None,
                "updatedAt": "2026-08-15T00:00:00Z",
            },
        ],
        merged=[{"number": 3, "title": "old", "mergedAt": "2026-08-14T00:00:00Z"}],
        closed=[{"number": 4, "title": "wont", "closedAt": "2026-08-13T00:00:00Z"}],
    )
    state = collect_state("owner/repo", runner=runner)
    assert state["branch"] == "master"
    assert state["head_sha_short"] == "a" * 7
    assert state["head_message"] == "feat: sample (#1)"
    assert [pr["number"] for pr in state["open_prs"]] == [1, 2]
    assert state["closed_unmerged"] == [
        {"number": 4, "title": "wont", "closedAt": "2026-08-13T00:00:00Z"}
    ]


def test_collect_state_filters_merged_out_of_closed() -> None:
    runner = make_runner(
        closed=[
            {
                "number": 4,
                "title": "merged one",
                "closedAt": "2026-08-13T00:00:00Z",
                "mergedAt": "2026-08-13T00:01:00Z",
            },
            {
                "number": 5,
                "title": "closed unmerged",
                "closedAt": "2026-08-12T00:00:00Z",
                "mergedAt": None,
            },
        ]
    )
    state = collect_state("owner/repo", runner=runner)
    assert [pr["number"] for pr in state["closed_unmerged"]] == [5]


def test_render_block_contains_all_sections() -> None:
    state = {
        "branch": "master",
        "head_sha_short": "78d3267",
        "head_message": "[V2] research core (#87)",
        "head_date": "2026-08-16T12:24:00Z",
        "open_prs": [
            {
                "number": 12,
                "title": "add feature",
                "headRefName": "feat/new",
                "baseRefName": "master",
                "isDraft": False,
                "mergeable": "MERGEABLE",
            }
        ],
        "merged": [{"number": 87, "title": "research core", "mergedAt": "2026-08-16T12:24:00Z"}],
        "closed_unmerged": [{"number": 76, "title": "grid research", "closedAt": "2026-08-16T12:01:00Z"}],
    }
    block = render_block(state, generated_at="2026-08-17T07:00:00Z")
    assert block.startswith(START_MARKER)
    assert block.endswith(END_MARKER + "\n")
    assert "Open PR（1）" in block
    assert "| #12 | add feature | feat/new | master | ready | MERGEABLE |" in block
    assert "#87 research core（2026-08-16T12:24:00Z）" in block
    assert "#76 grid research（closed 2026-08-16T12:01:00Z）" in block
    assert "source: `master` @ `78d3267`" in block
    assert "generated at 2026-08-17T07:00:00Z" in block


def test_render_block_empty_lists_and_draft_state() -> None:
    state = {
        "branch": "master",
        "head_sha_short": "abc1234",
        "head_message": "chore: x",
        "head_date": "2026-08-16T00:00:00Z",
        "open_prs": [
            {
                "number": 9,
                "title": "draft pr",
                "headRefName": "feat/d",
                "baseRefName": "master",
                "isDraft": True,
                "mergeable": None,
            }
        ],
        "merged": [],
        "closed_unmerged": [],
    }
    block = render_block(state, generated_at="2026-08-17T07:00:00Z")
    assert "Open PR（1）" in block
    assert "| #9 | draft pr | feat/d | master | draft | — |" in block
    assert block.count("_なし_") == 2


def test_render_block_escapes_table_pipes() -> None:
    state = {
        "branch": "master",
        "head_sha_short": "abc1234",
        "head_message": "chore: x",
        "head_date": "2026-08-16T00:00:00Z",
        "open_prs": [
            {
                "number": 3,
                "title": "feature | other",
                "headRefName": "feat/x",
                "baseRefName": "master",
                "isDraft": False,
                "mergeable": "MERGEABLE",
            }
        ],
        "merged": [],
        "closed_unmerged": [],
    }
    block = render_block(state, generated_at="2026-08-17T07:00:00Z")
    assert "feature ｜ other" in block
    assert "|" in block


def test_replace_block_appends_when_no_markers() -> None:
    body = "# Coordinator\n\npolicy text\n"
    block = "<!-- COORDINATOR-GENERATED:START -->\ncontent\n<!-- COORDINATOR-GENERATED:END -->\n"
    new_body, changed = replace_block(body, block)
    assert changed is True
    assert new_body.startswith("# Coordinator\n\npolicy text")
    assert START_MARKER in new_body
    assert "policy text" in new_body.split(START_MARKER)[0]


def test_replace_block_replaces_existing_section() -> None:
    body = (
        "# Coordinator\n\npolicy text\n\n"
        + "<!-- COORDINATOR-GENERATED:START -->\nold generated\n<!-- COORDINATOR-GENERATED:END -->\n"
        + "\ntrailing policy\n"
    )
    block = "<!-- COORDINATOR-GENERATED:START -->\nnew generated\n<!-- COORDINATOR-GENERATED:END -->\n"
    new_body, changed = replace_block(body, block)
    assert changed is True
    assert "new generated" in new_body
    assert "old generated" not in new_body
    assert "policy text" in new_body
    assert "trailing policy" in new_body


def test_replace_block_no_change_when_identical() -> None:
    block = "<!-- COORDINATOR-GENERATED:START -->\n<!-- generated at 2026-08-17T07:00:00Z -->\nsame\n<!-- COORDINATOR-GENERATED:END -->\n"
    body = "# policy\n\n" + block
    new_body, changed = replace_block(body, block)
    assert changed is False
    assert new_body == body


def test_replace_block_ignores_timestamp_for_change_detection() -> None:
    old_block = "<!-- COORDINATOR-GENERATED:START -->\n<!-- generated at 2026-08-16T07:00:00Z -->\nsame\n<!-- COORDINATOR-GENERATED:END -->\n"
    new_block = "<!-- COORDINATOR-GENERATED:START -->\n<!-- generated at 2026-08-17T07:00:00Z -->\nsame\n<!-- COORDINATOR-GENERATED:END -->\n"
    body = "# policy\n\n" + old_block
    _, changed = replace_block(body, new_block)
    assert changed is False


def test_replace_block_detects_content_change() -> None:
    old_block = "<!-- COORDINATOR-GENERATED:START -->\n<!-- generated at 2026-08-16T07:00:00Z -->\nold\n<!-- COORDINATOR-GENERATED:END -->\n"
    new_block = "<!-- COORDINATOR-GENERATED:START -->\n<!-- generated at 2026-08-17T07:00:00Z -->\nnew\n<!-- COORDINATOR-GENERATED:END -->\n"
    body = "# policy\n\n" + old_block
    _, changed = replace_block(body, new_block)
    assert changed is True


@pytest.mark.parametrize(
    "body",
    [
        "<!-- COORDINATOR-GENERATED:START -->\nno end\n",
        "no start\n<!-- COORDINATOR-GENERATED:END -->\n",
        "<!-- COORDINATOR-GENERATED:END -->\n<!-- COORDINATOR-GENERATED:START -->\n",
        "<!-- COORDINATOR-GENERATED:START -->\n<!-- COORDINATOR-GENERATED:START -->\nx\n<!-- COORDINATOR-GENERATED:END -->\n",
    ],
)
def test_replace_block_rejects_malformed_markers(body: str) -> None:
    block = "<!-- COORDINATOR-GENERATED:START -->\nx\n<!-- COORDINATOR-GENERATED:END -->\n"
    with pytest.raises(ValueError):
        replace_block(body, block)


def test_workflow_contract() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "schedule:" in text
    assert "cron:" in text
    assert "workflow_dispatch:" in text
    assert "issues: write" in text
    assert "scripts/sync_coordinator_issue.py" in text
    assert "--write" in text
    assert "COORDINATOR_ISSUE" in text
    assert "issue view" in text or "sync_coordinator_issue" in text
