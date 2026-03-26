# Plan 2: Agent Definitions & Invocation Implementation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Create the 16 agent definitions and the SDK integration layer that invokes them.

**Architecture:** Agent definitions live as `.md` files with YAML frontmatter specifying model and tools. At daemon startup, the agent registry parses all `.md` files from `~/.devteam/agents/` into an in-memory lookup keyed by role slug. The agent invoker wraps Claude Agent SDK `query()` calls, injecting the correct model, tools, working directory, and structured output schema per invocation. Structured output contracts (JSON schemas) ensure the orchestrator can machine-parse every agent response.

**Tech Stack:** Python 3.13, claude-agent-sdk==0.1.50, pyyaml, pydantic (JSON schema generation), pytest

**Note:** Structured output contracts (ImplementationResult, ReviewResult, DecompositionResult, RoutingResult) are defined in Plan 3 (`src/devteam/orchestrator/schemas.py`) as the single source of truth. This plan's invoker uses generic `dict` returns — schema validation is enforced in Plan 3's workflow layer.

---

## ~~Task 1: Create structured output contracts module~~ (REMOVED — see Plan 3, Task 1)

Structured output schemas are defined in `src/devteam/orchestrator/schemas.py` (Plan 3) to avoid duplication. The invoker in this plan returns raw dicts; the orchestrator validates them against schemas.

- [ ] **Step 0 (1 min):** Add Claude Agent SDK and PyYAML dependencies.

```bash
pixi add --pypi "claude-agent-sdk==0.1.50" "pyyaml>=6,<7"
```

- [ ] **Step 1 (2 min):** Create `src/devteam/agents/` package directory and `__init__.py`.

```bash
mkdir -p src/devteam/agents
touch src/devteam/agents/__init__.py
```

- [ ] **Step 2 (3 min):** Write test file `tests/agents/test_contracts.py`.

```bash
mkdir -p tests/agents
touch tests/agents/__init__.py
```

```python
# tests/agents/test_contracts.py
"""Tests for structured output contracts."""
import json
import pytest
from devteam.agents.contracts import (
    ImplementationResult,
    ReviewResult,
    DecompositionResult,
    RoutingResult,
    ReviewComment,
    TaskDecomposition,
)


class TestImplementationResult:
    def test_completed_result(self):
        result = ImplementationResult(
            status="completed",
            question=None,
            files_changed=["src/main.py", "src/utils.py"],
            tests_added=["tests/test_main.py"],
            summary="Implemented user authentication flow",
            confidence="high",
        )
        assert result.status == "completed"
        assert result.question is None
        assert len(result.files_changed) == 2
        assert result.confidence == "high"

    def test_needs_clarification_requires_question(self):
        result = ImplementationResult(
            status="needs_clarification",
            question="Should auth use OAuth2 or API keys?",
            files_changed=[],
            tests_added=[],
            summary="Blocked on auth strategy decision",
            confidence="low",
        )
        assert result.status == "needs_clarification"
        assert result.question is not None

    def test_blocked_result(self):
        result = ImplementationResult(
            status="blocked",
            question="Database migration failed — need DBA help",
            files_changed=[],
            tests_added=[],
            summary="Migration blocked",
            confidence="low",
        )
        assert result.status == "blocked"

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError):
            ImplementationResult(
                status="invalid_status",
                question=None,
                files_changed=[],
                tests_added=[],
                summary="test",
                confidence="high",
            )

    def test_invalid_confidence_rejected(self):
        with pytest.raises(ValueError):
            ImplementationResult(
                status="completed",
                question=None,
                files_changed=[],
                tests_added=[],
                summary="test",
                confidence="very_high",
            )

    def test_json_schema_generation(self):
        schema = ImplementationResult.model_json_schema()
        assert "properties" in schema
        assert "status" in schema["properties"]
        assert "files_changed" in schema["properties"]
        # Ensure enum constraints are present
        status_schema = schema["properties"]["status"]
        assert "enum" in status_schema or "$ref" in status_schema or "anyOf" in status_schema


class TestReviewResult:
    def test_approved_no_comments(self):
        result = ReviewResult(
            verdict="approved",
            comments=[],
            summary="Code looks good, well-structured.",
        )
        assert result.verdict == "approved"
        assert len(result.comments) == 0

    def test_needs_revision_with_comments(self):
        result = ReviewResult(
            verdict="needs_revision",
            comments=[
                ReviewComment(
                    file="src/main.py",
                    line=42,
                    severity="error",
                    comment="Missing null check on user input",
                ),
                ReviewComment(
                    file="src/main.py",
                    line=78,
                    severity="nitpick",
                    comment="Consider renaming variable for clarity",
                ),
            ],
            summary="One critical issue found.",
        )
        assert result.verdict == "needs_revision"
        assert len(result.comments) == 2
        assert result.comments[0].severity == "error"

    def test_invalid_verdict_rejected(self):
        with pytest.raises(ValueError):
            ReviewResult(
                verdict="maybe",
                comments=[],
                summary="test",
            )

    def test_invalid_severity_rejected(self):
        with pytest.raises(ValueError):
            ReviewComment(
                file="test.py",
                line=1,
                severity="critical",
                comment="test",
            )

    def test_json_schema_generation(self):
        schema = ReviewResult.model_json_schema()
        assert "properties" in schema
        assert "verdict" in schema["properties"]
        assert "comments" in schema["properties"]


class TestDecompositionResult:
    def test_simple_decomposition(self):
        result = DecompositionResult(
            tasks=[
                TaskDecomposition(
                    id="T-1",
                    description="Set up database schema",
                    assigned_to="data_engineer",
                    team="b",
                    depends_on=[],
                    pr_group="database-setup",
                ),
                TaskDecomposition(
                    id="T-2",
                    description="Build REST API endpoints",
                    assigned_to="backend_engineer",
                    team="a",
                    depends_on=["T-1"],
                    pr_group="api-endpoints",
                ),
            ],
            peer_assignments={"T-1": "infra_engineer", "T-2": "frontend_engineer"},
            parallel_groups=[["T-1"], ["T-2"]],
        )
        assert len(result.tasks) == 2
        assert result.tasks[1].depends_on == ["T-1"]
        assert result.peer_assignments["T-1"] == "infra_engineer"

    def test_invalid_team_rejected(self):
        with pytest.raises(ValueError):
            TaskDecomposition(
                id="T-1",
                description="test",
                assigned_to="backend_engineer",
                team="c",
                depends_on=[],
                pr_group="test",
            )

    def test_json_schema_generation(self):
        schema = DecompositionResult.model_json_schema()
        assert "properties" in schema
        assert "tasks" in schema["properties"]


class TestRoutingResult:
    def test_full_project_routing(self):
        result = RoutingResult(
            path="full_project",
            reasoning="Complex multi-component feature requiring architecture review",
        )
        assert result.path == "full_project"

    def test_small_fix_routing(self):
        result = RoutingResult(
            path="small_fix",
            reasoning="Single file bug fix with clear scope",
        )
        assert result.path == "small_fix"

    def test_invalid_path_rejected(self):
        with pytest.raises(ValueError):
            RoutingResult(
                path="unknown_path",
                reasoning="test",
            )

    def test_json_schema_generation(self):
        schema = RoutingResult.model_json_schema()
        assert "properties" in schema
        assert "path" in schema["properties"]


class TestSchemaForRole:
    """Test the helper that maps roles to their output schema."""

    def test_all_schemas_are_valid_json_schema(self):
        for model_cls in [ImplementationResult, ReviewResult, DecompositionResult, RoutingResult]:
            schema = model_cls.model_json_schema()
            # Verify it's valid JSON by round-tripping
            json_str = json.dumps(schema)
            parsed = json.loads(json_str)
            assert parsed == schema
```

**Test command:** `pixi run pytest tests/agents/test_contracts.py -v`

- [ ] **Step 3 (5 min):** Implement `src/devteam/agents/contracts.py`.

```python
# src/devteam/agents/contracts.py
"""Structured output contracts for agent invocations.

These Pydantic models define the JSON schemas that agents must conform to
when returning results. The orchestrator uses these to machine-parse agent
output without prose parsing.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ImplementationResult(BaseModel):
    """Result envelope for engineer implementation steps."""

    status: Literal["completed", "needs_clarification", "blocked"]
    question: str | None = Field(
        default=None,
        description="Question for supervisor if status is needs_clarification or blocked",
    )
    files_changed: list[str] = Field(
        default_factory=list,
        description="List of file paths modified during implementation",
    )
    tests_added: list[str] = Field(
        default_factory=list,
        description="List of test file paths created or modified",
    )
    summary: str = Field(description="What was built and why")
    confidence: Literal["high", "medium", "low"] = Field(
        description="Agent's confidence in the implementation quality",
    )


class ReviewComment(BaseModel):
    """A single review comment on a specific file location."""

    file: str = Field(description="Path to the file being commented on")
    line: int = Field(description="Line number of the comment")
    severity: Literal["error", "warning", "nitpick"] = Field(
        description="Severity level of the comment",
    )
    comment: str = Field(description="The review comment text")


class ReviewResult(BaseModel):
    """Result envelope for peer review and validation steps."""

    verdict: Literal[
        "approved", "approved_with_comments", "needs_revision", "blocked"
    ]
    comments: list[ReviewComment] = Field(
        default_factory=list,
        description="List of review comments with file locations",
    )
    summary: str = Field(description="Overall review summary")


class TaskDecomposition(BaseModel):
    """A single task within a decomposition result."""

    id: str = Field(description="Task ID (e.g., T-1)")
    description: str = Field(description="What the task accomplishes")
    assigned_to: str = Field(description="Agent role slug (e.g., backend_engineer)")
    team: Literal["a", "b"] = Field(description="Which team owns this task")
    depends_on: list[str] = Field(
        default_factory=list,
        description="Task IDs that must complete before this task",
    )
    pr_group: str = Field(
        description="PR group name — tasks in the same group ship as one PR",
    )


class DecompositionResult(BaseModel):
    """Result envelope for Chief Architect decomposition step."""

    tasks: list[TaskDecomposition] = Field(
        description="Ordered list of tasks with dependencies",
    )
    peer_assignments: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of task_id to peer reviewer role slug",
    )
    parallel_groups: list[list[str]] = Field(
        default_factory=list,
        description="Groups of task IDs that can execute simultaneously",
    )


class RoutingResult(BaseModel):
    """Result envelope for CEO routing decision."""

    path: Literal["full_project", "research", "small_fix", "oss_contribution"]
    reasoning: str = Field(description="Why this routing path was chosen")
```

**Test command:** `pixi run pytest tests/agents/test_contracts.py -v`

- [ ] **Step 4 (1 min):** Run tests, verify all pass.

```bash
pixi run pytest tests/agents/test_contracts.py -v
```

---

## Task 2: Create agent registry module

**File:** `src/devteam/agents/registry.py`

Parses agent `.md` files with YAML frontmatter, builds an in-memory registry keyed by role slug.

- [ ] **Step 1 (5 min):** Write test file `tests/agents/test_registry.py`.

```python
# tests/agents/test_registry.py
"""Tests for agent registry — parses .md frontmatter, builds tool/model registry."""
import os
import tempfile
from pathlib import Path

import pytest
from devteam.agents.registry import AgentDefinition, AgentRegistry


SAMPLE_AGENT_MD = """\
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Backend Engineer for the development team.

## Expertise
APIs, databases, service architecture, migrations, ORM patterns.

## Working Style
- Read existing code before proposing changes
- Follow project conventions discovered in the codebase
- Write tests alongside implementation
- Create focused, atomic commits

## Completion Protocol
When your work is complete:
1. Ensure all tests pass
2. Summarize what you built and any decisions you made
3. Flag anything you're uncertain about as a question
"""

CEO_AGENT_MD = """\
---
model: opus
tools:
  - Read
  - Glob
  - Grep
---

You are the CEO of the development team.

## Expertise
Intake, routing, orchestration. Never touches code.
"""

INVALID_FRONTMATTER_MD = """\
No frontmatter here, just text.

This should fail to parse.
"""

MISSING_MODEL_MD = """\
---
tools:
  - Read
---

Missing the model field.
"""


class TestAgentDefinition:
    def test_parse_valid_agent(self):
        defn = AgentDefinition.from_markdown(SAMPLE_AGENT_MD, "backend_engineer")
        assert defn.role == "backend_engineer"
        assert defn.model == "sonnet"
        assert "Read" in defn.tools
        assert "Bash" in defn.tools
        assert "query_knowledge" in defn.tools
        assert len(defn.tools) == 9
        assert "You are the Backend Engineer" in defn.prompt

    def test_parse_ceo_agent(self):
        defn = AgentDefinition.from_markdown(CEO_AGENT_MD, "ceo")
        assert defn.role == "ceo"
        assert defn.model == "opus"
        assert defn.tools == ["Read", "Glob", "Grep"]
        assert "Bash" not in defn.tools
        assert "You are the CEO" in defn.prompt

    def test_prompt_excludes_frontmatter(self):
        defn = AgentDefinition.from_markdown(SAMPLE_AGENT_MD, "backend_engineer")
        assert "---" not in defn.prompt
        assert "model:" not in defn.prompt
        assert "tools:" not in defn.prompt

    def test_invalid_frontmatter_raises(self):
        with pytest.raises(ValueError, match="frontmatter"):
            AgentDefinition.from_markdown(INVALID_FRONTMATTER_MD, "bad")

    def test_missing_model_raises(self):
        with pytest.raises(ValueError, match="model"):
            AgentDefinition.from_markdown(MISSING_MODEL_MD, "bad")


class TestAgentRegistry:
    def _create_agents_dir(self, tmp_path: Path) -> Path:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "backend_engineer.md").write_text(SAMPLE_AGENT_MD)
        (agents_dir / "ceo.md").write_text(CEO_AGENT_MD)
        return agents_dir

    def test_load_from_directory(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        registry = AgentRegistry.load(agents_dir)
        assert len(registry) == 2
        assert "backend_engineer" in registry
        assert "ceo" in registry

    def test_get_agent(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        registry = AgentRegistry.load(agents_dir)
        defn = registry.get("backend_engineer")
        assert defn.model == "sonnet"
        assert "Bash" in defn.tools

    def test_get_unknown_agent_raises(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        registry = AgentRegistry.load(agents_dir)
        with pytest.raises(KeyError):
            registry.get("nonexistent_agent")

    def test_get_tools(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        registry = AgentRegistry.load(agents_dir)
        tools = registry.get_tools("ceo")
        assert tools == ["Read", "Glob", "Grep"]

    def test_get_model(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        registry = AgentRegistry.load(agents_dir)
        assert registry.get_model("ceo") == "opus"
        assert registry.get_model("backend_engineer") == "sonnet"

    def test_list_roles(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        registry = AgentRegistry.load(agents_dir)
        roles = registry.list_roles()
        assert set(roles) == {"backend_engineer", "ceo"}

    def test_ignores_non_md_files(self, tmp_path):
        agents_dir = self._create_agents_dir(tmp_path)
        (agents_dir / "README.txt").write_text("Not an agent")
        (agents_dir / ".DS_Store").write_text("")
        registry = AgentRegistry.load(agents_dir)
        assert len(registry) == 2

    def test_empty_directory(self, tmp_path):
        agents_dir = tmp_path / "empty_agents"
        agents_dir.mkdir()
        registry = AgentRegistry.load(agents_dir)
        assert len(registry) == 0

    def test_directory_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            AgentRegistry.load(tmp_path / "nonexistent")
```

**Test command:** `pixi run pytest tests/agents/test_registry.py -v`

- [ ] **Step 2 (5 min):** Implement `src/devteam/agents/registry.py`.

```python
# src/devteam/agents/registry.py
"""Agent registry — parses .md frontmatter at startup, provides lookup by role.

Agent .md files are the single source of truth for model, prompt, and tool access.
The registry parses them once at daemon startup and provides an in-memory lookup
used by the invoker.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
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
```

**Test command:** `pixi run pytest tests/agents/test_registry.py -v`

- [ ] **Step 3 (1 min):** Run tests, verify all pass.

```bash
pixi run pytest tests/agents/test_registry.py -v
```

---

## Task 3: Create agent invoker module

**File:** `src/devteam/agents/invoker.py`

Wraps Claude Agent SDK `query()` calls with the correct model, tools, working directory, and structured output schema.

- [ ] **Step 1 (5 min):** Write test file `tests/agents/test_invoker.py`.

```python
# tests/agents/test_invoker.py
"""Tests for agent invoker — wraps Claude Agent SDK query() calls."""
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from devteam.agents.contracts import (
    DecompositionResult,
    ImplementationResult,
    ReviewResult,
    RoutingResult,
)
from devteam.agents.invoker import AgentInvoker, InvocationContext, InvocationError
from devteam.agents.registry import AgentDefinition, AgentRegistry


@pytest.fixture
def mock_registry():
    """Create a registry with a few test agents."""
    agents = {
        "backend_engineer": AgentDefinition(
            role="backend_engineer",
            model="sonnet",
            tools=["Read", "Edit", "Write", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "query_knowledge"],
            prompt="You are the Backend Engineer.",
        ),
        "ceo": AgentDefinition(
            role="ceo",
            model="opus",
            tools=["Read", "Glob", "Grep"],
            prompt="You are the CEO.",
        ),
        "qa_engineer": AgentDefinition(
            role="qa_engineer",
            model="haiku",
            tools=["Read", "Edit", "Write", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "query_knowledge"],
            prompt="You are the QA Engineer.",
        ),
    }
    return AgentRegistry(agents)


@pytest.fixture
def invoker(mock_registry):
    return AgentInvoker(mock_registry)


@pytest.fixture
def context(tmp_path):
    return InvocationContext(
        worktree_path=tmp_path,
        project_name="test-project",
    )


class TestInvocationContext:
    def test_creation(self, tmp_path):
        ctx = InvocationContext(
            worktree_path=tmp_path,
            project_name="my-app",
        )
        assert ctx.worktree_path == tmp_path
        assert ctx.project_name == "my-app"


class TestAgentInvoker:
    def test_schema_for_role_engineer(self, invoker):
        schema = invoker.schema_for_role("backend_engineer")
        assert schema == ImplementationResult.model_json_schema()

    def test_schema_for_role_ceo(self, invoker):
        schema = invoker.schema_for_role("ceo")
        assert schema == RoutingResult.model_json_schema()

    def test_schema_for_role_qa(self, invoker):
        schema = invoker.schema_for_role("qa_engineer")
        assert schema == ReviewResult.model_json_schema()

    def test_build_query_params_engineer(self, invoker, context):
        params = invoker.build_query_params(
            role="backend_engineer",
            task_prompt="Implement the login endpoint",
            context=context,
        )
        assert params["prompt"] == "Implement the login endpoint"
        assert params["model"] == "sonnet"
        assert params["cwd"] == str(context.worktree_path)
        assert params["allowed_tools"] == [
            "Read", "Edit", "Write", "Bash", "Glob", "Grep",
            "WebSearch", "WebFetch", "query_knowledge",
        ]
        assert params["permission_mode"] == "bypassPermissions"
        assert "json_schema" in params

    def test_build_query_params_ceo(self, invoker, context):
        params = invoker.build_query_params(
            role="ceo",
            task_prompt="Route this incoming request",
            context=context,
        )
        assert params["model"] == "opus"
        assert params["allowed_tools"] == ["Read", "Glob", "Grep"]

    def test_build_query_params_unknown_role_raises(self, invoker, context):
        with pytest.raises(KeyError):
            invoker.build_query_params(
                role="unknown_agent",
                task_prompt="test",
                context=context,
            )

    @pytest.mark.asyncio
    async def test_invoke_calls_query(self, invoker, context):
        """Test that invoke() calls the SDK query function with correct params."""
        mock_result = MagicMock()
        mock_result.result = json.dumps({
            "status": "completed",
            "question": None,
            "files_changed": ["src/auth.py"],
            "tests_added": ["tests/test_auth.py"],
            "summary": "Built auth endpoint",
            "confidence": "high",
        })

        with patch("devteam.agents.invoker.query", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = mock_result
            result = await invoker.invoke(
                role="backend_engineer",
                task_prompt="Build auth endpoint",
                context=context,
            )
            mock_query.assert_called_once()
            call_kwargs = mock_query.call_args[1]
            assert call_kwargs["model"] == "sonnet"
            assert call_kwargs["prompt"] == "Build auth endpoint"

    @pytest.mark.asyncio
    async def test_invoke_parses_implementation_result(self, invoker, context):
        """Test that invoke() parses the SDK response into the correct contract type."""
        result_data = {
            "status": "completed",
            "question": None,
            "files_changed": ["src/auth.py"],
            "tests_added": ["tests/test_auth.py"],
            "summary": "Built auth endpoint",
            "confidence": "high",
        }
        mock_result = MagicMock()
        mock_result.result = json.dumps(result_data)

        with patch("devteam.agents.invoker.query", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = mock_result
            result = await invoker.invoke(
                role="backend_engineer",
                task_prompt="Build auth endpoint",
                context=context,
            )
            assert isinstance(result, ImplementationResult)
            assert result.status == "completed"
            assert result.files_changed == ["src/auth.py"]

    @pytest.mark.asyncio
    async def test_invoke_query_failure_raises(self, invoker, context):
        """Test that SDK errors are wrapped in InvocationError."""
        with patch("devteam.agents.invoker.query", new_callable=AsyncMock) as mock_query:
            mock_query.side_effect = Exception("SDK connection failed")
            with pytest.raises(InvocationError, match="SDK connection failed"):
                await invoker.invoke(
                    role="backend_engineer",
                    task_prompt="Build auth endpoint",
                    context=context,
                )

    @pytest.mark.asyncio
    async def test_invoke_invalid_json_raises(self, invoker, context):
        """Test that unparseable agent output raises InvocationError."""
        mock_result = MagicMock()
        mock_result.result = "This is not JSON"

        with patch("devteam.agents.invoker.query", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = mock_result
            with pytest.raises(InvocationError, match="parse"):
                await invoker.invoke(
                    role="backend_engineer",
                    task_prompt="Build auth endpoint",
                    context=context,
                )
```

**Test command:** `pixi run pytest tests/agents/test_invoker.py -v`

- [ ] **Step 2 (5 min):** Implement `src/devteam/agents/invoker.py`.

```python
# src/devteam/agents/invoker.py
"""Agent invoker — wraps Claude Agent SDK query() calls.

Builds the correct invocation parameters (model, tools, working directory,
structured output schema) from the agent registry and executes the query.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from devteam.agents.contracts import (
    DecompositionResult,
    ImplementationResult,
    ReviewResult,
    RoutingResult,
)
from devteam.agents.registry import AgentRegistry

try:
    from claude_agent_sdk import query
except ImportError:
    # Allow import to succeed even without the SDK installed,
    # so tests can mock the query function.
    query = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class InvocationError(Exception):
    """Raised when an agent invocation fails."""


@dataclass(frozen=True)
class InvocationContext:
    """Runtime context for an agent invocation."""

    worktree_path: Path
    project_name: str


# Mapping from role slug patterns to their output contract.
# The orchestrator uses this to determine which JSON schema to require.
_ROLE_SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "ceo": RoutingResult,
    "chief_architect": DecompositionResult,
    "planner_researcher_a": DecompositionResult,
    "planner_researcher_b": DecompositionResult,
    "em_team_a": ReviewResult,
    "em_team_b": ReviewResult,
    "qa_engineer": ReviewResult,
    "security_engineer": ReviewResult,
    "tech_writer": ReviewResult,
}

# All engineer roles use ImplementationResult
_ENGINEER_ROLES = {
    "backend_engineer",
    "frontend_engineer",
    "devops_engineer",
    "data_engineer",
    "infra_engineer",
    "tooling_engineer",
    "cloud_engineer",
}


class AgentInvoker:
    """Invokes agents via the Claude Agent SDK with correct parameters.

    Reads model, tools, and prompt from the AgentRegistry. Determines
    the structured output schema based on the role. Wraps query() calls
    with error handling.
    """

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def schema_for_role(self, role: str) -> dict[str, Any]:
        """Return the JSON schema for a role's structured output.

        Args:
            role: Agent role slug.

        Returns:
            JSON schema dict suitable for the Agent SDK's json_schema parameter.
        """
        if role in _ROLE_SCHEMA_MAP:
            return _ROLE_SCHEMA_MAP[role].model_json_schema()
        if role in _ENGINEER_ROLES:
            return ImplementationResult.model_json_schema()
        # Default to ImplementationResult for unknown roles
        return ImplementationResult.model_json_schema()

    def _result_type_for_role(self, role: str) -> type[BaseModel]:
        """Return the Pydantic model class for a role's output."""
        if role in _ROLE_SCHEMA_MAP:
            return _ROLE_SCHEMA_MAP[role]
        return ImplementationResult

    def build_query_params(
        self,
        role: str,
        task_prompt: str,
        context: InvocationContext,
    ) -> dict[str, Any]:
        """Build the parameter dict for a Claude Agent SDK query() call.

        Args:
            role: Agent role slug (must exist in registry).
            task_prompt: The task-specific prompt to send to the agent.
            context: Runtime context (worktree path, project name).

        Returns:
            Dict of keyword arguments for query().

        Raises:
            KeyError: If role is not in the registry.
        """
        defn = self._registry.get(role)

        return {
            "prompt": task_prompt,
            "model": defn.model,
            "agent": role,
            "cwd": str(context.worktree_path),
            "allowed_tools": defn.tools,
            "permission_mode": "bypassPermissions",
            "json_schema": self.schema_for_role(role),
        }

    async def invoke(
        self,
        role: str,
        task_prompt: str,
        context: InvocationContext,
    ) -> BaseModel:
        """Invoke an agent and return the parsed structured result.

        Args:
            role: Agent role slug.
            task_prompt: The task-specific prompt.
            context: Runtime context.

        Returns:
            Parsed Pydantic model instance (ImplementationResult, ReviewResult, etc.).

        Raises:
            InvocationError: If the SDK call fails or the result cannot be parsed.
            KeyError: If the role is not in the registry.
        """
        params = self.build_query_params(role, task_prompt, context)

        logger.info("Invoking agent '%s' (model=%s) for project '%s'", role, params["model"], context.project_name)

        try:
            sdk_result = await query(**params)
        except Exception as e:
            raise InvocationError(
                f"Agent '{role}' invocation failed: {e}"
            ) from e

        # Parse the structured JSON output
        result_type = self._result_type_for_role(role)
        try:
            data = json.loads(sdk_result.result)
            return result_type.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            raise InvocationError(
                f"Failed to parse agent '{role}' output: {e}"
            ) from e
```

**Test command:** `pixi run pytest tests/agents/test_invoker.py -v`

- [ ] **Step 3 (1 min):** Run tests, verify all pass.

```bash
pixi run pytest tests/agents/test_invoker.py -v
```

---

## Task 4: Create the 16 agent definition template files

**Directory:** `src/devteam/templates/agents/`

No TDD needed for these files. Each `.md` file has YAML frontmatter (model, tools) and a prompt body defining identity, expertise, working style, and completion protocol.

### Tool sets

- **Executive tools (CEO only):** `Read`, `Glob`, `Grep`
- **Full tools (all engineers, EMs, planners, architect):** `Read`, `Edit`, `Write`, `Bash`, `Glob`, `Grep`, `WebSearch`, `WebFetch`, `query_knowledge`
- **Validation tools (QA, Security, Tech Writer):** `Read`, `Edit`, `Write`, `Bash`, `Glob`, `Grep`, `WebSearch`, `WebFetch`, `query_knowledge`

- [ ] **Step 1 (1 min):** Create the templates directory.

```bash
mkdir -p src/devteam/templates/agents
```

- [ ] **Step 2 (2 min):** Create `src/devteam/templates/agents/ceo.md`.

```markdown
---
model: opus
tools:
  - Read
  - Glob
  - Grep
---

You are the CEO of the development team.

## Expertise
Strategic intake, routing, and orchestration. You assess incoming work requests and route them to the appropriate path through the organization. You never touch code directly.

## Routing Decisions
You choose between these paths:
- **full_project** — Complex work requiring architecture review, planning, and multi-engineer execution
- **research** — Research requests that need investigation and a deliverable report
- **small_fix** — Clear-scope fixes that can go directly to an EM and engineer
- **oss_contribution** — Open-source contributions requiring project research first

## Working Style
- Read the request carefully before routing
- Consider scope, complexity, and risk when choosing a path
- For ambiguous requests, favor the more thorough path
- Flag requests that seem underspecified rather than guessing intent

## Completion Protocol
Return a routing decision with clear reasoning for why this path was chosen.
```

- [ ] **Step 3 (2 min):** Create `src/devteam/templates/agents/chief_architect.md`.

```markdown
---
model: opus
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Chief Architect of the development team.

## Expertise
System design, cross-cutting architecture, technical standards, API contracts, and decomposition of complex work into parallelizable tasks. You write design documents and architecture decision records (ADRs).

## Responsibilities
- Decompose specs and plans into concrete tasks assigned to the right specialists
- Define PR groupings and dependency ordering for parallel execution
- Assign peer reviewers based on team membership
- Flag spec ambiguities or internal inconsistencies — escalate to the human rather than proceeding with a flawed plan
- Set technical standards that all engineers follow

## Working Style
- Read the full spec and plan before decomposing
- Maximize parallelism — identify truly independent work streams
- Be explicit about task dependencies (what blocks what)
- Assign tasks to the specialist whose expertise matches best
- Keep PR groups cohesive — related changes ship together

## Completion Protocol
Return a decomposition with tasks, peer assignments, and parallel groups. Flag any spec issues as questions.
```

- [ ] **Step 4 (2 min):** Create `src/devteam/templates/agents/planner_researcher_a.md`.

```markdown
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are Planner/Researcher A for the development team.

## Expertise
Codebase analysis, requirements decomposition, technical research, and specification writing. You investigate codebases, research technologies, and produce detailed specs and plans.

## Responsibilities
- Analyze existing codebases to understand conventions, patterns, and architecture
- Research technologies, libraries, and approaches relevant to the project
- Write detailed specifications from raw ideas or issue descriptions
- Produce research reports with findings, trade-offs, and recommendations

## Working Style
- Start with broad exploration, then narrow to specifics
- Document what you find as you go — don't rely on memory
- Cite sources and evidence for recommendations
- Surface trade-offs honestly rather than advocating for one approach
- Coordinate with Planner/Researcher B to avoid duplicate work

## Completion Protocol
When your work is complete:
1. Summarize key findings and recommendations
2. Flag any areas of uncertainty or incomplete research
3. Identify follow-up questions that need answers before implementation
```

- [ ] **Step 5 (2 min):** Create `src/devteam/templates/agents/planner_researcher_b.md`.

```markdown
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are Planner/Researcher B for the development team.

## Expertise
Codebase analysis, requirements decomposition, technical research, and specification writing. You investigate codebases, research technologies, and produce detailed specs and plans.

## Responsibilities
- Analyze existing codebases to understand conventions, patterns, and architecture
- Research technologies, libraries, and approaches relevant to the project
- Write detailed specifications from raw ideas or issue descriptions
- Produce research reports with findings, trade-offs, and recommendations
- Serve as peer reviewer for Planner/Researcher A's work

## Working Style
- Start with broad exploration, then narrow to specifics
- Document what you find as you go — don't rely on memory
- Cite sources and evidence for recommendations
- Surface trade-offs honestly rather than advocating for one approach
- When peer-reviewing, focus on completeness, accuracy, and actionability

## Completion Protocol
When your work is complete:
1. Summarize key findings and recommendations
2. Flag any areas of uncertainty or incomplete research
3. Identify follow-up questions that need answers before implementation
```

- [ ] **Step 6 (2 min):** Create `src/devteam/templates/agents/em_team_a.md`.

```markdown
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Engineering Manager for Team A (Application Layer).

## Expertise
Delivery management, quality gates, coordination, and technical judgment for application-layer work. Your team includes Backend, Frontend, and DevOps engineers.

## Responsibilities
- Review engineer work for quality, completeness, and adherence to the spec
- Coordinate task handoffs within Team A
- Resolve technical questions within your authority
- Escalate architecture questions to the Chief Architect
- Escalate routing/policy questions to the CEO
- Ensure engineers follow project conventions and standards

## Team A Engineers
- **Backend Engineer** — APIs, services, server-side logic
- **Frontend Engineer** — UI, components, state management, accessibility
- **DevOps Engineer** — CI/CD pipelines, containerization, IaC, monitoring

## Working Style
- Judge work against the spec, not personal preference
- Give actionable feedback with specific file/line references
- Approve when the work meets requirements, even if you'd do it differently
- Block only for real issues — correctness, security, missing tests

## Completion Protocol
Return a review verdict with specific comments if revisions are needed.
```

- [ ] **Step 7 (2 min):** Create `src/devteam/templates/agents/em_team_b.md`.

```markdown
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Engineering Manager for Team B (Systems Layer).

## Expertise
Delivery management, quality gates, coordination, and technical judgment for systems-layer work. Your team includes Data, Infra, Tooling/CLI, and Cloud engineers.

## Responsibilities
- Review engineer work for quality, completeness, and adherence to the spec
- Coordinate task handoffs within Team B
- Absorb overflow work and handle projects outside traditional app development
- Resolve technical questions within your authority
- Escalate architecture questions to the Chief Architect
- Escalate routing/policy questions to the CEO

## Team B Engineers
- **Data Engineer** — Database design, migrations, schemas, query optimization, ETL
- **Infra Engineer** — Performance, scaling, complex refactoring
- **Tooling/CLI Engineer** — CLIs, SDKs, build tools, developer experience
- **Cloud/Platform Engineer** — Platform-specific deployment (AWS, GCP, Fly.io, Railway, etc.)

## Working Style
- Judge work against the spec, not personal preference
- Give actionable feedback with specific file/line references
- Approve when the work meets requirements, even if you'd do it differently
- Block only for real issues — correctness, security, missing tests

## Completion Protocol
Return a review verdict with specific comments if revisions are needed.
```

- [ ] **Step 8 (2 min):** Create `src/devteam/templates/agents/backend_engineer.md`.

```markdown
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Backend Engineer for the development team.

## Expertise
APIs, databases, service architecture, migrations, ORM patterns, authentication, authorization, and server-side business logic.

## Working Style
- Read existing code before proposing changes
- Follow project conventions discovered in the codebase
- Write tests alongside implementation
- Create focused, atomic commits
- Handle error cases explicitly — no silent failures
- Design APIs with clear contracts and consistent patterns

## Completion Protocol
When your work is complete:
1. Ensure all tests pass
2. Summarize what you built and any decisions you made
3. Flag anything you're uncertain about as a question
```

- [ ] **Step 9 (2 min):** Create `src/devteam/templates/agents/frontend_engineer.md`.

```markdown
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Frontend Engineer for the development team.

## Expertise
UI components, state management, accessibility (WCAG), responsive design, client-side routing, form handling, and frontend build systems.

## Working Style
- Read existing code before proposing changes
- Follow project conventions discovered in the codebase
- Write tests alongside implementation (unit + component tests)
- Create focused, atomic commits
- Ensure accessibility — semantic HTML, ARIA labels, keyboard navigation
- Match existing design system patterns when present

## Completion Protocol
When your work is complete:
1. Ensure all tests pass
2. Summarize what you built and any decisions you made
3. Flag anything you're uncertain about as a question
```

- [ ] **Step 10 (2 min):** Create `src/devteam/templates/agents/devops_engineer.md`.

```markdown
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the DevOps Engineer for the development team.

## Expertise
CI/CD pipelines, containerization (Docker, OCI), infrastructure as code, monitoring, logging, alerting, and deployment automation.

## Working Style
- Read existing CI/CD configuration before proposing changes
- Follow project conventions discovered in the codebase
- Write infrastructure tests and validation scripts
- Create focused, atomic commits
- Prefer declarative over imperative configuration
- Ensure pipelines are reproducible and idempotent

## Completion Protocol
When your work is complete:
1. Ensure all tests and validation scripts pass
2. Summarize what you built and any decisions you made
3. Flag anything you're uncertain about as a question
```

- [ ] **Step 11 (2 min):** Create `src/devteam/templates/agents/data_engineer.md`.

```markdown
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Data Engineer for the development team.

## Expertise
Database design, schema migrations, query optimization, ETL pipelines, data modeling, indexing strategies, and ORM configuration.

## Working Style
- Read existing schema and migration history before proposing changes
- Follow project conventions discovered in the codebase
- Write reversible migrations with both up and down paths
- Create focused, atomic commits
- Consider query performance implications of schema changes
- Test migrations against realistic data volumes when possible

## Completion Protocol
When your work is complete:
1. Ensure all tests and migrations pass
2. Summarize what you built and any decisions you made
3. Flag anything you're uncertain about as a question
```

- [ ] **Step 12 (2 min):** Create `src/devteam/templates/agents/infra_engineer.md`.

```markdown
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Infra Engineer for the development team.

## Expertise
Performance optimization, scaling strategies, complex refactoring, system reliability, caching, load balancing, and resource management.

## Working Style
- Read existing code and architecture before proposing changes
- Follow project conventions discovered in the codebase
- Write benchmarks and performance tests alongside implementation
- Create focused, atomic commits
- Profile before optimizing — measure, don't guess
- Document performance characteristics and scaling limits

## Completion Protocol
When your work is complete:
1. Ensure all tests pass
2. Summarize what you built and any decisions you made
3. Flag anything you're uncertain about as a question
```

- [ ] **Step 13 (2 min):** Create `src/devteam/templates/agents/tooling_engineer.md`.

```markdown
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Tooling/CLI Engineer for the development team.

## Expertise
CLI applications, SDKs, build systems, developer experience tooling, code generation, and internal developer platforms.

## Working Style
- Read existing code before proposing changes
- Follow project conventions discovered in the codebase
- Write tests alongside implementation
- Create focused, atomic commits
- Design CLIs with clear help text, consistent flags, and good error messages
- Prioritize developer ergonomics — tools should be intuitive

## Completion Protocol
When your work is complete:
1. Ensure all tests pass
2. Summarize what you built and any decisions you made
3. Flag anything you're uncertain about as a question
```

- [ ] **Step 14 (2 min):** Create `src/devteam/templates/agents/cloud_engineer.md`.

```markdown
---
model: sonnet
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Cloud/Platform Engineer for the development team.

## Expertise
Platform-specific deployment (AWS, GCP, Azure, Fly.io, Railway, Vercel, Cloudflare), cloud service configuration, managed databases, CDN setup, DNS, and platform-native patterns.

## Working Style
- Read existing deployment configuration before proposing changes
- Follow project conventions discovered in the codebase
- Write deployment validation scripts and smoke tests
- Create focused, atomic commits
- Use platform-native patterns rather than fighting the platform
- Document environment-specific configuration clearly

## Completion Protocol
When your work is complete:
1. Ensure all tests and deployment validation pass
2. Summarize what you built and any decisions you made
3. Flag anything you're uncertain about as a question
```

- [ ] **Step 15 (2 min):** Create `src/devteam/templates/agents/qa_engineer.md`.

```markdown
---
model: haiku
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the QA Engineer for the development team.

## Expertise
Test strategy, test authoring, acceptance validation, regression testing, edge case identification, and test coverage analysis.

## Responsibilities
- Validate implementation against acceptance criteria
- Identify missing test coverage
- Check edge cases and error handling
- Verify that tests actually test the right things (not just passing)
- Review test quality — meaningful assertions, not just smoke tests

## Working Style
- Read the spec/acceptance criteria first, then review the implementation
- Run existing tests before writing new ones
- Focus on behavior, not implementation details
- Check boundary conditions and error paths
- Verify that test names describe what they test

## Completion Protocol
Return a review verdict:
- **approved** — all acceptance criteria met, adequate test coverage
- **approved_with_comments** — criteria met, minor suggestions
- **needs_revision** — missing coverage or failing criteria (list specifics)
- **blocked** — fundamental issue preventing validation
```

- [ ] **Step 16 (2 min):** Create `src/devteam/templates/agents/security_engineer.md`.

```markdown
---
model: haiku
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Security Engineer for the development team.

## Expertise
OWASP compliance, dependency auditing, authentication/authorization review, input validation, secrets management, and security-focused code review.

## Responsibilities
- Audit code changes for common vulnerabilities (OWASP Top 10)
- Check dependency versions for known CVEs
- Review authentication and authorization logic
- Verify input validation and output encoding
- Ensure secrets are not hardcoded or logged
- Check for insecure defaults and misconfigurations

## Working Style
- Review the PR diff, not the entire codebase
- Focus on security-relevant changes — don't nitpick style
- Categorize findings by severity (error for real vulnerabilities, warning for best-practice violations, nitpick for hardening suggestions)
- Provide remediation guidance, not just identification

## Completion Protocol
Return a review verdict:
- **approved** — no security issues found
- **approved_with_comments** — minor hardening suggestions
- **needs_revision** — security vulnerabilities found (list specifics with severity)
- **blocked** — critical vulnerability that must be fixed before merge
```

- [ ] **Step 17 (2 min):** Create `src/devteam/templates/agents/tech_writer.md`.

```markdown
---
model: haiku
tools:
  - Read
  - Edit
  - Write
  - Bash
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - query_knowledge
---

You are the Tech Writer for the development team.

## Expertise
API documentation, architecture documentation, READMEs, runbooks, inline code documentation, and developer-facing guides.

## Responsibilities
- Review documentation changes for accuracy, completeness, and clarity
- Ensure public APIs have adequate documentation
- Verify that READMEs stay in sync with actual behavior
- Check that architecture docs reflect the current system
- Author documentation when assigned as the primary implementer

## Working Style
- Read the code to verify documentation accuracy
- Focus on developer audience — clarity and actionability
- Ensure code examples actually work
- Check for outdated references and broken links
- Keep docs concise — explain what developers need, skip what they don't

## Completion Protocol
Return a review verdict:
- **approved** — documentation is accurate and complete
- **approved_with_comments** — minor clarity improvements suggested
- **needs_revision** — inaccurate, incomplete, or misleading docs (list specifics)
- **blocked** — missing critical documentation for a public API
```

- [ ] **Step 18 (2 min):** Write a validation test that all 16 templates parse correctly.

```python
# tests/agents/test_templates.py
"""Tests that all 16 agent template .md files parse correctly."""
from pathlib import Path

import pytest
from devteam.agents.registry import AgentDefinition, AgentRegistry


TEMPLATES_DIR = Path(__file__).parent.parent.parent / "src" / "devteam" / "templates" / "agents"

EXPECTED_AGENTS = {
    "ceo": ("opus", ["Read", "Glob", "Grep"]),
    "chief_architect": ("opus", 9),
    "planner_researcher_a": ("sonnet", 9),
    "planner_researcher_b": ("sonnet", 9),
    "em_team_a": ("sonnet", 9),
    "em_team_b": ("sonnet", 9),
    "backend_engineer": ("sonnet", 9),
    "frontend_engineer": ("sonnet", 9),
    "devops_engineer": ("sonnet", 9),
    "data_engineer": ("sonnet", 9),
    "infra_engineer": ("sonnet", 9),
    "tooling_engineer": ("sonnet", 9),
    "cloud_engineer": ("sonnet", 9),
    "qa_engineer": ("haiku", 9),
    "security_engineer": ("haiku", 9),
    "tech_writer": ("haiku", 9),
}


class TestAgentTemplates:
    def test_templates_directory_exists(self):
        assert TEMPLATES_DIR.is_dir(), f"Templates directory not found: {TEMPLATES_DIR}"

    def test_all_16_agents_present(self):
        md_files = {f.stem for f in TEMPLATES_DIR.glob("*.md")}
        expected = set(EXPECTED_AGENTS.keys())
        assert md_files == expected, f"Missing: {expected - md_files}, Extra: {md_files - expected}"

    def test_registry_loads_all_templates(self):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        assert len(registry) == 16

    @pytest.mark.parametrize("role", EXPECTED_AGENTS.keys())
    def test_agent_parses_correctly(self, role):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        defn = registry.get(role)
        expected_model, expected_tools = EXPECTED_AGENTS[role]
        assert defn.model == expected_model, f"{role}: expected model {expected_model}, got {defn.model}"
        if isinstance(expected_tools, list):
            assert defn.tools == expected_tools, f"{role}: tools mismatch"
        else:
            assert len(defn.tools) == expected_tools, f"{role}: expected {expected_tools} tools, got {len(defn.tools)}"

    @pytest.mark.parametrize("role", EXPECTED_AGENTS.keys())
    def test_agent_has_nonempty_prompt(self, role):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        defn = registry.get(role)
        assert len(defn.prompt) > 50, f"{role}: prompt too short ({len(defn.prompt)} chars)"

    def test_ceo_has_restricted_tools(self):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        ceo = registry.get("ceo")
        assert "Bash" not in ceo.tools
        assert "Edit" not in ceo.tools
        assert "Write" not in ceo.tools
        assert "Read" in ceo.tools

    def test_engineers_have_full_tools(self):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        for role in ["backend_engineer", "frontend_engineer", "devops_engineer",
                      "data_engineer", "infra_engineer", "tooling_engineer", "cloud_engineer"]:
            defn = registry.get(role)
            assert "Bash" in defn.tools, f"{role} missing Bash"
            assert "Edit" in defn.tools, f"{role} missing Edit"
            assert "query_knowledge" in defn.tools, f"{role} missing query_knowledge"

    def test_validation_agents_use_haiku(self):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        for role in ["qa_engineer", "security_engineer", "tech_writer"]:
            defn = registry.get(role)
            assert defn.model == "haiku", f"{role} should use haiku, got {defn.model}"

    def test_executive_agents_use_opus(self):
        registry = AgentRegistry.load(TEMPLATES_DIR)
        for role in ["ceo", "chief_architect"]:
            defn = registry.get(role)
            assert defn.model == "opus", f"{role} should use opus, got {defn.model}"
```

**Test command:** `pixi run pytest tests/agents/test_templates.py -v`

- [ ] **Step 19 (1 min):** Run template validation tests, verify all pass.

```bash
pixi run pytest tests/agents/test_templates.py -v
```

---

## Task 5: Wire up `devteam init` to copy agent templates

**File:** Extend `src/devteam/cli.py` (assumes Plan 1 created this file)

The `devteam init` command copies agent templates from the package's bundled `templates/agents/` directory to `~/.devteam/agents/`.

- [ ] **Step 1 (3 min):** Write test file `tests/test_init_agents.py`.

```python
# tests/test_init_agents.py
"""Tests for devteam init copying agent templates to ~/.devteam/agents/."""
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from devteam.agents.template_manager import copy_agent_templates, get_bundled_templates_dir


class TestGetBundledTemplatesDir:
    def test_returns_path_to_templates(self):
        templates_dir = get_bundled_templates_dir()
        assert templates_dir.is_dir()
        assert (templates_dir / "ceo.md").exists()
        assert len(list(templates_dir.glob("*.md"))) == 16


class TestCopyAgentTemplates:
    def test_copies_all_templates(self, tmp_path):
        dest = tmp_path / "agents"
        copy_agent_templates(dest)
        assert dest.is_dir()
        md_files = list(dest.glob("*.md"))
        assert len(md_files) == 16

    def test_creates_destination_directory(self, tmp_path):
        dest = tmp_path / "nested" / "agents"
        copy_agent_templates(dest)
        assert dest.is_dir()

    def test_preserves_existing_customizations(self, tmp_path):
        dest = tmp_path / "agents"
        dest.mkdir()
        custom_file = dest / "ceo.md"
        custom_file.write_text("custom CEO content")

        copy_agent_templates(dest, overwrite=False)
        assert custom_file.read_text() == "custom CEO content"
        # But missing agents should be copied
        assert (dest / "backend_engineer.md").exists()

    def test_overwrite_replaces_existing(self, tmp_path):
        dest = tmp_path / "agents"
        dest.mkdir()
        custom_file = dest / "ceo.md"
        custom_file.write_text("custom CEO content")

        copy_agent_templates(dest, overwrite=True)
        assert custom_file.read_text() != "custom CEO content"
        assert "model: opus" in custom_file.read_text()

    def test_copies_content_correctly(self, tmp_path):
        dest = tmp_path / "agents"
        copy_agent_templates(dest)
        ceo_content = (dest / "ceo.md").read_text()
        assert "model: opus" in ceo_content
        assert "You are the CEO" in ceo_content
```

**Test command:** `pixi run pytest tests/test_init_agents.py -v`

- [ ] **Step 2 (3 min):** Implement `src/devteam/agents/template_manager.py`.

```python
# src/devteam/agents/template_manager.py
"""Manages copying agent templates from the package to user directories.

Used by `devteam init` (copies to ~/.devteam/agents/) and
`devteam project add` (copies to project's .claude/agents/).
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
        dest_file = dest_dir / src_file.name
        if dest_file.exists() and not overwrite:
            continue
        shutil.copy2(src_file, dest_file)
        copied.append(dest_file)

    return copied
```

**Test command:** `pixi run pytest tests/test_init_agents.py -v`

- [ ] **Step 3 (1 min):** Run tests, verify all pass.

```bash
pixi run pytest tests/test_init_agents.py -v
```

---

## Task 6: Wire up `devteam project add` to copy agents into project

**File:** Extend project registration (assumes Plan 1 created the project management module)

When a project is registered with `devteam project add /path/to/repo`, agent definitions are copied from `~/.devteam/agents/` into the project's `.claude/agents/` directory.

- [ ] **Step 1 (3 min):** Write test file `tests/test_project_agents.py`.

```python
# tests/test_project_agents.py
"""Tests for copying agents into a project's .claude/agents/ on registration."""
from pathlib import Path

import pytest
from devteam.agents.template_manager import copy_agent_templates, copy_agents_to_project


class TestCopyAgentsToProject:
    def _setup_global_agents(self, tmp_path: Path) -> Path:
        """Set up a fake ~/.devteam/agents/ directory with templates."""
        global_agents = tmp_path / "devteam" / "agents"
        copy_agent_templates(global_agents)
        return global_agents

    def test_copies_agents_to_project(self, tmp_path):
        global_agents = self._setup_global_agents(tmp_path)
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()

        copy_agents_to_project(global_agents, project_dir)

        claude_agents = project_dir / ".claude" / "agents"
        assert claude_agents.is_dir()
        assert len(list(claude_agents.glob("*.md"))) == 16
        assert (claude_agents / "ceo.md").exists()

    def test_creates_claude_directory(self, tmp_path):
        global_agents = self._setup_global_agents(tmp_path)
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()

        copy_agents_to_project(global_agents, project_dir)
        assert (project_dir / ".claude" / "agents").is_dir()

    def test_project_dir_must_exist(self, tmp_path):
        global_agents = self._setup_global_agents(tmp_path)
        with pytest.raises(FileNotFoundError):
            copy_agents_to_project(global_agents, tmp_path / "nonexistent")

    def test_preserves_existing_project_agents(self, tmp_path):
        global_agents = self._setup_global_agents(tmp_path)
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        claude_agents = project_dir / ".claude" / "agents"
        claude_agents.mkdir(parents=True)
        custom = claude_agents / "ceo.md"
        custom.write_text("customized CEO")

        copy_agents_to_project(global_agents, project_dir, overwrite=False)
        assert custom.read_text() == "customized CEO"
```

**Test command:** `pixi run pytest tests/test_project_agents.py -v`

- [ ] **Step 2 (3 min):** Add `copy_agents_to_project` to `src/devteam/agents/template_manager.py`.

Add this function to the existing `template_manager.py`:

```python
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
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Project directory not found: {project_dir}")

    dest_dir = project_dir / ".claude" / "agents"
    dest_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for src_file in sorted(Path(global_agents_dir).glob("*.md")):
        dest_file = dest_dir / src_file.name
        if dest_file.exists() and not overwrite:
            continue
        shutil.copy2(src_file, dest_file)
        copied.append(dest_file)

    return copied
```

**Test command:** `pixi run pytest tests/test_project_agents.py -v`

- [ ] **Step 3 (1 min):** Run tests, verify all pass.

```bash
pixi run pytest tests/test_project_agents.py -v
```

---

## Task 7: Update `__init__.py` exports and integration test

**File:** `src/devteam/agents/__init__.py`

- [ ] **Step 1 (2 min):** Update `src/devteam/agents/__init__.py` with public API exports.

```python
# src/devteam/agents/__init__.py
"""Agent definitions, registry, invoker, and structured output contracts."""
from devteam.agents.contracts import (
    DecompositionResult,
    ImplementationResult,
    ReviewComment,
    ReviewResult,
    RoutingResult,
    TaskDecomposition,
)
from devteam.agents.invoker import AgentInvoker, InvocationContext, InvocationError
from devteam.agents.registry import AgentDefinition, AgentRegistry
from devteam.agents.template_manager import (
    copy_agent_templates,
    copy_agents_to_project,
    get_bundled_templates_dir,
)

__all__ = [
    # Contracts
    "DecompositionResult",
    "ImplementationResult",
    "ReviewComment",
    "ReviewResult",
    "RoutingResult",
    "TaskDecomposition",
    # Invoker
    "AgentInvoker",
    "InvocationContext",
    "InvocationError",
    # Registry
    "AgentDefinition",
    "AgentRegistry",
    # Template management
    "copy_agent_templates",
    "copy_agents_to_project",
    "get_bundled_templates_dir",
]
```

- [ ] **Step 2 (3 min):** Write integration test `tests/agents/test_integration.py`.

```python
# tests/agents/test_integration.py
"""Integration test: load templates -> build registry -> build invoker -> verify params."""
from pathlib import Path

import pytest
from devteam.agents import (
    AgentInvoker,
    AgentRegistry,
    InvocationContext,
    copy_agent_templates,
    get_bundled_templates_dir,
)


class TestFullPipeline:
    """End-to-end: templates -> registry -> invoker parameter generation."""

    def test_bundled_templates_load_into_registry(self):
        templates_dir = get_bundled_templates_dir()
        registry = AgentRegistry.load(templates_dir)
        assert len(registry) == 16

    def test_copied_templates_load_into_registry(self, tmp_path):
        dest = tmp_path / "agents"
        copy_agent_templates(dest)
        registry = AgentRegistry.load(dest)
        assert len(registry) == 16

    def test_invoker_builds_params_for_all_roles(self, tmp_path):
        templates_dir = get_bundled_templates_dir()
        registry = AgentRegistry.load(templates_dir)
        invoker = AgentInvoker(registry)
        context = InvocationContext(worktree_path=tmp_path, project_name="test")

        for role in registry.list_roles():
            params = invoker.build_query_params(
                role=role,
                task_prompt=f"Test task for {role}",
                context=context,
            )
            assert params["model"] in ("opus", "sonnet", "haiku")
            assert params["permission_mode"] == "bypassPermissions"
            assert "json_schema" in params
            assert isinstance(params["allowed_tools"], list)
            assert len(params["allowed_tools"]) > 0

    def test_ceo_gets_routing_schema(self):
        templates_dir = get_bundled_templates_dir()
        registry = AgentRegistry.load(templates_dir)
        invoker = AgentInvoker(registry)
        schema = invoker.schema_for_role("ceo")
        assert "path" in schema["properties"]
        assert "reasoning" in schema["properties"]

    def test_engineers_get_implementation_schema(self):
        templates_dir = get_bundled_templates_dir()
        registry = AgentRegistry.load(templates_dir)
        invoker = AgentInvoker(registry)

        for role in ["backend_engineer", "frontend_engineer", "devops_engineer",
                      "data_engineer", "infra_engineer", "tooling_engineer", "cloud_engineer"]:
            schema = invoker.schema_for_role(role)
            assert "status" in schema["properties"]
            assert "files_changed" in schema["properties"]

    def test_reviewers_get_review_schema(self):
        templates_dir = get_bundled_templates_dir()
        registry = AgentRegistry.load(templates_dir)
        invoker = AgentInvoker(registry)

        for role in ["em_team_a", "em_team_b", "qa_engineer", "security_engineer", "tech_writer"]:
            schema = invoker.schema_for_role(role)
            assert "verdict" in schema["properties"]
            assert "comments" in schema["properties"]
```

**Test command:** `pixi run pytest tests/agents/test_integration.py -v`

- [ ] **Step 3 (1 min):** Run all tests.

```bash
pixi run pytest tests/agents/ tests/test_init_agents.py tests/test_project_agents.py -v
```

---

## Task 8: Add dependencies to pyproject.toml

**File:** `pyproject.toml` (assumes Plan 1 created this)

- [ ] **Step 1 (2 min):** Add `pyyaml` and `pydantic` to project dependencies.

Add to the `[project.dependencies]` section:

```toml
"pydantic>=2.0,<3",
"pyyaml>=6.0,<7",
```

Add to dev dependencies:

```toml
"pytest-asyncio>=0.23,<1",
```

- [ ] **Step 2 (1 min):** Install and verify.

```bash
pixi install
pixi run pytest tests/agents/ -v
```

---

## Summary

| Task | Files | What it builds |
|------|-------|---------------|
| 1 | `src/devteam/agents/contracts.py`, `tests/agents/test_contracts.py` | 4 Pydantic models with JSON schema generation |
| 2 | `src/devteam/agents/registry.py`, `tests/agents/test_registry.py` | .md frontmatter parser + in-memory role registry |
| 3 | `src/devteam/agents/invoker.py`, `tests/agents/test_invoker.py` | Claude Agent SDK `query()` wrapper with structured output |
| 4 | `src/devteam/templates/agents/*.md` (16 files), `tests/agents/test_templates.py` | All 16 agent definitions with correct model/tool/prompt |
| 5 | `src/devteam/agents/template_manager.py`, `tests/test_init_agents.py` | `devteam init` copies templates to `~/.devteam/agents/` |
| 6 | (extends template_manager.py), `tests/test_project_agents.py` | `devteam project add` copies agents to `.claude/agents/` |
| 7 | `src/devteam/agents/__init__.py`, `tests/agents/test_integration.py` | Public API exports + end-to-end integration test |
| 8 | `pyproject.toml` | Dependencies: pydantic, pyyaml, pytest-asyncio |

**Total files created:** 26 (4 Python modules, 16 agent templates, 6 test files)

**Dependencies added:** `pydantic>=2.0,<3`, `pyyaml>=6.0,<7`, `pytest-asyncio>=0.23,<1` (dev)

**Execution order:** Tasks 1-3 can be done in parallel. Task 4 depends on Task 2 (registry must exist for template validation tests). Tasks 5-6 depend on Task 4. Task 7 depends on all prior tasks. Task 8 should be done first if dependencies aren't already installed.
