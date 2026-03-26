"""Agent registry — parses .md frontmatter at startup, provides lookup by role.

Agent .md files are the single source of truth for model, prompt, and tool access.
The registry parses them once at daemon startup and provides an in-memory lookup
used by the invoker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# Regex to extract YAML frontmatter delimited by --- lines
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n(.*)",
    re.DOTALL,
)


@dataclass(frozen=True)
class AgentDefinition:
    """Parsed agent definition from a .md file."""

    role: str
    model: str
    tools: list[str]
    prompt: str

    @classmethod
    def from_markdown(cls, content: str, role: str) -> AgentDefinition:
        """Parse an agent .md file's content into an AgentDefinition.

        Args:
            content: Full text content of the .md file.
            role: Role slug derived from the filename (e.g., "backend_engineer").

        Returns:
            AgentDefinition with model, tools, and prompt extracted.

        Raises:
            ValueError: If frontmatter is missing or invalid.
        """
        match = _FRONTMATTER_RE.match(content)
        if not match:
            raise ValueError(
                f"Agent '{role}': missing YAML frontmatter (must start with --- delimiters)"
            )

        frontmatter_text, prompt = match.groups()

        try:
            frontmatter = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError as e:
            raise ValueError(f"Agent '{role}': invalid YAML frontmatter: {e}") from e

        if not isinstance(frontmatter, dict):
            raise ValueError(f"Agent '{role}': frontmatter must be a YAML mapping")

        if "model" not in frontmatter:
            raise ValueError(f"Agent '{role}': frontmatter must include 'model' field")

        model = frontmatter["model"]
        tools = frontmatter.get("tools", [])

        if not isinstance(tools, list):
            raise ValueError(f"Agent '{role}': 'tools' must be a list")

        return cls(
            role=role,
            model=str(model),
            tools=[str(t) for t in tools],
            prompt=prompt.strip(),
        )


class AgentRegistry:
    """In-memory registry of parsed agent definitions, keyed by role slug.

    Loaded once at daemon startup from a directory of .md files. Provides
    fast lookup of model, tools, and prompt for any role.
    """

    def __init__(self, agents: dict[str, AgentDefinition]) -> None:
        self._agents = agents

    @classmethod
    def load(cls, agents_dir: Path) -> AgentRegistry:
        """Parse all .md files in agents_dir and build the registry.

        Args:
            agents_dir: Path to directory containing agent .md files.

        Returns:
            AgentRegistry with all parsed agents.

        Raises:
            FileNotFoundError: If agents_dir does not exist.
        """
        agents_dir = Path(agents_dir)
        if not agents_dir.is_dir():
            raise FileNotFoundError(f"Agents directory not found: {agents_dir}")

        agents: dict[str, AgentDefinition] = {}
        for md_file in sorted(agents_dir.glob("*.md")):
            role = md_file.stem  # filename without extension
            content = md_file.read_text(encoding="utf-8")
            defn = AgentDefinition.from_markdown(content, role)
            agents[role] = defn

        return cls(agents)

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, role: str) -> bool:
        return role in self._agents

    def get(self, role: str) -> AgentDefinition:
        """Get agent definition by role slug.

        Raises:
            KeyError: If role is not in the registry.
        """
        if role not in self._agents:
            raise KeyError(f"Unknown agent role: '{role}'")
        return self._agents[role]

    def get_tools(self, role: str) -> list[str]:
        """Get the tool list for a role."""
        return self.get(role).tools

    def get_model(self, role: str) -> str:
        """Get the model for a role."""
        return self.get(role).model

    def list_roles(self) -> list[str]:
        """Return all registered role slugs."""
        return list(self._agents.keys())
