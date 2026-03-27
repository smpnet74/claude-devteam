"""PR creation, status checking, merge, and feedback handling.

All operations are idempotent: creating a PR that already exists returns
the existing one, merging an already-merged PR is a no-op, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, cast

from devteam.git.helpers import GhError, gh_run


@dataclass
class PRInfo:
    """Basic information about a pull request."""

    number: int
    url: str
    branch: str


class PRCheckStatus(Enum):
    """Aggregate status of CI checks on a PR."""

    ALL_PASSED = "all_passed"
    SOME_FAILED = "some_failed"
    PENDING = "pending"
    NO_CHECKS = "no_checks"


class CodeRabbitCategory(Enum):
    """Severity category for CodeRabbit comments."""

    ERROR = "error"
    WARNING = "warning"
    NITPICK = "nitpick"
    OTHER = "other"


@dataclass
class CategorizedComments:
    """CodeRabbit comments sorted by severity."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    nitpicks: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)


@dataclass
class PRFeedback:
    """Result of checking PR status -- CI, reviews, comments."""

    ci_complete: bool
    check_status: PRCheckStatus
    all_green: bool
    failed_checks: list[str] = field(default_factory=list)
    review_comments: list[dict[str, Any]] = field(default_factory=list)
    review_decision: str = ""
    coderabbit_comments: CategorizedComments = field(default_factory=CategorizedComments)


def find_existing_pr(
    cwd: Path,
    branch: str,
    repo: str | None = None,
) -> PRInfo | None:
    """Check if a PR already exists for the given branch.

    Idempotent recovery: before creating a new PR, check if one exists.

    Args:
        cwd: Working directory (must be in a git repo).
        branch: Head branch name.
        repo: Optional upstream repo in 'owner/name' format.

    Returns:
        PRInfo if a PR exists, None otherwise.
    """
    args = [
        "pr",
        "list",
        "--head",
        branch,
        "--state",
        "open",
        "--json",
        "number,url,headRefName,state",
    ]
    if repo:
        args.extend(["--repo", repo])

    try:
        prs = cast(list[dict[str, Any]], gh_run(args, cwd=cwd, parse_json=True))
    except GhError:
        return None

    if not prs:
        return None

    pr = prs[0]
    return PRInfo(
        number=pr["number"],
        url=pr["url"],
        branch=pr["headRefName"],
    )


def create_pr(
    cwd: Path,
    title: str,
    body: str,
    branch: str,
    base: str = "main",
    upstream_repo: str | None = None,
) -> PRInfo:
    """Create a pull request via gh CLI.

    Idempotent: if a PR already exists for the branch, returns it.

    Args:
        cwd: Working directory (must be in a git repo).
        title: PR title.
        body: PR body/description.
        branch: Head branch name.
        base: Base branch to merge into.
        upstream_repo: If working from a fork, the upstream 'owner/name'.

    Returns:
        PRInfo with number and URL.
    """
    # Idempotency: check if PR already exists
    existing = find_existing_pr(cwd, branch, repo=upstream_repo)
    if existing is not None:
        return existing

    args = [
        "pr",
        "create",
        "--title",
        title,
        "--body",
        body,
        "--head",
        branch,
        "--base",
        base,
    ]
    if upstream_repo:
        args.extend(["--repo", upstream_repo])

    result = cast(dict[str, Any], gh_run(args, cwd=cwd, parse_json=True))
    return PRInfo(
        number=result["number"],
        url=result["url"],
        branch=result["headRefName"],
    )


def check_pr_status(cwd: Path, pr_number: int) -> PRFeedback:
    """Check CI checks and review status for a PR.

    This is a single-shot check (no polling loop). The workflow
    layer calls this repeatedly with DBOS.sleep() between calls.

    Args:
        cwd: Working directory (must be in a git repo).
        pr_number: PR number to check.

    Returns:
        PRFeedback with CI status, review comments, and categorized
        CodeRabbit comments.
    """
    # Get CI check status
    try:
        checks: list[dict[str, Any]] = cast(
            list[dict[str, Any]],
            gh_run(
                ["pr", "checks", str(pr_number), "--json", "name,state,conclusion"],
                cwd=cwd,
                parse_json=True,
            ),
        )
    except GhError:
        checks = []

    # Get review status
    try:
        review_data: dict[str, Any] = cast(
            dict[str, Any],
            gh_run(
                [
                    "pr",
                    "view",
                    str(pr_number),
                    "--json",
                    "reviews,comments,reviewDecision",
                ],
                cwd=cwd,
                parse_json=True,
            ),
        )
    except GhError:
        review_data = {"reviews": [], "comments": [], "reviewDecision": ""}

    # Analyze CI checks
    if not checks:
        ci_complete = True
        check_status = PRCheckStatus.NO_CHECKS
        failed_checks: list[str] = []
    else:
        all_completed = all(c.get("state") == "completed" for c in checks)
        ci_complete = all_completed
        failed = [c["name"] for c in checks if c.get("conclusion") == "failure"]
        failed_checks = failed

        if not all_completed:
            check_status = PRCheckStatus.PENDING
        elif failed:
            check_status = PRCheckStatus.SOME_FAILED
        else:
            check_status = PRCheckStatus.ALL_PASSED

    # Categorize CodeRabbit comments
    all_comments = review_data.get("comments", [])
    coderabbit = categorize_coderabbit_comments(all_comments)

    review_decision = review_data.get("reviewDecision", "")
    all_green = (
        check_status in (PRCheckStatus.ALL_PASSED, PRCheckStatus.NO_CHECKS)
        and not coderabbit.errors
        and review_decision in ("APPROVED", "")
    )

    return PRFeedback(
        ci_complete=ci_complete,
        check_status=check_status,
        all_green=all_green,
        failed_checks=failed_checks,
        review_comments=review_data.get("reviews", []),
        review_decision=review_decision,
        coderabbit_comments=coderabbit,
    )


def merge_pr(
    cwd: Path,
    pr_number: int,
    strategy: str = "squash",
) -> None:
    """Merge a PR via gh CLI.

    Idempotent: if the PR is already merged, this is a no-op.

    Args:
        cwd: Working directory.
        pr_number: PR number to merge.
        strategy: Merge strategy -- 'squash', 'merge', or 'rebase'.
    """
    strategy_flag = f"--{strategy}"
    try:
        gh_run(
            [
                "pr",
                "merge",
                str(pr_number),
                strategy_flag,
                "--auto",
                "--delete-branch",
            ],
            cwd=cwd,
        )
    except GhError as e:
        if "already been merged" in e.stderr:
            return
        raise


def close_pr(
    cwd: Path,
    pr_number: int,
    comment: str | None = None,
) -> None:
    """Close a PR without merging.

    Idempotent: closing an already-closed PR is a no-op.

    Args:
        cwd: Working directory.
        pr_number: PR number to close.
        comment: Optional comment to post before closing.
    """
    if comment:
        try:
            gh_run(
                ["pr", "comment", str(pr_number), "--body", comment],
                cwd=cwd,
            )
        except GhError:
            pass  # Comment failure is non-fatal

    try:
        gh_run(["pr", "close", str(pr_number)], cwd=cwd)
    except GhError as e:
        if "already closed" in e.stderr:
            return
        raise


def categorize_coderabbit_comments(
    comments: list[dict[str, Any]],
) -> CategorizedComments:
    """Categorize CodeRabbit comments by severity.

    Errors first, warnings second, nitpicks deprioritized.

    Args:
        comments: List of comment dicts with 'body' and 'author' keys.

    Returns:
        CategorizedComments with sorted lists.
    """
    result = CategorizedComments()

    for comment in comments:
        author = comment.get("author", "")
        # Handle author as string or dict
        if isinstance(author, dict):
            author = author.get("login", "")
        if "coderabbit" not in author.lower():
            continue

        body = comment.get("body", "")
        body_lower = body.lower()

        if body_lower.startswith("[error]") or "[error]" in body_lower:
            result.errors.append(body)
        elif body_lower.startswith("[warning]") or "[warning]" in body_lower:
            result.warnings.append(body)
        elif body_lower.startswith("[nitpick]") or "[nitpick]" in body_lower:
            result.nitpicks.append(body)
        else:
            result.other.append(body)

    return result
