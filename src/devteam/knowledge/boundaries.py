"""Knowledge boundaries -- sharing rules, secret scanning, scope filtering."""

from __future__ import annotations

import enum
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class SharingScope(str, enum.Enum):
    """Knowledge sharing scope."""

    SHARED = "shared"
    PROJECT = "project"


class SecretDetectedError(Exception):
    """Raised when a knowledge entry contains a likely secret."""


# Patterns that indicate secrets/credentials
SECRET_PATTERNS = [
    # AWS access key IDs
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    # Generic API keys assigned as string values
    (
        re.compile(
            r"""(?:api[_-]?key|apikey|secret[_-]?key)\s*[=:]\s*["'][^$<{][^"']{8,}["']""",
            re.IGNORECASE,
        ),
        "API key assignment",
    ),
    # Password assignments
    (
        re.compile(
            r"""(?:password|passwd|pwd)\s*[=:]\s*["'][^$<{][^"']{4,}["']""",
            re.IGNORECASE,
        ),
        "Password assignment",
    ),
    # Bearer tokens (JWT-like)
    (re.compile(r"Bearer\s+eyJ[A-Za-z0-9_.\-]{20,}"), "Bearer token"),
    # Private key blocks
    (
        re.compile(r"-----BEGIN\s+(RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        "Private key",
    ),
    # Connection strings with embedded passwords
    (
        re.compile(r"(?:postgres|mysql|mongodb|redis)://[^:]+:[^@$<{]+@"),
        "Connection string with password",
    ),
    # GitHub personal access tokens
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "GitHub token"),
    # Generic secret/token long hex strings
    (
        re.compile(
            r"""(?:secret|token)\s*[=:]\s*["'][0-9a-f]{32,}["']""",
            re.IGNORECASE,
        ),
        "Secret/token hex string",
    ),
]


def determine_sharing_scope(tags: list[str], content: str) -> SharingScope:
    """Determine sharing scope based on tags.

    Rules:
    - "shared" or "process" tag -> SHARED (cross-project)
    - "project" tag -> PROJECT (project-scoped)
    - No relevant tags -> PROJECT (conservative default)
    """
    tag_set = set(tags)

    if "shared" in tag_set or "process" in tag_set:
        return SharingScope.SHARED

    if "project" in tag_set:
        return SharingScope.PROJECT

    # Conservative default: project-scoped
    return SharingScope.PROJECT


def scan_for_secrets(content: str) -> None:
    """Scan content for likely secrets. Raises SecretDetectedError if found.

    Allows placeholder patterns like ${VAR}, <your-key-here>, $ENV_VAR.
    """
    for pattern, description in SECRET_PATTERNS:
        match = pattern.search(content)
        if match:
            matched_text = match.group(0)
            # Skip placeholder patterns -- but not literal $ in passwords
            if any(p in matched_text for p in ("${", "<")) or re.search(
                r"\$[A-Z_]", matched_text
            ):
                continue
            raise SecretDetectedError(
                f"Potential {description} detected in knowledge content. "
                f"Entry rejected to prevent secret leakage."
            )


def apply_scope_filter(
    scope: str,
    project: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Build a filter dict for knowledge queries based on scope.

    Args:
        scope: One of "shared", "project", "my_role", "all".
        project: Current project name (for project/all scopes).
        role: Current agent role (for my_role/all scopes).

    Returns:
        Dict with filter keys: sharing, project, role as applicable.
    """
    filters: dict[str, Any] = {}

    if scope == "shared":
        filters["sharing"] = "shared"
    elif scope == "project":
        if project:
            filters["project"] = project
    elif scope == "my_role":
        if role:
            filters["role"] = role
    elif scope == "all":
        if project:
            filters["project"] = project
        if role:
            filters["role"] = role

    return filters
