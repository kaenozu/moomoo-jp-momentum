#!/usr/bin/env python3
"""Synchronize the PR/merge status section of a Coordinator issue.

Coordinator issues (e.g. moomoo #77) list open PRs and merge history by
hand, which goes stale within hours of a merge burst. This script keeps a
dedicated section of the issue body fresh by regenerating it from the
GitHub CLI (`gh`).

Separation of concerns:

- Everything OUTSIDE the COORDINATOR-GENERATED markers is the hand-written
  policy part and is never touched.
- Everything INSIDE the markers is machine-generated on every run.

The block is only written back when its content (ignoring the generated-at
timestamp) actually changed, so scheduled runs do not churn the issue.

Typical flow:

    gh --repo owner/repo issue view 77          # policy part lives above the markers
    python3 scripts/sync_coordinator_issue.py --repo owner/repo --issue-number 77 --write
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

START_MARKER = "<!-- COORDINATOR-GENERATED:START -->"
END_MARKER = "<!-- COORDINATOR-GENERATED:END -->"
TIMESTAMP_PREFIX = "<!-- generated at "
DEFAULT_ISSUE_NUMBER = 77
DEFAULT_LIMIT = 10
OPEN_PR_LIMIT = 50

Runner = Callable[[Sequence[str]], str]


def run_gh(
    gh: str, repo: str, args: Sequence[str], runner: Runner | None = None
) -> str:
    """Run a gh subcommand against the given repository and return stdout.

    `gh api` does not accept the `--repo` flag; the repository is part of
    the endpoint path, so it is omitted for that subcommand.
    """
    if args and args[0] == "api":
        command = [gh, *args]
    else:
        command = [gh, "--repo", repo, *args]
    if runner is not None:
        return runner(command)
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8"
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(str(arg) for arg in args)} failed "
            f"(exit {completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout


def gh_json(
    gh: str, repo: str, args: Sequence[str], runner: Runner | None = None
) -> Any:
    return json.loads(run_gh(gh, repo, args, runner))


def collect_state(
    repo: str,
    *,
    gh: str = "gh",
    runner: Runner | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Fetch the default-branch HEAD and the PR/merge state from GitHub."""
    default_branch = run_gh(
        gh, repo, ["api", f"repos/{repo}", "--jq", ".default_branch"], runner
    ).strip()
    head = gh_json(
        gh, repo, ["api", f"repos/{repo}/commits/{default_branch}"], runner
    )
    head_message = str(head.get("commit", {}).get("message", ""))
    head_date = str(head.get("commit", {}).get("committer", {}).get("date", ""))
    head_sha = str(head.get("sha", ""))
    open_prs = gh_json(
        gh,
        repo,
        [
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            str(OPEN_PR_LIMIT),
            "--json",
            "number,title,headRefName,baseRefName,isDraft,mergeable,updatedAt",
        ],
        runner,
    )
    merged = gh_json(
        gh,
        repo,
        [
            "pr",
            "list",
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "number,title,mergedAt",
        ],
        runner,
    )
    closed = gh_json(
        gh,
        repo,
        [
            "pr",
            "list",
            "--state",
            "closed",
            "--limit",
            str(limit),
            "--json",
            "number,title,closedAt,mergedAt",
        ],
        runner,
    )
    closed_unmerged = [
        pr for pr in closed if pr.get("mergedAt") is None
    ]
    return {
        "branch": default_branch,
        "head_sha": head_sha,
        "head_sha_short": head_sha[:7],
        "head_message": head_message.splitlines()[0] if head_message else "",
        "head_date": head_date,
        "open_prs": sorted(open_prs, key=lambda pr: int(pr["number"])),
        "merged": sorted(merged, key=lambda pr: str(pr.get("mergedAt") or ""), reverse=True),
        "closed_unmerged": sorted(
            closed_unmerged,
            key=lambda pr: str(pr.get("closedAt") or ""),
            reverse=True,
        ),
    }


def _cell(value: Any) -> str:
    """Make a value safe for a single markdown table cell."""
    return str(value).replace("|", "｜").replace("\n", " ").strip()


def _empty_or_list(items: list[Any]) -> list[str]:
    return ["_なし_"] if not items else items


def render_block(state: dict[str, Any], *, generated_at: str) -> str:
    """Render the full machine-generated block including both markers."""
    open_prs = state["open_prs"]
    merged = state["merged"]
    closed = state["closed_unmerged"]
    lines = [
        START_MARKER,
        f"{TIMESTAMP_PREFIX}{generated_at} -->",
        "",
        "## PR / merge 状態（自動生成 — マーカー内は手で編集しない）",
        "",
        (
            f"- source: `{state['branch']}` @ `{state['head_sha_short']}` — "
            f"{state['head_message']}（{state['head_date']}）"
        ),
        "",
        f"### Open PR（{len(open_prs)}）",
        "",
    ]
    if open_prs:
        lines.append("| PR | Title | Branch | Base | State | Mergeable |")
        lines.append("|---|---|---|---|---|---|")
        for pr in open_prs:
            mergeable = str(pr.get("mergeable") or "—")
            state_label = "draft" if pr.get("isDraft") else "ready"
            lines.append(
                f"| #{pr['number']} | {_cell(pr['title'])} | {_cell(pr['headRefName'])} "
                f"| {_cell(pr['baseRefName'])} | {state_label} | {_cell(mergeable)} |"
            )
    else:
        lines.append("_なし_")
    lines.extend(["", f"### 最近 merge（{len(merged)}）", ""])
    for entry in _empty_or_list(merged):
        if isinstance(entry, str):
            lines.append(entry)
        else:
            lines.append(f"- #{entry['number']} {_cell(entry['title'])}（{entry['mergedAt']}）")
    lines.extend(["", f"### 最近 close（unmerged）（{len(closed)}）", ""])
    for entry in _empty_or_list(closed):
        if isinstance(entry, str):
            lines.append(entry)
        else:
            lines.append(
                f"- #{entry['number']} {_cell(entry['title'])}（closed {entry['closedAt']}）"
            )
    lines.extend(["", END_MARKER])
    return "\n".join(lines) + "\n"


def _strip_timestamp(block: str) -> str:
    return "\n".join(
        line for line in block.splitlines() if not line.startswith(TIMESTAMP_PREFIX)
    ).strip()


def replace_block(body: str, block: str) -> tuple[str, bool]:
    """Replace the generated section, returning (new_body, changed).

    Appends the block when the body has no markers yet (first-run
    bootstrap). Refuses to touch a body with malformed markers.
    """
    start = body.find(START_MARKER)
    end = body.find(END_MARKER)
    if start == -1 and end == -1:
        return body.rstrip() + "\n\n" + block, True
    if start == -1 or end == -1 or end < start:
        raise ValueError(
            "COORDINATOR-GENERATED markers are malformed (START must precede END)"
        )
    if body.find(START_MARKER, start + len(START_MARKER)) != -1:
        raise ValueError("multiple COORDINATOR-GENERATED START markers found")
    if body.find(END_MARKER, end + len(END_MARKER)) != -1:
        raise ValueError("multiple COORDINATOR-GENERATED END markers found")
    old_block = body[start : end + len(END_MARKER)]
    if _strip_timestamp(old_block) == _strip_timestamp(block):
        return body, False
    return body[:start] + block + body[end + len(END_MARKER) :], True


def write_issue_body(gh: str, repo: str, issue_number: int, body: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".md", delete=False
    ) as handle:
        handle.write(body)
        path = Path(handle.name)
    try:
        run_gh(gh, repo, ["issue", "edit", str(issue_number), "--body-file", str(path)])
    finally:
        path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Synchronize the PR/merge status section of a Coordinator issue "
            "between the COORDINATOR-GENERATED markers."
        )
    )
    parser.add_argument("--repo", required=True, help="owner/repository")
    parser.add_argument(
        "--issue-number",
        type=int,
        default=DEFAULT_ISSUE_NUMBER,
        help=f"Coordinator issue number (default: {DEFAULT_ISSUE_NUMBER})",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the generated block to the issue (default: dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do not write the generated block (explicit alias of the default)",
    )
    parser.add_argument("--gh", default="gh", help="gh executable (default: gh)")
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="merged/closed PR limit"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="also write the generated block to this file",
    )
    args = parser.parse_args(argv)
    if args.write and args.dry_run:
        parser.error("--write and --dry-run are mutually exclusive")

    try:
        state = collect_state(args.repo, gh=args.gh, limit=args.limit)
    except (RuntimeError, json.JSONDecodeError, KeyError, ValueError) as error:
        print(
            f"sync_coordinator_issue: failed to collect GitHub state: {error}",
            file=sys.stderr,
        )
        return 1

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    block = render_block(state, generated_at=generated_at)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(block, encoding="utf-8")

    try:
        body = run_gh(
            args.gh,
            args.repo,
            ["issue", "view", str(args.issue_number), "--json", "body", "--jq", ".body"],
        )
        new_body, changed = replace_block(body, block)
    except (RuntimeError, ValueError) as error:
        print(f"sync_coordinator_issue: {error}", file=sys.stderr)
        return 1

    if not changed:
        print("no change: generated section is up to date")
        return 0
    if not args.write:
        print("dry-run: would update the generated section:")
        print(block, end="")
        return 0
    write_issue_body(args.gh, args.repo, args.issue_number, new_body)
    print(f"updated {args.repo}#{args.issue_number}")
    return 0


if __name__ == "__main__":
    # The generated block contains non-ASCII punctuation; on Windows the
    # console default (cp932) cannot encode it. Force UTF-8 when possible.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    raise SystemExit(main())
