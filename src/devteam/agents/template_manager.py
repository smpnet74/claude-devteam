"""Manages copying agent templates from the package to user directories.

Used by ``devteam init`` (copies to ~/.devteam/agents/) and
``devteam project add`` (copies to project's .claude/agents/).
"""

from __future__ import annotations

import shutil
from pathlib import Path


def get_bundled_templates_dir() -> Path:
    """Return the path to the bundled agent template .md files.

    These are shipped with the devteam package under
    src/devteam/templates/agents/.
    """
    return Path(__file__).parent.parent / "templates" / "agents"


def copy_agent_templates(
    dest_dir: Path,
    overwrite: bool = True,
) -> list[Path]:
    """Copy agent template .md files to a destination directory.

    Args:
        dest_dir: Destination directory (created if it doesn't exist).
        overwrite: If True, overwrite existing files. If False, skip
                   files that already exist (preserving customizations).

    Returns:
        List of paths to copied files.
    """
    source_dir = get_bundled_templates_dir()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for src_file in sorted(source_dir.glob("*.md")):
        if src_file.is_symlink():
            continue
        dest_file = dest_dir / src_file.name
        if dest_file.exists() and not overwrite:
            continue
        shutil.copy2(src_file, dest_file)
        copied.append(dest_file)

    return copied


def copy_agents_to_project(
    global_agents_dir: Path,
    project_dir: Path,
    overwrite: bool = True,
) -> list[Path]:
    """Copy agent definitions from ~/.devteam/agents/ to a project's .claude/agents/.

    Args:
        global_agents_dir: Path to ~/.devteam/agents/ (source).
        project_dir: Path to the project root (must exist).
        overwrite: If True, overwrite existing files.

    Returns:
        List of paths to copied files.

    Raises:
        FileNotFoundError: If project_dir does not exist.
    """
    global_agents_dir = Path(global_agents_dir)
    if not global_agents_dir.exists():
        raise FileNotFoundError(f"Global agents directory not found: {global_agents_dir}")

    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Project directory not found: {project_dir}")

    dest_dir = project_dir / ".claude" / "agents"
    dest_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for src_file in sorted(global_agents_dir.glob("*.md")):
        if src_file.is_symlink():
            continue
        dest_file = dest_dir / src_file.name
        if dest_file.exists() and not overwrite:
            continue
        shutil.copy2(src_file, dest_file)
        copied.append(dest_file)

    return copied
