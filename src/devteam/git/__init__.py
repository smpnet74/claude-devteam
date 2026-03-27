"""Git lifecycle management -- worktrees, branches, PRs, forks, cleanup.

All operations follow the idempotent recovery pattern: before any
side-effecting step, check if the effect already happened.
"""

from devteam.git.worktree import (
    WorktreeInfo,
    create_worktree,
    remove_worktree,
    list_worktrees,
    worktree_exists,
)
from devteam.git.branch import (
    create_feature_branch,
    delete_local_branch,
    delete_remote_branch,
    branch_exists,
    remote_branch_exists,
)
from devteam.git.helpers import (
    GitError,
    GhError,
    git_run,
    gh_run,
    get_repo_root,
    get_current_branch,
    get_default_branch,
)
from devteam.git.pr import (
    PRInfo,
    PRCheckStatus,
    PRFeedback,
    create_pr,
    check_pr_status,
    merge_pr,
    close_pr,
    find_existing_pr,
)
from devteam.git.fork import (
    ForkStatus,
    ForkResult,
    check_push_access,
    find_existing_fork,
    ensure_fork,
    setup_fork_remotes,
    detect_fork_strategy,
)
from devteam.git.cleanup import (
    CleanupResult,
    CleanupAction,
    cleanup_after_merge,
    cleanup_on_cancel,
    cleanup_single_pr,
)
from devteam.git.recovery import (
    RecoveryCheck,
    check_worktree_state,
    check_branch_pushed,
    check_pr_exists,
    check_pr_merged,
    reset_worktree_to_clean,
    check_same_repo_concurrency,
)

__all__ = [
    # Worktree
    "WorktreeInfo",
    "create_worktree",
    "remove_worktree",
    "list_worktrees",
    "worktree_exists",
    # Branch
    "create_feature_branch",
    "delete_local_branch",
    "delete_remote_branch",
    "branch_exists",
    "remote_branch_exists",
    # Helpers
    "GitError",
    "GhError",
    "git_run",
    "gh_run",
    "get_repo_root",
    "get_current_branch",
    "get_default_branch",
    # PR
    "PRInfo",
    "PRCheckStatus",
    "PRFeedback",
    "create_pr",
    "check_pr_status",
    "merge_pr",
    "close_pr",
    "find_existing_pr",
    # Fork
    "ForkStatus",
    "ForkResult",
    "check_push_access",
    "find_existing_fork",
    "ensure_fork",
    "setup_fork_remotes",
    "detect_fork_strategy",
    # Cleanup
    "CleanupResult",
    "CleanupAction",
    "cleanup_after_merge",
    "cleanup_on_cancel",
    "cleanup_single_pr",
    # Recovery
    "RecoveryCheck",
    "check_worktree_state",
    "check_branch_pushed",
    "check_pr_exists",
    "check_pr_merged",
    "reset_worktree_to_clean",
    "check_same_repo_concurrency",
]
