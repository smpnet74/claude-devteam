# claude-devteam: Durable AI Development Team Orchestrator

## Overview

claude-devteam is a Python CLI application that orchestrates a team of 16 specialized AI agents to execute software development projects. It replaces Paperclip's heartbeat-based polling model with event-driven durable workflows, enforces development process in code rather than agent prompts, and builds institutional knowledge that compounds across projects.

### V1 Scope

**Supported:**
- Single local operator (one human, one machine)
- GitHub-hosted repositories
- Claude CLI as the agent runtime
- CodeRabbit integration (optional)
- Multiple concurrent jobs across different repos

**Not in V1:**
- Multi-user collaboration
- Non-GitHub VCS (GitLab, Bitbucket)
- Web dashboard
- Multi-machine distribution
- Slack/Discord notifications

### Problems Solved

| Problem (Paperclip) | Solution (claude-devteam) |
|---|---|
| Heartbeat polling — finished work sits idle for up to an hour | Event-driven handoffs via DBOS workflows — immediate routing |
| Workflow steps get skipped when agents lose context | Review chain enforced in Python code, not agent instructions |
| Agents relearn the same things every session | Two-mechanism memory: injected index + on-demand knowledge query tool |
| Token-heavy fat context payloads on every heartbeat | Agents receive only their task + relevant knowledge, nothing more |
| Single EM bottleneck — delegation doesn't happen | Two parallel EM-led tracks + orchestrator handles delegation |
| No execution visibility — must mentally stitch together issue logs | Execution trace queryable via `devteam trace` over DBOS state |
| Branches and worktrees never cleaned up | Mandatory cleanup as a durable workflow step after every merge |
| PR feedback cycle is clunky and token-intensive | Session-resumption with diff-only feedback, zero-token polling for CI |

### Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| Orchestrator | DBOS (Python SDK) | Durable workflow execution, crash recovery, queues |
| Workflow persistence | SQLite (via DBOS) | Workflow state, step results, execution trace |
| Agent invocation | Claude Agent SDK (Python) | Programmatic Claude session management |
| Knowledge store | SurrealDB (embedded, file-backed) | Institutional memory — vector search + graph relationships |
| Embeddings | Ollama + nomic-embed-text | Local embedding generation, zero external API dependency |
| CLI | Python (Click or Typer) | User interface — `devteam` commands |
| Git isolation | Git worktrees | Per-task/PR-group isolated working directories |

---

## System Architecture

```
+---------------------------------------------------+
|  devteam CLI                                       |
|  (see Operator Model for full command list)        |
+---------------------------------------------------+
|  devteam daemon (FastAPI on localhost:7432)         |
+---------------------------------------------------+
|  DBOS Orchestrator                                 |
|  +- Workflow Engine (durable plan execution)       |
|  +- Agent Invoker (Claude Agent SDK query())       |
|  +- Queue Manager (concurrency, priority)          |
|  +- Rate Limit Handler (reactive backoff)          |
+------------------------+--------------------------+
|  SQLite (DBOS)         |  SurrealDB (Knowledge)   |
|  - workflow state      |  - shared team knowledge  |
|  - step results        |  - per-agent expertise    |
|  - execution trace     |  - project learnings      |
|  - queue state         |  - graph relationships    |
|  - global pause flag   |  - vector embeddings      |
+------------------------+--------------------------+
```

### Global Daemon Model

The devteam daemon is a single long-running process on the operator's machine. All state lives in `~/.devteam/`:

```
~/.devteam/
  config.toml          # global configuration
  devteam.sqlite       # DBOS workflow state
  knowledge/           # SurrealDB embedded store
  agents/              # agent definition templates
  projects/            # registered project clones (OSS)
  logs/                # agent output logs (W-{id}/T-{id}.log)
  traces/              # trace exports
  exports/             # knowledge exports
  focus/               # per-shell focus state (<shell-pid>)
  daemon.pid           # singleton lock
  daemon.port          # active port file
```

- One daemon, one state directory, one HTTP API
- `devteam daemon start` / `devteam daemon stop` / `devteam daemon status`
- Singleton lock prevents duplicate daemons
- If the daemon is not running, `devteam start` launches it automatically
- Read-only commands (`status`, `trace`) can query SQLite directly without a running daemon

Projects are registered with the daemon: `devteam project add /path/to/repo`. Agent definitions are copied from `~/.devteam/agents/` into each project's `.claude/agents/` on registration.

### Process Model

Agent invocations are ephemeral — each is a single `query()` call through the Agent SDK that starts and finishes. There are no persistent agent processes.

Multiple jobs run concurrently within the single daemon process. All jobs share the DBOS queue (which enforces concurrency limits), the SurrealDB knowledge base (with scoping rules — see Knowledge Boundaries), and the API rate limit budget.

### Disaster Recovery

| Scenario | Behavior | Recovery |
|---|---|---|
| Daemon crash | Active agent subprocesses orphan and die | `devteam resume` — DBOS replays completed steps from SQLite, resumes at first incomplete step |
| Machine reboot | Same as crash | Same — `devteam resume` |
| Agent invocation hangs | DBOS step timeout fires | Orchestrator retries or escalates |
| SQLite corruption | Workflow state lost | Periodic SQLite backups (WAL mode for crash resilience) |
| SurrealDB corruption | Knowledge lost, workflows unaffected | Knowledge rebuilds over time through extraction |
| SurrealDB unavailable | Knowledge index empty, extraction skipped | Agents proceed without knowledge (graceful degradation) |
| Rate limit mid-agent | Agent returns error | Step marked failed, durable sleep until reset, retry |

### Side-Effect Recovery

DBOS can replay workflow state, but filesystem, git, and GitHub side effects are not automatically reversible. The orchestrator enforces idempotent recovery for all side-effecting steps:

**Worktree recovery:** Before retrying a failed agent step, reset the worktree to the last known clean commit. Partial work is discarded; the agent redoes it cleanly.

**Git recovery:** Before pushing, check if the branch already exists on remote with the expected commits. If so, skip the push (idempotent).

**GitHub recovery:** Before opening a PR, check if a PR already exists for that branch. If so, capture the PR number and continue (idempotent reconciliation). Same for comments — check before posting.

**Merge recovery:** Before merging, verify the PR still exists and is in a mergeable state. If already merged, skip and proceed to cleanup.

Pattern: **before any side-effecting step, check if the effect already happened.** Every external action is idempotent on retry.

### Shutdown & Job Control

| Command | Behavior |
|---|---|
| `devteam stop` | Graceful — active agents finish current step, then all workflows halt |
| `devteam stop --force` | Immediate — SIGTERM then SIGKILL all agent subprocesses, exit |
| `devteam stop W-1` | Graceful stop for job W-1 only, other jobs continue |
| `devteam pause W-1` | Pause job W-1, other jobs continue |
| `devteam resume` | Restart daemon and recover all pending workflows from SQLite |
| `devteam resume W-1` | Resume a paused job |
| `devteam cancel W-1` | Cancel job W-1, full cleanup (see Job Cancellation below) |
| `devteam cancel W-1 --revert-merged` | Cancel + create revert PRs for already-merged work |
| `devteam retry W-1/T-3` | Retry a specific failed task |
| `Ctrl+C` | Same as `--force` |

### Job Cancellation

`devteam cancel W-1` performs a complete cleanup:

1. **Stop all active agents** for the job (SIGTERM)
2. **Close any open PRs** with a comment ("Cancelled by operator")
3. **Delete remote feature branches** created by the job
4. **Remove worktrees** created by the job
5. **Delete local feature branches** created by the job
6. **Leave main/master untouched** — all work happens on feature branches, so main is always clean

**Already-merged PRs** are preserved by default. Work that passed peer review, QA, security, and EM approval is presumably good code. To revert merged work, use `--revert-merged`, which creates revert PRs that require manual merge approval.

Post-cancel status output:

```
[W-1] My App — CANCELLED

  Cleaned up:
    Closed PR #12 feat/user-auth
    Deleted branch feat/user-auth (local + remote)
    Removed worktree .worktrees/feat-user-auth
    Closed PR #14 feat/auth-ui
    Deleted branch feat/auth-ui (local + remote)
    Removed worktree .worktrees/feat-auth-ui

  Preserved (already merged):
    PR #11 feat/project-init — merged before cancel
    Run: devteam cancel W-1 --revert-merged to create revert PR
```

Each cleanup step is idempotent — if the daemon crashes mid-cancellation, `devteam cancel W-1` can be run again safely.

---

## Agent Team Structure

16 agents organized as a fixed, general-purpose software consulting firm. The team structure does not change per project — specialization ensures every project gets the right expertise. Engineers not needed for a specific job simply are not invoked.

```
                        Human (Board)
                            |
                           CEO
                     (opus, intake & routing)
                     +------+---------------+
                     |      |               |
             Chief Architect |          (small fixes)
             (opus, design   |               |
              & standards)   |               v
                  |          |          directly to
             Planner/    Planner/      appropriate EM
             Researcher  Researcher
             (sonnet)    (sonnet)
                  |
           +------+-------+
        EM Team A        EM Team B
        (sonnet)         (sonnet)
       +---+---+      +--+--+--+--+
   Backend Frontend  Data Infra Tooling Cloud
   DevOps
   (all sonnet)      (all sonnet)

               Shared Services
        (review gates — scope-appropriate)
        +------+--------+------+
       QA    Security  Tech Writer
     (haiku) (haiku)   (haiku)
```

### Team Roster

| Layer | Agent | Model | Specialization |
|---|---|---|---|
| Executive | CEO | opus | Intake, routing, orchestration. Never touches code. |
| Architecture | Chief Architect | opus | Design, standards, cross-track decomposition. Writes design docs and ADRs. Can flag spec ambiguities. |
| Planning | Planner/Researcher (x2) | sonnet | Codebase analysis, requirements decomposition, research, spec writing. |
| Delivery | EM Team A | sonnet | Delivery management for Team A. Quality gate, coordination. |
| Delivery | EM Team B | sonnet | Delivery management for Team B. Quality gate, coordination. |
| Team A | Backend Engineer | sonnet | APIs, services, server-side logic. |
| Team A | Frontend Engineer | sonnet | UI, components, state management, accessibility. |
| Team A | DevOps Engineer | sonnet | CI/CD pipelines, containerization, IaC, monitoring. |
| Team B | Data Engineer | sonnet | Database design, migrations, schemas, query optimization, ETL. |
| Team B | Infra Engineer | sonnet | Performance, scaling, complex refactoring. |
| Team B | Tooling/CLI Engineer | sonnet | CLIs, SDKs, build tools, developer experience. |
| Team B | Cloud/Platform Engineer | sonnet | Platform-specific deployment (AWS, GCP, Fly.io, Railway, etc.). |
| Shared | QA Engineer | haiku | Test strategy, test authoring, acceptance validation. |
| Shared | Security Engineer | haiku | OWASP compliance, dependency audits, security review. |
| Shared | Tech Writer | haiku | API docs, architecture docs, READMEs, runbooks. |

### Team Design

**Team A (3 engineers):** Backend, Frontend, DevOps — the application layer. Backend and Frontend work against shared interfaces; DevOps builds the CI/CD pipeline tightly coupled to the app.

**Team B (4 engineers):** Data, Infra, Tooling/CLI, Cloud — the systems layer. More independent work streams. Can absorb overflow and handle projects that don't involve traditional app development.

**Shared Services (3 agents):** QA, Security, Tech Writer — review gates applied based on work type, not universally (see Route-Appropriate Review).

**Status display:** Internally, teams are fixed. In `devteam status`, sections are labeled contextually based on what's active — "Planning," "Application," "Systems," "Review" — rather than always showing "Team A / Team B." Empty sections are hidden.

### Model Tiering

| Tier | Model | Roles | Rationale |
|---|---|---|---|
| Executive | opus | CEO, Chief Architect | Strategic reasoning, architectural decisions |
| Engineering | sonnet | EMs, all engineers, Planners | Implementation, coordination, research |
| Validation | haiku | QA, Security, Tech Writer | Follow checklists against defined criteria |
| Extraction | haiku | Knowledge extractor (internal, not a team member) | Lightweight post-step knowledge extraction |

The knowledge extractor is NOT one of the 16 team agents. It is an internal orchestrator function — a haiku invocation used by the orchestrator to extract learnings from agent output. It has no agent definition file and does not appear in the team roster.

### Tool Access

All agents except the CEO receive the full tool suite: Read, Edit, Write, Bash, Glob, Grep, WebSearch, WebFetch, plus the `query_knowledge` tool for memory access.

The CEO is restricted to Read, Glob, Grep — it routes and decides, never touches code.

Specialization comes from agent prompts and accumulated knowledge, not artificial tool restrictions. MCP servers are available to any agent that needs them (Playwright, GitHub, cloud-specific MCPs).

### Configuration Authority

Agent `.md` files are the single source of truth for: model, prompt, tool access. `~/.devteam/config.toml` is the single source of truth for: approval policies, concurrency limits, project settings, rate limit behavior. No duplication between the two.

At daemon startup, agent `.md` files are parsed once and their tool lists are loaded into an in-memory registry. The orchestrator's `allowed_tools` parameter in `invoke_agent_step()` reads from this registry — it is derived from the `.md` files, not a separate configuration.

---

## Routing & Intake

The CEO is the intelligent router. It assesses incoming work and sends it down the appropriate path.

### Routing Table

| Intake Type | Path | Review Chain |
|---|---|---|
| Superpowers spec+plan | CEO → CA decomposes across teams → EMs execute | Per work type (see Review Timing) |
| Raw idea / issue (needs spec+plan) | CEO → CA → Planners → CA reviews spec → decompose → EMs | Planner peer review → CA validates research/spec |
| Research request | CEO → CA → Planners → deliverable back to human | Planner peer review → CA validates |
| Small fix (clear scope) | CEO → appropriate EM → engineer → review chain | Peer → EM → QA (if behavior change) |
| Open-source issue | CEO → Planners research the project → CA specs it → decompose → EMs | Full review chain |

### Route-Appropriate Review

Not every deliverable goes through every reviewer. The orchestrator applies review gates based on the type of work:

| Work Type | Review Chain |
|---|---|
| Code changes | Peer review → EM → QA → Security → Tech Writer |
| Research output | Planner peer review → CA validates quality and accuracy |
| Planning/spec output | Planner peer review → CA validates |
| Architecture decisions | CA authors → CEO reviews |
| Documentation only | Tech Writer authors → relevant engineer validates accuracy |
| Small fix | Peer review → EM → QA (only if behavior change) |

Every deliverable gets reviewed by someone qualified to judge it, but not by everyone.

### Review Timing: Pre-PR vs Post-PR

| Gate | Timing | Granularity |
|---|---|---|
| Peer review | Pre-PR (per task) | Reviewer reads code in the worktree before PR opens |
| EM review | Pre-PR (per task) | EM approves before PR opens |
| QA review | Post-PR (per PR group) | QA validates against acceptance criteria after CI passes |
| Security review | Post-PR (per PR group) | Security audits the PR diff |
| Tech Writer review | Post-PR (per PR group) | Docs reviewed alongside the PR |

A task is "complete" when it passes peer review and EM approval (pre-PR gates). The PR is "ready to merge" when it passes QA, Security, Tech Writer, CI, and CodeRabbit (post-PR gates).

### CEO Routing for Superpowers Specs

When the human has already built a spec and plan using superpowers (which includes its own spec review loop), the CA's job is decomposition, not re-review:

- Map plan tasks to the right specialists
- Assign tasks to Team A vs Team B for maximum parallelism
- Identify dependencies between teams
- Trusts the superpowers process, but CAN flag internal inconsistencies or ambiguities. If the CA identifies an issue, the workflow pauses and escalates to the human rather than proceeding with a flawed plan.

### Peer Relationships & Escalation Routing

CEO and Chief Architect are peers, not in a strict hierarchy. Escalation routing depends on the type of question:

| Question Type | Escalation Path |
|---|---|
| Architecture/design | Engineer → EM → CA → Human |
| Routing/policy/priority | Engineer → EM → CEO → Human |
| Spec ambiguity/product | Engineer → EM → CEO → Human |
| Technical implementation | Engineer → EM → (resolved at EM level usually) |

---

## Entity Model & State Machine

### Entity Hierarchy

```
Job (W-1, W-2...)
  └─ App (the repo / service / module being worked on)
       └─ Task (T-1, T-2... unique within a job)
            └─ Question (Q-1, Q-2... unique within a job)
```

Jobs contain one or more Apps (repos). Tasks belong to Apps and are assigned to specific agents. Questions belong to Tasks.

PR Groups are an operational concept — the CA's decomposition step defines which tasks ship together as a single PR. PR Groups are visible in status output but are not a separate entity in the hierarchy.

### Job Lifecycle

```
created → planning → decomposing → executing → reviewing → completed
                                       ↕            ↕
                                  paused_rate_limit  failed
                                       ↕
                                    canceled
```

### Task Lifecycle

```
queued → assigned → executing → waiting_on_review → approved → completed
                       ↕              ↕
                  waiting_on_question  revision_requested → executing
                       ↕              ↕
                  waiting_on_ci       failed
                       ↕
                    paused
                       ↕
                    canceled (from any non-terminal state)
```

When a job is cancelled, all its non-completed tasks transition to `canceled`.

### Question Lifecycle

```
raised → escalated_to_supervisor → resolved
                ↕
         escalated_to_leadership → resolved
                ↕
         escalated_to_human → resolved
```

### PR Lifecycle

```
branch_created → pr_opened → waiting_on_ci → ci_passed → ready_for_merge → merged → cleaned_up
                                  ↕                ↕
                            ci_failed → fixing → waiting_on_ci
                                                   ↕
                                            max_iterations → escalated_to_human
```

**Task/PR state interaction:** When a PR enters `fixing` due to CI failure or review comments, the associated task(s) re-enter `revision_requested` → `executing`. These are parallel state machines tracking the same work — the task tracks the agent's activity, the PR tracks the GitHub artifact.

---

## Workflow Engine & Durability

The DBOS orchestrator enforces development process in Python code. Agents cannot skip workflow steps because the workflow function controls the sequence — agents only do the work assigned to them.

### Workflow Structure

Each routing path becomes a DBOS workflow. Every function call is a `@DBOS.step()` whose result is persisted to SQLite. If the process crashes, restart replays completed steps instantly (from SQLite, no agent invocation) and resumes at the first incomplete step.

**Important:** DBOS durability covers workflow state only. Filesystem, git, and GitHub side effects require explicit idempotent recovery (see Side-Effect Recovery above). PR status is polled every 60 seconds — the system is event-driven internally but uses polling for external CI/CodeRabbit status.

All code samples in this section are illustrative pseudocode showing the conceptual flow, not final implementation signatures.

```python
@DBOS.workflow()
def full_project_workflow(spec: str, plan: str):
    routing = ceo_route(spec, plan)
    decomposition = ca_decompose(spec, plan)

    # Dependency-aware parallel dispatch (DAG, not sequential list)
    task_dag = build_dependency_graph(decomposition.tasks)
    results = execute_dag(task_dag)  # runs independent tasks in parallel,
                                     # respects dependency edges

    # Route-appropriate review (only for code changes)
    if results.has_code_changes:
        qa_review(results)
        security_review(results)
        tech_writer_review(results)

    return integration_complete(results)
```

### Parallel Task Execution (DAG Model)

Tasks are not executed sequentially. The CA's decomposition produces a dependency graph:

```python
@DBOS.workflow()
def execute_dag(task_dag):
    completed = {}
    in_flight = {}  # task_id -> workflow_handle

    while task_dag.has_pending() or in_flight:
        # Launch all tasks whose dependencies are satisfied
        for task in task_dag.get_ready_tasks(completed):
            if task.id not in in_flight:
                handle = DBOS.start_workflow(task_workflow, task)
                in_flight[task.id] = handle

        # Wait for ANY in-flight task to complete (not all)
        finished_id, result = wait_for_any(in_flight)
        completed[finished_id] = result
        del in_flight[finished_id]
        # Loop back — newly unblocked tasks will be launched
```

### Review Chain Enforcement

Within each team, the review chain is enforced by the workflow function:

```python
@DBOS.workflow()
def task_workflow(task):
    impl = engineer_execute(task)
    peer = peer_review(impl, task.peer_reviewer)
    em_approval = em_review(impl, peer)

    while em_approval.needs_revision:
        impl = engineer_execute(task, em_approval.feedback)
        peer = peer_review(impl, task.peer_reviewer)
        em_approval = em_review(impl, peer)

    return impl
```

An agent cannot skip peer review because `peer_review()` is called before `em_review()` in the workflow function. This is code, not an instruction that can be forgotten.

### Review Failure Loop

When QA or Security fails a task after the PR is opened:

1. Failure feedback routes back to the original engineer (not a different agent)
2. Engineer fixes in the same worktree with session resumption
3. Fix goes through the full review chain again (peer → EM → QA/Security)
4. The orchestrator tracks revision count — circuit breaker after max iterations

### Peer Review Assignment

Peer reviewers are assigned by the orchestrator based on team membership:

| Team A | Peer Reviews |
|---|---|
| Backend | Frontend or DevOps reviews |
| Frontend | Backend reviews (shared interface awareness) |
| DevOps | Backend reviews (CI/CD + app coupling) |

| Team B | Peer Reviews |
|---|---|
| Data | Infra reviews (query performance, scaling) |
| Infra | Data or Tooling reviews |
| Tooling/CLI | Infra or Cloud reviews |
| Cloud | Infra reviews (deployment + infrastructure) |

The CA's decomposition step assigns peer reviewers. If the natural peer is busy, the EM assigns an available engineer from the same team.

### Question Escalation

Questions are first-class workflow events that pause execution:

1. Agent has a question → workflow pauses that task branch (other branches continue)
2. Question routes based on type (architecture → CA, policy → CEO, technical → EM)
3. Supervisor answers autonomously if within their authority, resuming the branch immediately
4. If supervisor can't answer → escalates up the chain
5. If no agent can resolve → surfaces to human via `devteam status --questions`
6. Human answers with `devteam answer <question-id> "..."` and the branch resumes

Task branches are paused, not proceeding with assumptions. No gap-filling, no hallucinated directions.

### Merge Conflict Policy

When parallel PRs within the same job touch the same files:

1. The first PR to pass all checks merges normally
2. The second PR detects a conflict during pre-merge check
3. The orchestrator routes the conflicting PR back to its engineer for rebase
4. Engineer rebases against the updated main branch in their worktree
5. PR goes through CI again (review approvals preserved if code changes are minimal)

For PRs across different jobs targeting the same repo: same-repo concurrency is detected at `devteam start` time. The orchestrator warns the human and serializes conflicting work unless the human explicitly allows parallel execution.

---

## Agent Invocation Model

### Agent Definitions

Each agent is defined as a `.claude/agents/<role>.md` file with minimal, static content. Agent `.md` files are the single source of truth for model, prompt, and tool access.

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
```

Agent definitions contain identity, expertise, and working style. No workflow instructions, no review routing, no process rules — the orchestrator handles all of that.

### Structured Output Contracts

The orchestrator depends on machine-readable outcomes from agents. Each step type requires a structured result envelope (enforced via the Agent SDK's `json_schema` parameter):

```python
# Implementation step result
ImplementationResult = {
    "status": "completed | needs_clarification | blocked",
    "question": "string or null",
    "files_changed": ["list of paths"],
    "tests_added": ["list of test files"],
    "summary": "what was built and why",
    "confidence": "high | medium | low"
}

# Review step result
ReviewResult = {
    "verdict": "approved | approved_with_comments | needs_revision | blocked",
    "comments": [{"file": "path", "line": N, "severity": "error|warning|nitpick", "comment": "text"}],
    "summary": "review summary"
}

# Decomposition result
DecompositionResult = {
    "tasks": [{"id": "str", "description": "str", "assigned_to": "role",
               "team": "a|b", "depends_on": ["task_ids"], "pr_group": "str"}],
    "peer_assignments": {"task_id": "reviewer_role"},
    "parallel_groups": [["task_ids that can run simultaneously"]]
}

# Routing result
RoutingResult = {
    "path": "full_project | research | small_fix | oss_contribution",
    "reasoning": "why this path"
}
```

These schemas ensure the orchestrator can reliably parse agent output without prose parsing.

### Dynamic Context Injection

Each agent invocation receives only what it needs. The invocation is structured as a sub-workflow (DBOS steps cannot call other steps):

```python
@DBOS.workflow()
def invoke_agent_workflow(role: str, task: Task, run_context: RunContext):
    knowledge_index = build_memory_index_step(role, run_context.project)

    context = f"""
## Your Assignment
{task.description}

## Spec Context
{task.relevant_spec_section}

## Available Knowledge
{knowledge_index}

## Questions
If anything is unclear, state your question clearly and stop.
Do not guess or assume.
"""

    result = invoke_agent_step(role, context, run_context)
    extract_knowledge_step(result.output, role, task)

    return result

@DBOS.step()
def invoke_agent_step(role: str, context: str, run_context: RunContext):
    result = await query(
        prompt=context,
        agent=role,
        options=ClaudeAgentOptions(
            cwd=run_context.worktree_path,
            allowed_tools=agent_tools[role],
            permission_mode="bypassPermissions",
            json_schema=result_schema_for(role)
        )
    )
    return result
```

### Token Efficiency

| Paperclip | claude-devteam |
|---|---|
| Fat payload every heartbeat (goal ancestry, budget, all assignments) | Only the specific task + knowledge index |
| Agent re-reads full AGENTS.md, SOUL.md, HEARTBEAT.md every cycle | Static agent prompt is small; dynamic context is task-specific |
| Agent calls APIs to discover what to work on | Orchestrator tells it exactly what to do |
| Agent decides its own workflow (and sometimes forgets) | Orchestrator controls workflow; agent just does the work |
| Agent manages its own memory reads/writes | Orchestrator handles knowledge retrieval and extraction |

---

## Knowledge & Memory System

### Architecture

Two-layer knowledge system in SurrealDB with two access mechanisms.

**Layer 1 — Shared Team Knowledge:** Cross-cutting patterns, project conventions, platform gotchas, process learnings. Accessible to all agents.

**Layer 2 — Agent Expertise:** Role-specific knowledge that compounds over time. Backend engineer accumulates API patterns; Cloud engineer accumulates platform-specific deployment knowledge. Isolated by default but queryable by other agents when needed.

### Knowledge Boundaries

Knowledge sharing follows layered defaults:

| Knowledge Type | Default Scope | Example |
|---|---|---|
| Process/tool knowledge | Shared across all projects | "CodeRabbit comments must be resolved before merge" |
| Code-level knowledge | Project-scoped | "This project uses Drizzle ORM, not Prisma" |
| Platform patterns | Shared across all projects | "Fly.io requires HEALTHCHECK in Dockerfile" |
| Security-sensitive | Project-scoped, never shared | API keys, auth configurations, internal URLs |

The knowledge extractor (haiku) tags each entry as `process` (shared by default) or `project-specific` (scoped by default) during extraction. The orchestrator filters knowledge queries accordingly.

**Security rules:**
- Knowledge entries are scanned for secrets/credentials before persistence — entries containing likely secrets are rejected
- `devteam knowledge purge <entry-id>` removes specific entries immediately
- `devteam knowledge redact <entry-id>` removes sensitive content while preserving the learning
- `devteam knowledge export --project myapp` exports project-scoped knowledge for backup/review

### Data Model

```sql
DEFINE TABLE knowledge SCHEMAFULL;
DEFINE FIELD content ON knowledge TYPE string;
DEFINE FIELD summary ON knowledge TYPE string;
DEFINE FIELD source ON knowledge TYPE object;
DEFINE FIELD tags ON knowledge TYPE array<string>;
DEFINE FIELD sharing ON knowledge TYPE string;  -- "shared" or "project"
DEFINE FIELD project ON knowledge TYPE option<string>;
DEFINE FIELD embedding ON knowledge TYPE array<float>;
DEFINE FIELD created_at ON knowledge TYPE datetime;
DEFINE FIELD verified ON knowledge TYPE bool;
DEFINE FIELD access_count ON knowledge TYPE int;

DEFINE INDEX knowledge_vec ON knowledge
    FIELDS embedding HNSW DIMENSION 768 DIST COSINE;

-- Graph relationships
-- agent:backend->discovered->knowledge:k1
-- knowledge:k2->supersedes->knowledge:k1
-- knowledge:k3->requires->knowledge:k1
-- knowledge:k1->relates_to->knowledge:k4
```

### Why SurrealDB (Not a Plain Vector Store)

SurrealDB's differentiated value is combining vector search, graph traversal, and document queries in a single query:

```sql
SELECT
    content, summary, tags,
    vector::similarity::cosine(embedding, $task_embedding) AS relevance,
    <-discovered<-agent.role AS discovered_by,
    ->requires->knowledge.summary AS prerequisites
FROM knowledge
WHERE embedding <|10|> $task_embedding
  AND (sharing = "shared" OR project = $current_project)
  AND (tags CONTAINS "shared" OR tags CONTAINS $agent_role)
  AND id NOT IN (SELECT ->supersedes->knowledge FROM knowledge)
ORDER BY relevance DESC
LIMIT 5;
```

One query that combines vector similarity, graph traversal (who discovered it, what prerequisites exist), document filtering (sharing scope, verification status), and staleness exclusion (superseded entries filtered out).

Graph relationships model knowledge evolution (supersedes), dependencies (requires), cross-agent transfer (agent X discovered, agent Y applied), and topical connections (relates_to).

### Access Mechanism 1: Memory Index (Injected)

A concise summary of what knowledge exists, generated dynamically from SurrealDB before each agent invocation. The index shows topics and entry counts, not content:

```
## Available Knowledge
You can query the knowledge base for details on any of these topics.

Backend:
- PostgreSQL migration patterns (3 entries, last updated 2026-03-20)
- REST API error handling conventions (2 entries)

Shared:
- CodeRabbit comment resolution process (1 entry, verified)
- Fly.io deployment checklist (4 entries)

Project-specific (myapp):
- Auth flow uses OAuth2+PKCE (1 entry)
```

The index is always fresh because it is a query result, not a cached file. It stays compact (~30-50 lines) regardless of knowledge base size because it groups by topic with counts, scoped by agent role and current project.

For performance at scale, SurrealDB events can materialize the index as a pre-computed record that auto-refreshes on every knowledge write:

```sql
DEFINE EVENT refresh_index ON knowledge
    WHEN $event IN ["CREATE", "UPDATE", "DELETE"]
    THEN {
        UPDATE index:current SET
            sections = (SELECT tags, count(),
                        math::max(created_at) AS last_updated
                        FROM knowledge GROUP BY tags),
            rebuilt_at = time::now();
    };
```

### Access Mechanism 2: Knowledge Query Tool (On-Demand)

Agents receive a `query_knowledge` tool that lets them search SurrealDB when they need details:

```python
def query_knowledge(query: str, scope: str = "all") -> str:
    """Search the team knowledge base.

    Args:
        query: what you're looking for
        scope: "shared", "my_role", "project", or "all"
    """
    embedding = ollama.embed(model="nomic-embed-text", input=query)

    scope_filter = ""
    if scope == "shared":
        scope_filter = 'AND sharing = "shared"'
    elif scope == "my_role":
        scope_filter = f'AND tags CONTAINS "{agent_role}"'
    elif scope == "project":
        scope_filter = f'AND project = "{current_project}"'

    results = surrealdb.query(f"""
        SELECT content, summary, tags,
            vector::similarity::cosine(embedding, $vec) AS relevance,
            <-discovered<-agent.role AS source_role,
            ->requires->knowledge.summary AS prerequisites
        FROM knowledge
        WHERE embedding <|5|> $vec
          AND id NOT IN (SELECT ->supersedes->knowledge FROM knowledge)
          {scope_filter}
        ORDER BY relevance DESC
        LIMIT 5
    """, {"vec": embedding["embeddings"][0]})

    return format_results(results)
```

### Knowledge Lifecycle

**Extraction (automatic, after every agent step):**

```python
@DBOS.step()
def extract_knowledge(agent_output, agent_role, task):
    learnings = invoke_agent(
        role="knowledge_extractor",  # haiku
        prompt=f"""Review this agent's work output.
        Extract ONLY genuinely reusable knowledge.
        Tag each entry as "process" (tool/workflow patterns)
        or "project" (code-specific conventions).
        Do NOT extract: task-specific details, obvious things,
        secrets, credentials, or internal URLs.""",
        context=agent_output
    )
    persist_to_surrealdb(learnings, agent_role, task)
```

**Verification:** Knowledge starts as `verified = false`. When an agent queries knowledge and successfully applies it (task completes without revision), the orchestrator marks it as verified.

**Decay & Consolidation (periodic):**
- `access_count` tracks how often knowledge is retrieved and used
- Knowledge that is never accessed decays in relevance ranking
- Contradictory knowledge is flagged; newer entries `supersede` older ones
- Periodic consolidation merges related fragments into coherent entries

### Embedding Generation

All embeddings are generated locally via Ollama with the `nomic-embed-text` model:

- 768 dimensions, ~274MB model size
- 8192 token context window (important for multi-paragraph knowledge entries)
- Zero external API dependency — no Anthropic/OpenAI calls for embeddings
- No token costs, no rate limits on the memory system

Prerequisite: Ollama running locally with the model pulled (`ollama pull nomic-embed-text`).

### Knowledge Admin Commands

| Command | Purpose |
|---|---|
| `devteam knowledge search "query"` | Semantic search of the knowledge base |
| `devteam knowledge stats` | Entry counts by scope, role, project, verification status |
| `devteam knowledge verify <entry-id>` | Manually mark an entry as verified |
| `devteam knowledge redact <entry-id>` | Remove sensitive content, preserve the learning |
| `devteam knowledge purge <entry-id>` | Delete an entry entirely |
| `devteam knowledge purge --project myapp` | Delete all project-scoped knowledge for a project |
| `devteam knowledge export` | Export full knowledge base (JSON) |
| `devteam knowledge export --project myapp` | Export project-scoped knowledge only |

---

## Rate Limit Management

### Approach

The orchestrator handles rate limiting reactively — catching errors when they occur and pausing all workflows via a global flag.

### Mechanism

The Agent SDK does not expose HTTP rate limit headers. The orchestrator uses two complementary approaches:

**1. Catch rate limit errors reactively:**

```python
@DBOS.workflow()
def rate_limit_aware_invoke(role, task, context):
    try:
        return invoke_agent_step(role, task, context)
    except RateLimitError as e:
        reset_seconds = parse_reset_time(e) or DEFAULT_BACKOFF_SECONDS
        set_global_pause_flag(reset_seconds)
        DBOS.sleep(reset_seconds)
        clear_global_pause_flag()
        return invoke_agent_step(role, task, context)
```

**2. Global pause flag checked before each invocation:**

Each workflow checks a shared pause flag (stored in SQLite) before dispatching its next agent invocation. When one workflow triggers a rate limit pause, all workflows see it:

```python
@DBOS.step()
def check_pause_before_invoke():
    pause_until = get_global_pause_flag()
    if pause_until and pause_until > now():
        return PauseRequired(resume_at=pause_until)
    return PauseNotRequired()
```

### Design Decisions

- Primary mechanism is reactive error handling — catch rate limit errors and back off
- Global pause flag in SQLite coordinates across all workflows
- Durable sleep survives process crashes — wake time is persisted to SQLite
- All workflows pause globally — prevents one job from starving another
- `devteam status` shows rate limit state when a pause is active (conditional — only shown when pause is active, not as a permanent metric)

---

## Git Lifecycle Management

### Project Jailing

Each job is scoped to registered project directories. Agent invocations set `cwd` to the project repo path. For multi-repo projects, the orchestrator manages which repo an agent is pointed at based on the task.

### Same-Repo Concurrency

When two jobs target the same repo:
- Detected at `devteam start` time
- Human is warned: "Job W-2 targets the same repo as active job W-1"
- Work is serialized by default unless the human explicitly allows parallel execution (`--allow-concurrent`)
- If allowed, each job gets its own worktrees and branches — merge conflicts handled by the rebase policy

### Human Edits During Execution

If the human manually edits files in a worktree while the system is running:
- Agent invocations will see the human's changes (they work in the same worktree)
- This is supported but risky — use `devteam pause W-1` before manual editing, then `devteam resume W-1`
- `devteam takeover W-1/T-3` is the explicit supported path for manual intervention (see Manual Takeover)

### Worktree & Branch Lifecycle

The orchestrator manages the full git lifecycle as workflow steps:

1. CA decomposition defines PR groupings — which tasks ship together
2. Orchestrator creates a worktree + feature branch per PR group
3. Engineers work in the worktree
4. On completion, orchestrator commits and opens PR
5. CI + CodeRabbit run; feedback is routed back to the engineer
6. On merge, orchestrator deletes worktree + local branch + remote branch

```python
@DBOS.step()
def setup_worktree(task_group, branch_name):
    subprocess.run(["git", "worktree", "add",
                     f".worktrees/{branch_name}", "-b", branch_name])
    return WorktreeInfo(path=f".worktrees/{branch_name}", branch=branch_name)

@DBOS.step()
def cleanup_after_merge(worktree_path, branch_name):
    subprocess.run(["git", "worktree", "remove", worktree_path])
    subprocess.run(["git", "branch", "-d", branch_name])
    subprocess.run(["git", "push", "origin", "--delete", branch_name])
```

Cleanup is mandatory — it's a workflow step, not optional agent behavior. If cleanup fails (e.g., branch has unmerged work), it flags the issue in `devteam status`.

### PR Feedback Loop

The PR feedback cycle uses session resumption for token efficiency and zero-token polling for CI/CodeRabbit. Note: PR status checking is polling-based (every 60 seconds), not event-driven.

**Waiting for CI/CodeRabbit (zero tokens):**

```python
@DBOS.step()
def check_pr_status(pr_number: int) -> PRFeedback:
    """Single check — no loop. Called repeatedly from the workflow."""
    checks = subprocess.run(
        ["gh", "pr", "checks", str(pr_number), "--json",
         "name,state,conclusion"], capture_output=True)
    reviews = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--json",
         "reviews,comments,reviewRequests"], capture_output=True)
    return parse_pr_status(checks, reviews)
```

Polling loops belong at the workflow level, not inside steps. `DBOS.sleep()` is a workflow-level primitive.

**Fixing PR feedback (session resumption):**

```python
@DBOS.workflow()
def pr_lifecycle(task, worktree, branch):
    pr = open_pr(task, branch)
    session_id = None
    last_check_timestamp = None

    for iteration in range(MAX_FIX_ITERATIONS):
        while True:
            pr_feedback = check_pr_status(pr.number)
            if pr_feedback.ci_complete and pr_feedback.coderabbit_complete:
                break
            DBOS.sleep(60)

        if pr_feedback.all_green:
            break

        new_feedback = pr_feedback.since(last_check_timestamp)

        session_id, result = engineer_fix_pr(
            role=task.assigned_to,
            session_id=session_id,
            new_feedback=new_feedback,
            worktree=worktree
        )

        push_changes(worktree, branch)
        last_check_timestamp = now()
    else:
        escalate_to_human(
            f"PR #{pr.number} failed to go green after "
            f"{MAX_FIX_ITERATIONS} fix cycles.")

    em_merge(pr, task)
    cleanup_after_merge(task, worktree, branch)
```

Key design decisions:
- **Session resumption** — the agent remembers what it already fixed. No re-reading the PR, no warm-up.
- **Diff-only feedback** — each iteration only shows NEW failures and comments, not everything.
- **CodeRabbit comments are categorized** — errors first, warnings second, nitpicks deprioritized.
- **Circuit breaker** — max 5 iterations (configurable), then escalate to human.

---

## Concurrency Model

### Single Process, Shared Queue

All workflows execute within the single daemon process. A shared agent invocation queue enforces concurrency limits and supports priority:

```python
agent_queue = Queue("agent_invocations", worker_concurrency=3)
```

All jobs submit to the same queue. The system never runs more than N agents simultaneously regardless of how many jobs are active.

### Priority

Jobs and tasks support priority levels: `high`, `normal` (default), `low`.

- `devteam start --priority high` — job's tasks get queue priority
- `devteam prioritize W-1/T-3` — bump a specific task to high priority

High-priority tasks are dequeued before normal-priority tasks. Within the same priority, FIFO ordering applies.

### Multi-Job Operation

```bash
devteam start --spec app1-design.md --plan app1-plan.md    # Workflow W-1
devteam start --spec app2-design.md --plan app2-plan.md    # Workflow W-2
devteam start --issue https://github.com/org/repo/issues/42  # Workflow W-3
```

| Resource | Sharing | Notes |
|---|---|---|
| DBOS/SQLite | Shared, isolated by workflow ID | Steps don't collide |
| SurrealDB knowledge | Shared with scoping rules | Process knowledge shared; code knowledge project-scoped |
| Agent definitions | Shared | Templates, not instances |
| Git repos | Isolated per job | Each job gets its own worktrees |
| API rate limit | Shared | Global pause flag coordinates all workflows |

### Open-Source Issue Flow

`devteam start --issue https://github.com/org/repo/issues/123`:

1. Orchestrator checks push access to the target repo via `gh api repos/org/repo --jq .permissions.push`
2. **If push access exists** (already your fork or you're a collaborator): clone directly, work normally
3. **If no push access**: check if a fork already exists via `gh repo list --fork --json nameWithOwner`
   - Fork exists: clone the fork, set upstream remote to the original repo
   - No fork: auto-fork via `gh repo fork org/repo --clone`, set upstream remote
4. Planners research the project — conventions, test framework, contribution guidelines
5. CA designs the approach and creates a spec+plan
6. Normal execution flow continues — worktrees and branches are created in the fork
7. PR is opened from fork against the upstream repo (`gh pr create --repo org/repo`)
8. On job completion (or cancellation), the clone/fork is preserved by default. `devteam project remove <name>` cleans it up (the GitHub fork remains — fork deletion is manual).

---

## Execution Tracing

All execution trace data lives in DBOS/SQLite, captured as metadata on each `@DBOS.step()`. No separate trace store.

Each step records: agent role, step type (routing, decomposition, implementation, review, question), duration, token usage, decision made, and outcome.

### Trace Commands

| Command | Purpose |
|---|---|
| `devteam trace W-1` | Execution timeline for a job |
| `devteam trace W-1 --app api-service` | Trace just one app's work |
| `devteam trace W-1/T-2` | Full path for a specific task |
| `devteam trace --slow` | Highlight bottlenecks — longest steps, most revision cycles |
| `devteam trace --questions` | All questions, escalation paths, and resolutions |
| `devteam trace --export dot` | Export as Graphviz DOT for visual rendering |

### Example Output

```
devteam trace W-1/T-2

Task W-1/T-2: Frontend auth components

T-2.1  CEO routed            -> full_project path        420ms
T-2.2  CA decomposed         -> assigned to Team A       3.2s
T-2.3  Frontend implemented  -> first pass               45s
T-2.4  ? Question Q-7        -> "OAuth2+PKCE or magic link?"
       +- EM-A: could not resolve
       +- CEO: could not resolve
       +- Human: "OAuth2 + PKCE"              wait: 12m
T-2.5  Frontend revised      -> incorporated answer      38s
T-2.6  Backend peer review   -> approved w/ comments     8s
T-2.7  Frontend addressed    -> review comments          12s
T-2.8  EM-A review           -> approved                 5s
T-2.9  QA review             -> passed                   6s
T-2.10 Security review       -> passed (no auth issues)  4s

Total: 14m 36s (12m waiting on human answer)
```

---

## Operator Model

### What the Human Can Do

| Action | Command | Notes |
|---|---|---|
| Start work | `devteam start` | Multiple intake paths |
| See everything | `devteam status` | Filterable by job, app, team, agent, status |
| See task detail | `devteam status W-1/T-3` | Full context for a specific task |
| See questions | `devteam status --questions` | All pending questions needing answers |
| Answer questions | `devteam answer W-1/Q-3 "..."` | Unblocks the paused branch |
| Question detail | `devteam question W-1/Q-3` | Full context: spec excerpt, task summary, diff, suggested options |
| Steer work | `devteam comment W-1/T-2 "..."` | Feedback queued for next agent invocation |
| Approve merge | `devteam merge W-1/PR-12` | Only if merge is set to manual in config |
| Pause/resume | `devteam pause/resume W-1` | Job-scoped control |
| Cancel | `devteam cancel W-1` | Cancels job, cleans up worktrees/branches |
| Retry | `devteam retry W-1/T-3` | Retry a failed task |
| Prioritize | `devteam prioritize W-1/T-3` | Bump a task to high priority |
| Take over | `devteam takeover W-1/T-3` | Pause task, get worktree path for manual editing |
| Hand back | `devteam handback W-1/T-3` | Resume workflow from review stage after manual edits |
| Troubleshoot | `devteam trace W-1/T-2` | Full execution path for a task |
| Manage knowledge | `devteam knowledge ...` | Search, verify, redact, purge, export |
| Emergency stop | `devteam stop --force` | Kill everything immediately |
| Merge (manual mode) | `devteam merge W-1/PR-12` | Only if merge=manual in config. Verifies all checks passed before merging — will not force-merge a failing PR. |
| Register project | `devteam project add <path>` | Register a repo with the daemon |
| Remove project | `devteam project remove <name>` | Unregister and optionally clean up clones |
| Daemon control | `devteam daemon start/stop/status` | Manage the daemon process |
| Initialize | `devteam init` | First-time setup — creates ~/.devteam/ and templates |

### Manual Takeover Flow

When the human needs to step in and edit code directly:

1. `devteam takeover W-1/T-3` — pauses that task, outputs the worktree path
2. Human navigates to the worktree and makes their edits
3. Human commits their changes
4. `devteam handback W-1/T-3` — runs validation checks, then resumes the workflow

**Handback validation checks:**
- Worktree must have a clean working tree (no uncommitted changes)
- No history rewrites (force pushes) detected on the branch
- Changed files must be within the expected PR group's scope (warning if files outside scope were modified)
- If validation fails, `handback` reports the issues and does not resume — human must fix first

After validation passes, the workflow resumes from the review stage (peer review → EM → shared services). The task is marked as `human_edited` in the trace for auditability.

### Comment Cascading

When `devteam comment <parent-task-id> "change approach"` is run:

1. Comment attached to the task in DBOS state
2. All active child tasks identified
3. Feedback queued for those agents and delivered on their NEXT invocation
4. Agents that are currently mid-execution cannot receive injected feedback — feedback arrives when the orchestrator next invokes that agent
5. `devteam status` shows `feedback queued` on affected tasks

---

## CLI Interface

### Task Hierarchy & ID Scheme

```
Job (W-1, W-2...)
  └─ App (the repo / service / module being worked on)
       └─ Task (T-1, T-2... unique within a job)
            └─ Question (Q-1, Q-2... unique within a job)
```

Task and question IDs are job-scoped. Display formats: `W-{job}/T-{seq}` for tasks (e.g., `W-1/T-3`), `W-{job}/Q-{seq}` for questions (e.g., `W-1/Q-1`).

**Shorthand:** When only one job is active, the `W-{job}/` prefix is optional — `T-3` resolves unambiguously. When multiple jobs are active, either qualify with the prefix or use `devteam focus W-1` to set the default job context.

**`devteam focus`** writes the focused job ID to `~/.devteam/focus/<shell-pid>`. Each shell session has its own focus state. Different terminal windows can focus on different jobs. The focus file is cleaned up when the shell exits or when focus is cleared with `devteam focus --clear`.

App names are derived from the repository name or specified in the spec/plan.

### Status Output

```
[W-1] My App — 2 apps, started 2h ago — 22% complete (2/9 tasks)

  api-service (github.com/user/myapp-api)
    Application
      W-1/T-1  Backend API schema        [backend]    ✅ completed
      W-1/T-2  Auth middleware            [backend]    🔄 executing
      W-1/T-3  CI pipeline               [devops]     ⏳ queued (blocked by T-2)
    Systems
      W-1/T-4  Database migrations        [data]       ✅ completed
      W-1/T-5  Redis caching             [infra]      🔄 executing

  frontend (github.com/user/myapp-ui)
    Application
      W-1/T-6  Auth components           [frontend]   🔄 executing
      W-1/T-7  Dashboard views           [frontend]   ⏳ queued

  Review
      W-1/T-8  QA review                 waiting for code tasks
      W-1/T-9  Security review           waiting for code tasks

  PRs
    PR #12 feat/user-auth (api-service) — CI running
    PR #14 feat/auth-ui (frontend) — waiting for api PR

  ❓ Questions (1)
    W-1/Q-1  [backend/T-2]  "Redis session store or JWT?"
             → Escalated to you
             → devteam answer W-1/Q-1 "JWT with refresh tokens"
             → devteam question W-1/Q-1 (for full context)

[W-2] OSS Contribution — 1 app, started 30m ago — 0% complete (0/3 tasks)

  surrealdb (github.com/surrealdb/surrealdb)
    Planning
      W-2/T-1  Research target codebase   [planner]    🔄 executing

⏸ Rate limited — resumes in 1h 42m (only shown when active)
Agents running: 3/3
```

---

## Configuration

### `~/.devteam/config.toml` (global)

```toml
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
push = "auto"              # feature branches only
open_pr = "auto"
merge = "auto"             # only after all review gates pass
cleanup = "auto"           # only after confirmed merge
push_to_main = "never"     # hard block

[knowledge]
embedding_model = "nomic-embed-text"
surrealdb_path = "file://~/.devteam/knowledge"
cross_project_sharing = "layered"  # "layered" | "all" | "none"

[rate_limit]
default_backoff_seconds = 1800

[pr]
max_fix_iterations = 5
ci_poll_interval_seconds = 60

[git]
worktree_dir = ".worktrees"
```

### Per-Project `devteam.toml` (overrides)

Placed in a project's root directory to override global settings:

```toml
[project]
name = "myapp"
repos = ["github.com/user/myapp-api", "github.com/user/myapp-ui"]

[approval]
merge = "manual"    # override: require human approval for this repo

[execution]
test_command = "npm test"
lint_command = "npm run lint"
build_command = "npm run build"
merge_strategy = "squash"    # "squash" | "merge" | "rebase"
pr_template = ".github/pull_request_template.md"
```

Project execution commands are explicit configuration, not guessed by agents.

---

## Artifacts

Agent outputs are stored in predictable locations:

| Artifact | Location |
|---|---|
| Generated specs | `<project>/docs/specs/` |
| Research memos | `<project>/docs/research/` |
| ADRs | `<project>/docs/adr/` |
| Implementation code | In the worktree, committed to the feature branch |
| Trace exports | `~/.devteam/traces/W-{id}.dot` or `.json` |
| Knowledge exports | `~/.devteam/exports/knowledge-{date}.json` |
| Agent output logs | `~/.devteam/logs/W-{id}/T-{id}.log` |

All artifacts are files on disk, not locked inside the database.

---

## Security, Trust, and Safety

### Filesystem Boundaries

Agents are jailed to project directories via `cwd` in the Agent SDK. However, `cwd` is not a hard sandbox — agents with Bash access could theoretically navigate outside. Mitigation:

- Agent prompts explicitly instruct: "You may only work within your assigned project directory"
- The orchestrator's `Stop` hook validates that all file changes are within the expected project path. Changes outside the project directory cause the step to fail and escalate.
- `permission_mode="bypassPermissions"` is scoped to the tools listed in the agent's `.md` file — it does not grant arbitrary system access

### Network Boundaries

- Agents can make outbound HTTP requests via WebFetch/WebSearch (needed for research, documentation lookup)
- No inbound network access is granted to agents
- MCP servers run locally and are scoped per agent invocation

### Daemon API Security

The FastAPI daemon on `localhost:7432`:
- Binds to loopback only (`127.0.0.1`) — not accessible from the network
- No authentication in V1 (single-operator, local-only assumption)
- If multi-user support is added later, token-based auth would be required

### Secret Handling

- Agent output logs are stored in `~/.devteam/logs/` — operator should treat these as potentially containing sensitive output
- Knowledge extraction includes secret scanning — entries containing likely secrets (API keys, tokens, passwords) are rejected before persistence
- Trace exports may contain task descriptions and agent summaries — `devteam trace --export` should be treated as potentially sensitive
- Environment variables are NOT passed to agent invocations unless explicitly configured in `devteam.toml`

### Git Safety

- Agents never push to main/master — enforced by the orchestrator, not agent instructions
- `push_to_main = "never"` is a hard block in config with no override
- All work happens on feature branches in isolated worktrees
- Force push is never used by the orchestrator

### Audit Trail

Every agent invocation, every review decision, every human action (comment, answer, takeover, cancel) is recorded in the DBOS execution trace. `devteam trace` provides full auditability of what happened, who did it, and when.

---

## Installation & Prerequisites

### Prerequisites

- Python 3.11+
- Claude CLI authenticated (`claude --version`)
- Ollama running with nomic-embed-text (`ollama pull nomic-embed-text`)
- Git
- `gh` CLI authenticated

### Installation

```bash
pip install claude-devteam
devteam init    # creates ~/.devteam/, agent templates, config
```

### Project Registration

```bash
cd /path/to/your/project
devteam project add .    # registers this repo with the daemon
```

---

## End-to-End Workflows

### Workflow 1: Superpowers Spec+Plan Implementation

```bash
# You've built the spec and plan with Claude CLI + superpowers
devteam start --spec docs/specs/myapp-design.md --plan docs/plans/myapp-plan.md

# Check progress
devteam status

# Answer a question from the frontend engineer
devteam question W-1/Q-1           # see full context
devteam answer W-1/Q-1 "Use OAuth2 + PKCE"

# Team finishes, PRs merge, worktrees clean up automatically
devteam status                      # shows completed
```

### Workflow 2: Small Fix

```bash
# Clear scope, quick fix
devteam start --prompt "Fix the off-by-one error in pagination.py line 42"

# CEO routes directly to EM, single engineer fixes, peer reviews, merges
devteam status                      # usually done in minutes
```

### Workflow 3: Question Escalation with Human Answer

```bash
devteam status --questions

# See full context for a question
devteam question W-1/Q-3
# Output shows: spec excerpt, task summary, current diff,
# what the agent tried, suggested options with consequences

# Answer and resume
devteam answer W-1/Q-3 "Go with option A — JWT with refresh tokens"

# Branch resumes automatically
devteam status W-1/T-2              # now back to "executing"
```

---

## Future Enhancements (Out of Scope for V1)

- Adding Team C (third EM track) via configuration change
- Live query subscriptions for real-time `devteam status` updates
- SurrealDB Live Queries for inter-agent knowledge notifications
- MCP server scoping per agent role
- Multi-machine distribution (DBOS on Postgres, SurrealDB server mode)
- Web dashboard for execution visualization
- Slack/Discord notifications for pending questions
- Webhook-based PR status (replacing polling)
- Multi-user collaboration with role-based access
