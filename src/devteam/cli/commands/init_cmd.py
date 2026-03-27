"""devteam init — first-time setup."""

from __future__ import annotations

from pathlib import Path

import typer

from devteam.agents.template_manager import copy_agent_templates
from devteam.cli.common import get_devteam_home

app = typer.Typer(help="Initialize devteam.")


DEFAULT_CONFIG = """\
# claude-devteam global configuration
# See: https://github.com/smpnet74/claude-devteam

[daemon]
port = 7432

[general]
max_concurrent_agents = 3

[models]
executive = "opus"
engineering = "sonnet"
validation = "haiku"
extraction = "haiku"

[approval]
commit = "auto"
push = "auto"
open_pr = "auto"
merge = "auto"
cleanup = "auto"
push_to_main = "never"

[knowledge]
embedding_model = "nomic-embed-text"
ollama_url = "http://localhost:11434"
surrealdb_url = "ws://localhost:8000"
surrealdb_username = "root"
surrealdb_password = "root"
cross_project_sharing = "layered"

[rate_limit]
default_backoff_seconds = 1800

[pr]
max_fix_iterations = 5
ci_poll_interval_seconds = 60

[git]
worktree_dir = ".worktrees"
"""

DIRS = ["logs", "traces", "exports", "focus", "agents", "projects", "knowledge"]


def init_devteam_home(home: Path) -> bool:
    """Create the ~/.devteam directory structure.

    Returns True if newly created, False if already existed.
    """
    created = not home.exists()
    home.mkdir(parents=True, exist_ok=True)

    for d in DIRS:
        (home / d).mkdir(exist_ok=True)

    config_path = home / "config.toml"
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG)

    # Copy bundled agent templates to ~/.devteam/agents/ (skip existing customizations)
    agents_dir = home / "agents"
    copy_agent_templates(agents_dir, overwrite=False)

    return created


@app.callback(invoke_without_command=True)
def init() -> None:
    """Initialize devteam — creates ~/.devteam/ and default configuration."""
    home = get_devteam_home()
    try:
        created = init_devteam_home(home)
    except OSError as e:
        typer.echo(f"Error: failed to initialize devteam at {home}: {e}")
        raise typer.Exit(code=1)
    if created:
        typer.echo(f"Initialized devteam at {home}")
    else:
        typer.echo(f"Already initialized at {home}")
