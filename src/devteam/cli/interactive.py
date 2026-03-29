"""Interactive terminal session for monitoring and controlling devteam jobs.

Provides command parsing and dispatch for the interactive prompt_toolkit UI.
The full split-pane TUI is built on top of this command layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedCommand:
    """A parsed interactive command."""

    name: str
    args: list[str]
    raw: str


# Supported commands and their minimum argument counts
COMMANDS: dict[str, int] = {
    "answer": 2,  # /answer Q-1 Use JWT
    "comment": 2,  # /comment T-3 some feedback
    "pause": 0,  # /pause
    "resume": 0,  # /resume
    "cancel": 0,  # /cancel
    "status": 0,  # /status
    "verbose": 1,  # /verbose T-1
    "quiet": 1,  # /quiet T-1
    "priority": 2,  # /priority T-3 high
    "help": 0,  # /help
}


def parse_command(raw: str) -> ParsedCommand | None:
    """Parse a slash command from user input.

    Returns None if the input is not a valid command.
    Commands start with '/' followed by the command name.

    Args:
        raw: Raw user input string.

    Returns:
        ParsedCommand if valid, None if not a command.
    """
    stripped = raw.strip()
    if not stripped.startswith("/"):
        return None

    parts = stripped[1:].split(None, 1)  # Split on first whitespace
    if not parts:
        return None

    name = parts[0].lower()
    if name not in COMMANDS:
        return None

    # Parse args: for commands with text (answer, comment), keep everything after
    # the first required args as one string
    if len(parts) > 1:
        remaining = parts[1]
        min_args = COMMANDS[name]

        if min_args >= 2:
            # First token is the target, rest is the text
            arg_parts = remaining.split(None, 1)
            args = arg_parts
        elif min_args == 1:
            args = [remaining.split()[0]]
        else:
            args = remaining.split() if remaining else []
    else:
        args = []

    return ParsedCommand(name=name, args=args, raw=stripped)


def format_help() -> str:
    """Return help text for interactive commands."""
    return """Interactive Commands:
  /answer Q-1 <text>     Answer a pending question
  /comment T-3 <text>    Inject feedback into a running task
  /pause                 Pause all running tasks
  /resume                Resume paused tasks
  /cancel                Cancel the current job
  /status                Show job and task status
  /verbose T-1           Show detailed output for a task
  /quiet T-1             Reduce output for a task
  /priority T-3 high     Change task priority (high/normal/low)
  /help                  Show this help"""


def validate_command(cmd: ParsedCommand) -> str | None:
    """Validate a parsed command has enough arguments.

    Returns an error message if invalid, None if valid.
    """
    min_args = COMMANDS.get(cmd.name, 0)
    if len(cmd.args) < min_args:
        if cmd.name == "answer":
            return "Usage: /answer <question-ref> <your answer>"
        if cmd.name == "comment":
            return "Usage: /comment <task-ref> <feedback text>"
        if cmd.name in ("verbose", "quiet"):
            return f"Usage: /{cmd.name} <task-ref>"
        if cmd.name == "priority":
            return "Usage: /priority <task-ref> <high|normal|low>"
        return f"/{cmd.name} requires at least {min_args} argument(s)"
    return None
