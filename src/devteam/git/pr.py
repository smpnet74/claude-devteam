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

_ALLOWED_MERGE_STRATEGIES = frozenset({"squash", "merge", "rebase"})


@dataclass
class PRInfo:
    """Basic information about a pull request."""

    number: int
    url: str
    branch: str
    title: str = ""
    state: str = ""
    base_branch: str = ""


class PRCheckStatus(Enum):
    """Aggregate status of CI checks on a PR."""

    ALL_PASSED = "all_passed"
    SOME_FAILED = "some_failed"
    PENDING = "pending"
    NO_CHECKS = "no_checks"


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
    api_errors: list[str] = field(default_factory=list)


def find_existing_pr(
    cwd: Path,
    branch: str,
    repo: str | None = None,
    expected_owner: str | None = None,
) -> PRInfo | None:
    """Check if a PR already exists for the given branch.

    Idempotent recovery: before creating a new PR, check if one exists.

    Args:
        cwd: Working directory (must be in a git repo).
        branch: Head branch name.
        repo: Optional upstream repo in 'owner/name' format.
        expected_owner: Optional fork owner to filter by in cross-fork scenarios.
            When set, only PRs from this owner's fork are considered.

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
        "number,url,headRefName,state,headRepositoryOwner",
    ]
    if repo:
        args.extend(["--repo", repo])

    try:
        prs = cast(list[dict[str, Any]], gh_run(args, cwd=cwd, parse_json=True))
    except GhError as e:
        # 404 / "no repository" → no PR found. Other errors propagate.
        if "404" in e.stderr or "not found" in e.stderr.lower():
            return None
        raise

    if not prs:
        return None

    # Filter by expected owner if specified (cross-fork disambiguation)
    if expected_owner:
        prs = [
            p for p in prs if p.get("headRepositoryOwner", {}).get("login", "") == expected_owner
        ]
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

    # gh pr create prints the PR URL on stdout (not JSON)
    url = gh_run(args, cwd=cwd)
    if not isinstance(url, str):
        raise TypeError(f"Expected URL string from gh pr create, got {type(url).__name__}")
    url_str = url.strip()

    # Extract PR number from URL (e.g., https://github.com/owner/repo/pull/42)
    try:
        pr_number = int(url_str.rstrip("/").split("/")[-1])
    except (ValueError, IndexError) as e:
        raise GhError(args, 0, f"Could not parse PR number from URL: {url_str}") from e

    # Fetch full PR info for consistency
    fetched = find_existing_pr(cwd, branch, repo=upstream_repo)
    if fetched is not None:
        return fetched

    return PRInfo(
        number=pr_number,
        url=url_str,
        branch=branch,
        title=title,
        state="OPEN",
        base_branch=base,
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
    api_errors: list[str] = []

    # Get CI check status
    # gh pr checks supports fields: name, state, bucket, workflow (not conclusion)
    # NOTE: gh pr checks returns non-zero exit for pending (exit 8) and failed checks.
    # Use check=False to parse JSON regardless of exit code.
    try:
        checks_result = gh_run(
            ["pr", "checks", str(pr_number), "--json", "name,state,bucket"],
            cwd=cwd,
            check=False,
            parse_json=True,
        )
        checks: list[dict[str, Any]] = (
            cast(list[dict[str, Any]], checks_result) if isinstance(checks_result, list) else []
        )
    except GhError as e:
        # Real transport/auth/schema failure — not just non-zero exit from pending/failed
        checks = []
        api_errors.append(f"Failed to fetch CI checks: {e.stderr}")

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
    except GhError as e:
        review_data = {"reviews": [], "comments": [], "reviewDecision": ""}
        api_errors.append(f"Failed to fetch review data: {e.stderr}")

    # Analyze CI checks using bucket values:
    # "pass" -> passed, "fail" -> failed, "pending" -> pending, "cancel" -> failed
    if not checks:
        ci_complete = True
        check_status = PRCheckStatus.NO_CHECKS
        failed_checks: list[str] = []
    else:
        has_pending = False
        has_failed = False
        failed_checks = []

        for check in checks:
            bucket = check.get("bucket", "")
            if bucket == "fail" or bucket == "cancel":
                has_failed = True
                failed_checks.append(check["name"])
            elif bucket == "pending":
                has_pending = True
            elif bucket == "skipping":
                # Skipped checks are non-blocking (e.g., path-filtered CI)
                pass
            # bucket == "pass" is the implicit success case

        ci_complete = not has_pending

        if has_failed:
            check_status = PRCheckStatus.SOME_FAILED
        elif has_pending:
            check_status = PRCheckStatus.PENDING
        else:
            check_status = PRCheckStatus.ALL_PASSED

    # Categorize CodeRabbit comments
    all_comments = review_data.get("comments", [])
    coderabbit = categorize_coderabbit_comments(all_comments)

    review_decision = review_data.get("reviewDecision", "")
    # Deny-list approach: treat unknown/new review states as non-blocking
    # rather than requiring an explicit allow-list that could miss new states.
    all_green = (
        check_status in (PRCheckStatus.ALL_PASSED, PRCheckStatus.NO_CHECKS)
        and not coderabbit.errors
        and review_decision not in ("CHANGES_REQUESTED", "REVIEW_REQUIRED")
        and not api_errors
    )

    return PRFeedback(
        ci_complete=ci_complete,
        check_status=check_status,
        all_green=all_green,
        failed_checks=failed_checks,
        review_comments=review_data.get("reviews", []),
        review_decision=review_decision,
        coderabbit_comments=coderabbit,
        api_errors=api_errors,
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
    if strategy not in _ALLOWED_MERGE_STRATEGIES:
        raise ValueError(
            f"Invalid merge strategy {strategy!r}; "
            f"must be one of {sorted(_ALLOWED_MERGE_STRATEGIES)}"
        )
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
            # Non-fatal: comment posting can fail due to rate limits, auth issues, etc.
            # TODO: Add logging when logger is available.
            pass

    try:
        gh_run(["pr", "close", str(pr_number)], cwd=cwd)
    except GhError as e:
        if "already closed" in e.stderr.lower() or "already merged" in e.stderr.lower():
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
