# claude-devteam: Master Implementation Plan

> **For agentic workers:** This is the master coordination plan. Each phase references a detailed sub-plan. Execute phases in order — each phase must be complete and passing before starting the next (except where parallel execution is noted).

**Goal:** Build a durable AI development team orchestrator that replaces Paperclip's heartbeat model with event-driven workflows, enforcing process in code and building institutional knowledge that compounds across projects.

**Spec:** `docs/superpowers/specs/2026-03-25-claude-devteam-design.md`

---

## Development Workflow

All implementation work uses feature branches with pull requests. **PRs require human approval before merge** — no auto-merging to main.

1. **Branch per phase:** Create a feature branch (e.g., `feat/phase-1-core-daemon-cli`) for each phase.
2. **PR for review:** When a phase is complete with all tests passing, open a PR to `main`.
3. **CI must pass:** The PR workflow (`.github/workflows/pr.yml`) runs policy checks, ruff, pyright, and pytest. All must pass.
4. **CodeRabbit reviews:** CodeRabbit is configured (`.coderabbit.yaml`) and will review every PR automatically.
5. **Human approval required:** The PR waits for the human operator to review and approve. Do not merge without explicit approval.
6. **Squash merge:** Use squash merge for clean history on `main`.

---

## Architecture Overview

```
CLI (Typer) → Daemon (FastAPI) → DBOS Orchestrator → Agent SDK (Claude)
                                       ↕                    ↕
                                  SQLite (state)      SurrealDB (knowledge)
                                                           ↕
                                                     Ollama (embeddings)
```

**16 specialized agents** organized as a fixed consulting firm with two parallel execution tracks, coordinated by durable DBOS workflows that enforce review chains, handle questions, and manage the full git lifecycle.

---

## Phase Dependency Graph

```
Phase 1: Core Daemon & CLI (foundation)
    ↓
Phase 2: Agent Definitions & Invocation
    ↓
    ├──────────────────────────────┐
    ↓                              ↓
Phase 3: Workflow Engine      Phase 5: Knowledge System
    ↓                              ↓
Phase 4: Git Lifecycle        (merges into Phase 3 integration)
    ↓                              ↓
    └──────────────────────────────┘
                    ↓
Phase 6: Rate Limit & Concurrency
                    ↓
         Final Integration & Smoke Test
```

**Phases 3 and 5 can run in parallel** — they both depend on Phase 2 but are independent of each other. Phase 4 depends on Phase 3. Phase 6 depends on Phases 1+2 but integrates with all others.

---

## Phase 1: Core Daemon & CLI

**Plan:** `docs/superpowers/plans/2026-03-25-plan-1-core-daemon-cli.md`

**What it builds:**
- Python project scaffolding (pixi workspace, pyproject.toml)
- Entity models (Job, Task, Question, PRGroup) with state machines
- Configuration loading (global ~/.devteam/config.toml + per-project devteam.toml)
- Daemon process management (singleton lock, PID file, start/stop)
- FastAPI server on localhost:7432
- Typer CLI with all commands (stubs for unimplemented features)
- DBOS database scaffold
- ~/.devteam/ directory structure

**Deliverable:** `devteam init` creates the full directory structure. `devteam daemon start/stop/status` manages the process. `devteam status` shows an empty job list. All other commands exist as stubs.

**Tasks:** 10 | **Tests:** ~45

**Exit criteria:**
- [ ] `devteam init` creates ~/.devteam/ with all subdirectories
- [ ] `devteam daemon start` launches, `status` confirms running, `stop` kills it
- [ ] Entity models enforce valid state transitions
- [ ] Config loads and merges global + project settings
- [ ] All tests pass: `pixi run test`

---

## Phase 2: Agent Definitions & Invocation

**Plan:** `docs/superpowers/plans/2026-03-25-plan-2-agent-definitions-invocation.md`

**What it builds:**
- 16 agent template .md files with correct model tiering and tool access
- Agent registry (parses .md frontmatter, builds in-memory lookup)
- Agent invoker (wraps Claude Agent SDK query() calls)
- Structured output contracts (JSON schemas for all result types)
- Template management (devteam init copies to ~/.devteam/agents/, project add copies to .claude/agents/)

**Deliverable:** `devteam init` installs all 16 agent templates. The invoker can call any agent by role name with structured output.

**Tasks:** 8 | **Tests:** ~35

**Exit criteria:**
- [ ] All 16 agent .md files parse correctly with right models and tools
- [ ] Registry loads agents and returns correct config by role
- [ ] Invoker wraps query() with correct parameters (tested with mock)
- [ ] Structured output schemas validate all result types
- [ ] `devteam init` and `devteam project add` copy templates correctly
- [ ] All tests pass: `pixi run test`

---

## Phase 3: Workflow Engine

**Plan:** `docs/superpowers/plans/2026-03-25-plan-3-workflow-engine.md`

**Can start after:** Phase 2 complete
**Can run in parallel with:** Phase 5

**What it builds:**
- CEO routing workflow (classifies intake type, returns RoutingResult)
- CA decomposition workflow (produces task DAG with peer assignments)
- DAG execution engine (dependency-aware parallel dispatch)
- Task workflow (engineer → peer review → EM review with revision loop)
- Review chain enforcement (route-appropriate gates based on work type)
- Question escalation (type-based routing through chain of command)
- Job lifecycle management
- CLI bridge for start/comment/answer commands

**Deliverable:** `devteam start --spec X --plan Y` creates a job, routes it, decomposes into tasks, and executes the full workflow with review chains (using mocked agents during testing).

**Tasks:** 10 | **Tests:** ~50

**Exit criteria:**
- [ ] CEO routing correctly classifies all 5 intake types
- [ ] CA decomposition produces valid task DAGs with dependencies
- [ ] DAG engine runs independent tasks in parallel, respects dependencies
- [ ] Review chain enforced: peer_review() always before em_review()
- [ ] Questions pause the task branch, escalate through the chain
- [ ] Route-appropriate review applies correct gates per work type
- [ ] `devteam start/comment/answer` work end-to-end (mocked agents)
- [ ] All tests pass: `pixi run test`

---

## Phase 4: Git Lifecycle

**Plan:** `docs/superpowers/plans/2026-03-25-plan-4-git-lifecycle.md`

**Can start after:** Phase 3 complete

**What it builds:**
- Worktree management (create/remove/list)
- Branch lifecycle (create feature branches, delete after merge)
- Fork detection (push access check, existing fork lookup, auto-fork)
- PR creation, status checking, merge, close via gh CLI
- PR feedback loop (session resumption, diff-only, circuit breaker)
- Cleanup after merge and cancel
- Side-effect idempotent recovery
- Same-repo concurrency detection
- CLI commands: cancel, merge, takeover, handback

**Deliverable:** The full git lifecycle works end-to-end — from worktree creation through PR merge and cleanup. `devteam cancel` performs complete cleanup. Fork detection handles OSS contributions.

**Tasks:** 12 | **Tests:** ~55

**Exit criteria:**
- [ ] Worktrees create/remove correctly in temp git repos
- [ ] Feature branches create/delete (local + remote, mocked)
- [ ] Fork detection identifies push access, finds existing forks, auto-forks
- [ ] PR lifecycle: create → check status → merge → cleanup
- [ ] Feedback loop: categorizes CodeRabbit comments, circuit breaker works
- [ ] Cancel cleanup: closes PRs, deletes branches, removes worktrees
- [ ] All side-effect operations are idempotent on retry
- [ ] Takeover/handback validation works correctly
- [ ] All tests pass: `pixi run test`

---

## Phase 5: Knowledge System

**Plan:** `docs/superpowers/plans/2026-03-25-plan-5-knowledge-system.md`

**Can start after:** Phase 2 complete
**Can run in parallel with:** Phase 3

**What it builds:**
- SurrealDB connection and schema initialization (Docker container, ws://localhost:8000)
- Graph relationships (discovered, supersedes, requires, relates_to)
- Ollama embedding integration (nomic-embed-text, 768d)
- Vector search with scope filtering
- Knowledge boundaries (process=shared, code=project-scoped, secret scanning)
- Knowledge extraction (haiku agent, auto-tagging)
- Memory index generation (concise topic summaries)
- Knowledge query tool (query_knowledge for agents)
- Admin CLI commands (search, stats, verify, redact, purge, export)
- Materialized index event
- Decay and consolidation logic
- Graceful degradation

**Deliverable:** The knowledge system can extract learnings, store them with embeddings and graph relationships, generate memory indexes, and respond to agent queries. Admin commands provide full CRUD.

**Tasks:** 13 | **Tests:** ~60

**Exit criteria:**
- [ ] SurrealDB schema initializes in mem:// mode for tests
- [ ] Knowledge CRUD works with embeddings and graph relationships
- [ ] Vector search returns relevant results with scope filtering
- [ ] Secret scanning rejects entries containing likely secrets
- [ ] Knowledge extraction produces valid entries from agent output
- [ ] Memory index is concise and scoped by role/project
- [ ] query_knowledge returns relevant results with access_count tracking
- [ ] Admin commands work (search, verify, redact, purge, export)
- [ ] Graceful degradation: system works when SurrealDB is unavailable
- [ ] All tests pass: `pixi run test`

---

## Phase 6: Rate Limit & Concurrency

**Plan:** `docs/superpowers/plans/2026-03-25-plan-6-rate-limit-concurrency.md`

**Can start after:** Phase 2 complete (integrates with all other phases)

**What it builds:**
- DBOS Queue with worker_concurrency limit
- Global pause flag (SQLite-backed, cross-workflow coordination)
- Reactive rate limit handling (catch error, set pause, durable sleep, retry)
- Priority system (high/normal/low for jobs and tasks)
- Approval gates (configurable per action: commit, push, open_pr, merge, cleanup)
- Concurrency configuration
- CLI commands: prioritize, status display enhancements

**Deliverable:** The queue enforces concurrency limits. Rate limit errors trigger global pause with durable sleep. Priority ordering is respected. Approval gates block/allow actions per config.

**Tasks:** 11 | **Tests:** ~40

**Exit criteria:**
- [ ] Queue enforces max concurrent agents
- [ ] Global pause flag persists across connection close/reopen
- [ ] Rate limit errors trigger pause, sleep, and retry
- [ ] High-priority tasks dequeue before normal-priority
- [ ] Approval gates respect config (auto/manual/never per action)
- [ ] push_to_main is always NEVER regardless of config
- [ ] Status display shows rate limit state conditionally
- [ ] All tests pass: `pixi run test`

---

## Cross-Plan Implementation Notes

These items were identified during plan review and must be addressed during implementation:

1. **Structured output schemas** are defined ONLY in Plan 3's `src/devteam/orchestrator/schemas.py`. Plan 2's contracts.py task is removed. Plan 2's invoker returns raw dicts; Plan 3 validates them.

2. **Entity state enums** (JobStatus, TaskStatus, etc.) are defined ONLY in Plan 1's `src/devteam/models/entities.py`. Plan 3's schemas import from there — never redefine.

3. **AgentInvoker interface** is defined in Plan 2's `src/devteam/agents/invoker.py`. Plan 3's workflow modules import from there.

4. **Session ID tracking** for Claude Agent SDK session resumption: add `session_id: Optional[str]` field to the Task entity in Plan 1. Plan 2's invoker accepts/returns session IDs. Plan 4's PR feedback loop uses this for session resumption.

5. **Missing CLI commands** to add during integration:
   - `devteam resume` — Plan 1 stub + Plan 3 DBOS workflow replay logic
   - `devteam trace` — query DBOS step metadata, format as timeline. Implement in Plan 3 or as a separate integration task.
   - `devteam focus` — write/read `~/.devteam/focus/<shell-pid>`. Implement in Plan 1.

6. **Python version:** standardize on 3.13 across all plans and pyproject.toml.

7. **Plan 5 vector search** must use SurrealDB's native `<|N|>` KNN operator for HNSW index utilization, not plain ORDER BY.

8. **Plan 6 SQLite queue** is the V1 implementation. If DBOS Queue proves more appropriate during integration, swap at that time.

9. **CI and CodeRabbit** are pre-configured in the repo (`.github/workflows/pr.yml` and `.coderabbit.yaml`). Plan 1's scaffolding step must include `ruff` and `pyright` as dev dependencies so CI passes from the first PR.

10. **Dependency pins:** DBOS `>=2.16,<3`, SurrealDB Python SDK `>=1.0.8,<2` (server v3.0.4 via Docker), Claude Agent SDK `==0.1.50`. These are intentional pins — do not widen ranges.

---

## Final Integration

After all 6 phases are complete:

### Integration Checklist

- [ ] **End-to-end smoke test:** `devteam init` → `devteam project add` → `devteam start --spec X --plan Y` → verify job runs through routing → decomposition → task execution → review chains → PR lifecycle → cleanup (with mocked agent responses)
- [ ] **Knowledge integration:** verify that knowledge extraction runs after each agent step and memory index is injected into subsequent invocations
- [ ] **Rate limit integration:** verify that a simulated rate limit error pauses all workflows and resumes after sleep
- [ ] **Cancel integration:** verify that `devteam cancel` stops agents, closes PRs, deletes branches, removes worktrees
- [ ] **Question flow:** verify that a question pauses the branch, escalates through the chain, and resumes on answer
- [ ] **Multi-job:** start two jobs, verify they share the queue and rate limit state
- [ ] **Crash recovery:** kill the daemon mid-workflow, restart with `devteam resume`, verify it picks up where it left off
- [ ] **Full test suite:** all ~285 tests pass: `pixi run test`

### First Real Project Test

After integration tests pass, run the system against a small real project:

1. Write a spec+plan for a simple CLI tool (e.g., a JSON formatter)
2. `devteam start --spec spec.md --plan plan.md`
3. Monitor with `devteam status`
4. Verify the full flow works with real Claude invocations
5. Check that knowledge was extracted and persisted
6. Check that worktrees and branches were cleaned up after merge

---

## Summary

| Phase | Plan | Tasks | Tests | Depends On | Parallel With |
|---|---|---|---|---|---|
| 1. Core Daemon & CLI | plan-1-core-daemon-cli.md | 10 | ~45 | — | — |
| 2. Agent Definitions | plan-2-agent-definitions-invocation.md | 8 | ~35 | Phase 1 | — |
| 3. Workflow Engine | plan-3-workflow-engine.md | 10 | ~50 | Phase 2 | Phase 5 |
| 4. Git Lifecycle | plan-4-git-lifecycle.md | 12 | ~55 | Phase 3 | — |
| 5. Knowledge System | plan-5-knowledge-system.md | 13 | ~60 | Phase 2 | Phase 3 |
| 6. Rate Limit & Concurrency | plan-6-rate-limit-concurrency.md | 11 | ~40 | Phases 2, 3, 4 | — |
| **Total** | | **64** | **~285** | | |

**Build order (sequential path):** 1 → 2 → 3+5 (parallel) → 4 → 6 → Integration

**Optimization:** Phases 3 and 5 can be worked on simultaneously by different agents or in parallel worktrees, saving significant calendar time.
