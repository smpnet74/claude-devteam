# Runtime Wiring Design: DBOS-Centric Architecture

> **Purpose:** Replace the stopgap orchestration layer (in-memory JobStore, FastAPI daemon stubs, separate SQLite files) with DBOS durable workflows at the center. Wire all Phase 2-6 scaffolding into a working single-operator system with an interactive terminal experience.

---

## Problem Statement

After completing Phases 1-6 (1153 tests, all passing), every module works in isolation but the system does not function as a whole. The root cause: Phase 3 deferred DBOS integration and built plain Python functions with an in-memory JobStore. Every subsequent phase followed that pattern, creating three competing state management systems (DBOS unused, JobStore in-memory, separate SQLite files) and leaving all inter-module wiring undone.

### What exists but is not connected

| Module | Status | Gap |
|--------|--------|-----|
| Agent invoker | Builds SDK params, never called from real workflows | No adapter to real Claude Agent SDK |
| Rate limit wrapper | Library exists | Not called from any orchestrator workflow |
| Knowledge index | Builder exists | Not injected into agent prompts |
| query_knowledge tool | Schema defined | Not registered with SDK runtime |
| Status display | Formatters exist | Not wired into terminal output |
| Git lifecycle | Full library | Not called from workflow execution |
| Concurrency queue | SQLite-backed | Redundant with DBOS workflow concurrency |

---

## Architecture Overview

```
Operator Terminal (prompt_toolkit)
    ├── Log Panel (scrolling events)
    ├── Input Line (commands: /answer, /pause, /verbose, etc.)
    └── Event Loop
         ├── DBOS Workflow Engine (SQLite state)
         │    ├── execute_job workflow (parent)
         │    │    ├── route_intake step
         │    │    ├── decompose step
         │    │    └── execute_task workflows (children, parallel)
         │    │         ├── invoke_agent step (with retry)
         │    │         ├── peer_review step
         │    │         ├── em_review step
         │    │         └── create_pr step
         │    └── post_pr_review step
         ├── SurrealDB (knowledge, optional)
         ├── Ollama (embeddings, optional)
         └── Git/GitHub (worktrees, PRs)
```

**Single process.** The CLI hosts the async event loop. DBOS manages workflow durability. No daemon, no FastAPI, no separate background process.

---

## Operator Experience

### Starting a Job

```bash
devteam start --spec spec.md --plan plan.md
```

The CLI:
1. Loads and merges config (`~/.devteam/config.toml` + project `devteam.toml`)
2. Initializes DBOS via `DBOS(config={"name": "devteam", "system_database_url": "sqlite:///devteam_system.sqlite"})` then calls `DBOS.launch()` — creates/opens the DBOS SQLite database
3. Connects to SurrealDB and Ollama (graceful degradation if unavailable)
4. Starts the `execute_job` workflow via `DBOS.start_workflow_async()`
5. Enters the interactive terminal session

### Interactive Terminal

```
┌──────────────────────────────────────────────────┐
│ [W-1] Routing... full_project                    │
│ [W-1] Decomposing... 4 tasks created             │
│ [W-1/T-1] backend_engineer starting              │
│ [W-1/T-2] frontend_engineer starting             │
│ [W-1/T-1] Complete (3 files, 2 tests)            │
│ [W-1/T-2] QUESTION [Q-1] Redis or JWT?           │
│ [W-1/T-3] devops_engineer starting               │
│                                                  │
├──────────────────────────────────────────────────┤
│ devteam> _                                       │
└──────────────────────────────────────────────────┘
```

- **Log panel** (top): Scrolling events from all active workflows. Summary by default.
- **Input line** (bottom): Persistent, always available for commands.
- **Built with** `prompt_toolkit` for concurrent async input + output.

### Commands

| Command | Effect |
|---------|--------|
| `/answer Q-1 Use JWT` | Answer a question, resume paused branch |
| `/comment T-3 Use staging cluster` | Inject feedback into a task |
| `/pause` | Pause all work |
| `/resume` | Resume paused work |
| `/cancel` | Cancel everything, full cleanup |
| `/status` | Show detailed status snapshot |
| `/verbose T-1` | Stream full agent output for a task |
| `/quiet T-1` | Return to summary mode |
| `/priority T-3 high` | Change task priority |
| `/help` | List commands |

### Question Tiers

**Tier 1 — Blocking (ARCHITECTURAL, BLOCKED):**
- All agent work pauses
- Input line changes to `BLOCKING Q-1> _`
- Operator must respond before anything continues
- Used for: spec deviations, architectural decisions, unrecoverable blockers

**Tier 2 — Non-blocking (TECHNICAL, PRODUCT, PROCESS):**
- Highlighted in log: `QUESTION [Q-1] Redis or JWT?`
- Other task branches continue working
- If unanswered after configurable timeout, escalates visibility
- Used for: implementation choices, clarifications, trade-offs

### Crash Recovery

Two distinct cases:

**Case 1: Process crash (Ctrl+C, crash, terminal close)**

The workflow was actively running when the process died. DBOS automatically recovers pending workflows on `DBOS.launch()` — no explicit `resume_workflow` call is needed.

```bash
devteam resume W-1
```
- Calls `DBOS.launch()` — DBOS discovers the interrupted workflow and automatically resumes it from the last completed step (not from the beginning)
- Reconnects to SurrealDB/Ollama
- Re-enters the interactive terminal session for the recovered workflow
- Worktrees and branches are still on disk — work is not lost
- On resume, bootstrap scans for orphaned worktrees (not attached to any active DBOS workflow) and offers to clean them

**Case 2: Explicit resume after `/pause` (CLI was closed while paused)**

The workflow is still running (sleeping in the pause check loop). If the CLI was closed, the workflow was interrupted mid-sleep.

```bash
devteam resume W-1
```
- Calls `DBOS.launch()` — DBOS recovers the workflow (it was sleeping in the pause check loop, which is a durable `DBOS.sleep_async`)
- The workflow resumes in its paused state, waiting for a `control:resume` message
- The CLI reattaches the interactive session and shows "PAUSED" state
- Operator can then `/resume` to continue work

### Multi-Job Considerations

V1 is single-job — one interactive session at a time. Multi-job is deferred but the design accounts for it:

```bash
devteam start --spec foo.md    # Error if job already running:
                                # "Job W-1 is active. Use /cancel or devteam resume W-1"
devteam list                    # Show active/paused workflows from DBOS
devteam attach W-1              # Re-enter interactive session for a running job
devteam resume W-1              # Resume a paused/crashed workflow
```

The V1 enforcement: `bootstrap()` checks `DBOS.list_workflows(status="PENDING,RUNNING")` before starting a new one. If any exist, it refuses and tells the operator what to do.

### Event Polling Strategy

The interactive terminal polls DBOS for workflow events:
- **Polling interval:** 200ms (balances responsiveness with CPU usage)
- **Backpressure:** If DBOS emits events faster than the terminal renders, events are batched — the UI renders the latest batch on each tick rather than queuing unboundedly
- **Throttle:** `/verbose` mode streams agent output at terminal render speed, dropping intermediate chunks if the agent produces faster than the terminal can display

### Tier 1 Blocking Behavior

When the UI detects a Tier 1 question (from polling child events):
1. UI sends `control:pause` to parent and all active children — this is the same mechanism as `/pause`, so all workflows stop at their next step boundary
2. All event rendering pauses (events are buffered, not lost)
3. Commands in flight complete normally (a `/comment` already sent is delivered)
4. `/pause` becomes a no-op during Tier 1 (already effectively paused)
5. `/cancel` still works (operator can abort during a blocking question)
6. After the operator answers, UI sends the answer to the child and sends `control:resume` to parent + all children
7. Buffered events render and normal flow resumes

---

## Canonical Identifiers

**Job ID (`W-1`)** is a user-facing alias. The DBOS `workflow_id` is the real identifier — a UUID assigned by DBOS when the workflow starts. A mapping table in DBOS state links `W-1` → `workflow_uuid`. All internal routing uses the DBOS workflow_id. The CLI translates `W-1` to the UUID before any operation.

**Task ID (`T-1`)** maps to a child workflow. Each task's DBOS workflow_id is stored as a DBOS event on the parent workflow: `set_event("task:T-1:workflow_id", child_uuid)`.

**Question ID (`Q-T2-1`)** is a DBOS event key on the child workflow, scoped to its task. Each child mints its own IDs: `Q-{task_id}-{local_counter}` (e.g., `Q-T2-1`, `Q-T2-2`). This is inherently unique within the job since task IDs are unique and each child owns its own counter. No parent coordination needed.

Children raise questions by setting events on themselves: `await DBOS.set_event("question:Q-T2-1", question_data)`. The UI discovers questions by polling all child workflow events. Answers are sent from the UI directly to the child workflow via `await DBOS.send(destination_id=child_workflow_id, message=answer_text, topic="answer:Q-T2-1")`.

---

## Communication Model

### Workflow Responsibilities

**Parent workflow (`execute_job`):**
- Owns the job lifecycle (routing → decomposition → DAG → review → cleanup)
- Launches child workflows for each task
- Does NOT relay questions — the UI discovers questions by polling child events directly
- Emits sequenced log events for the terminal UI
- Receives `/pause`, `/resume`, and `/cancel` control messages via `DBOS.recv()`

**Child workflows (`execute_task`):**
- Own individual task execution (engineer → peer review → EM review → PR)
- Raise questions by setting events ON THEMSELVES: `await DBOS.set_event("question:Q-1", question_data)` — visible to the UI via `get_all_events_async(child_workflow_id)`
- Wait for answers via `await DBOS.recv(topic="answer:Q-1")` — the UI sends answers directly to the child
- Emit log events on themselves (the UI polls both parent and child events)
- Do NOT send questions to the parent — the UI discovers questions by polling child events directly

**Terminal UI:**
- Polls events from the parent workflow AND all active child workflows via `get_all_events_async()`
- Discovers questions by finding `question:*` keys in child workflow events
- Routes `/answer Q-1 text` by looking up which child workflow owns Q-1, then `await DBOS.send_async(destination_id=child_workflow_id, message=text, topic="answer:Q-1")`
- Routes `/comment T-1 text` by looking up T-1's child workflow_id, then `await DBOS.send_async(destination_id=child_workflow_id, message=text, topic="comment")`
- Routes `/pause` and `/resume` to the parent workflow AND all active children via `DBOS.send_async(topic="control:pause"/"control:resume")`
- Routes `/cancel` to the parent workflow

### Message Flow Diagram

```
Child workflow (T-2) raises a question:
  1. await DBOS.set_event("question:Q-1", {tier: 2, text: "Redis or JWT?", task: "T-2"})
  2. answer = await DBOS.recv(topic="answer:Q-1")  # blocks until answer arrives

Terminal UI (polling):
  3. Polls get_all_events_async("abc-123") → sees "question:Q-1" key appear
  4. Renders: "[W-1/T-2] QUESTION [Q-1] Redis or JWT?"

Operator types: /answer Q-1 Use JWT

Terminal UI:
  5. Looks up Q-1 → owned by child workflow for T-2 (UUID: abc-123)
  6. await DBOS.send_async(destination_id="abc-123", message="Use JWT", topic="answer:Q-1")

Child workflow (T-2):
  7. DBOS.recv(topic="answer:Q-1") unblocks with "Use JWT"
  8. Incorporates answer into next engineer prompt
  9. await DBOS.set_event("log:000047", "T-2 question Q-1 answered, resuming")

Terminal UI:
  10. Polls events, renders: "[W-1/T-2] Question Q-1 answered, resuming"
```

### Events vs Messages Split

DBOS provides two distinct communication primitives. The split is intentional:

| Primitive | API | Purpose | Direction |
|-----------|-----|---------|-----------|
| **Events** (`set_event` / `get_all_events`) | Key-value, pollable, visible to any caller | **Visibility** — letting observers see state | Workflow sets on itself |
| **Messages** (`send` / `recv`) | Topic-based, consumed once, blocks receiver | **Control handoff** — triggering action in another workflow | Sender to receiver |

**Events (for visibility — set by the workflow on itself):**
- Log entries (`log:000001`, `log:000002`, ...)
- Question metadata (`question:Q-1`, `question:Q-2`, ...)
- Task status (`task:T-1:status`, `task:T-2:status`, ...)
- PR status (`pr:T-1`, `pr:T-2`, ...)
- Pause state (`pause_state`)
- Cancel state (`cancel_state`)

**Messages (for control handoff — sent between workflows):**
- `/answer` → child workflow (`topic="answer:Q-1"`)
- `/comment` → child workflow (`topic="comment"`)
- `/pause`, `/resume` → parent + children (`topic="control:pause"`, `topic="control:resume"`)
- `/cancel` → parent workflow (`topic="control:cancel"`)
- `/priority` → parent workflow (`topic="control:priority"`)

---

## Terminal Event Transport

DBOS events are key-value snapshots, not an append-only log. `get_all_events_async(workflow_id)` returns `Dict[str, Any]` — a dict mapping event keys to their latest values. To build a scrolling log:

**Sequenced event keys:** Each workflow emits events with auto-incrementing keys:
- `log:000001` → `"Routing... full_project"`
- `log:000002` → `"Decomposing... 4 tasks created"`
- `log:000003` → `"T-1 backend_engineer starting"`

The UI tracks the last-seen sequence number per workflow and only renders new entries on each poll tick.

**Implementation:**

```python
# In each workflow, maintain an explicit monotonic counter (not DBOS.step_id,
# which is an internal implementation detail and not guaranteed to be sequential
# or stable across replays).
log_seq = 0

async def emit_log(message: str, level: str = "info"):
    nonlocal log_seq
    log_seq += 1
    await DBOS.set_event(f"log:{log_seq:06d}", {
        "message": message, "level": level, "timestamp": time.time()
    })

# In terminal UI:
async def poll_events(workflow_id: str, last_seen: int) -> list[dict]:
    all_events = await DBOS.get_all_events_async(workflow_id)
    new_events = {k: v for k, v in all_events.items()
                  if k.startswith("log:") and int(k.split(":")[1]) > last_seen}
    return [new_events[k] for k in sorted(new_events)]
```

---

## Pause Semantics

**`/pause` is an operator gate, not a DBOS cancel.**

`set_event_async` is only callable from within the workflow that owns the event, not from external code. The UI cannot directly set a pause event on the parent workflow. Instead, the UI sends a control message to the parent workflow, and the parent receives and acts on it internally.

**UI sends control messages:**
```python
# /pause command — UI sends message to parent workflow
await DBOS.send_async(destination_id=parent_workflow_id, message=True, topic="control:pause")

# /resume command — UI sends message to parent workflow
await DBOS.send_async(destination_id=parent_workflow_id, message=True, topic="control:resume")
```

**Parent workflow checks for control messages between steps:**
```python
# Inside the parent workflow, between major steps:
paused = False

async def check_pause():
    nonlocal paused
    # Check for pause/resume control messages (non-blocking)
    while True:
        msg = await DBOS.recv(topic="control:pause", timeout_seconds=0)
        if msg is None:
            break
        paused = True
    while True:
        msg = await DBOS.recv(topic="control:resume", timeout_seconds=0)
        if msg is None:
            break
        paused = False
    # If paused, block until resume
    while paused:
        await DBOS.set_event("pause_state", True)
        msg = await DBOS.recv(topic="control:resume", timeout_seconds=1)
        if msg is not None:
            paused = False
    await DBOS.set_event("pause_state", False)
```

Child workflows also call `check_pause()` before each major step — they receive their own `control:pause` / `control:resume` messages from the UI (the UI fans out the pause to all active children).

**Behavior:**
- `/pause` → UI sends `control:pause` to parent and all active children. Active steps finish, no new steps start, UI shows "PAUSED"
- `/resume` → UI sends `control:resume` to parent and all active children. Workflows continue from where they paused
- `/cancel` → triggers cleanup workflow (close PRs, delete branches, remove worktrees), terminates all child workflows
- Pause does NOT run cleanup. Cancel does.
- Child workflows are not individually pausable — pause is global per job.

---

## Cancellation Ordering

When the parent workflow receives a cancel control message, it follows a strict ordering to ensure clean shutdown:

1. **Set cancelling state:** Parent sets `await DBOS.set_event("cancel_state", "cancelling")` so the UI can display status
2. **Signal children:** Parent sends `control:cancel` to all active child workflows
3. **Wait for children to reach step boundaries:** Active child steps run to completion of their current step (agent invocation, git operation, etc.). Children check for `control:cancel` at the same points they check for pause — between steps. This avoids interrupting mid-operation.
4. **Run cleanup in order:** Once all children have stopped:
   - Close open PRs (set to "closed" state on GitHub)
   - Delete remote branches created by this job
   - Delete local branches created by this job
   - Remove worktrees created by this job
5. **Set final state:** Parent sets `await DBOS.set_event("cancel_state", "cancelled")`

Cleanup is idempotent — each operation handles "already done" gracefully. If the process crashes during cleanup, `DBOS.launch()` recovers and re-runs from the last completed cleanup step.

---

## Idempotency Rules

Every `@DBOS.step()` must be idempotent on retry. DBOS replays steps after crash recovery, so a step that ran partially before the crash may run again.

| Side Effect | Idempotency Strategy |
|-------------|---------------------|
| Worktree creation | `create_worktree()` already checks if worktree exists for branch, returns existing |
| Agent invocation | NOT idempotent — agent may produce different output. This is acceptable: the step result is stored by DBOS, so replayed steps use the stored result, not a new invocation |
| Knowledge extraction | Idempotent — `create_entry()` with same content is a no-op (or upsert by content hash) |
| Git commit | Check if HEAD already has the expected changes before committing |
| Git push | `push` with same content is a no-op (remote already has the commits) |
| PR creation | `create_pr()` already calls `find_existing_pr()` first — returns existing PR |
| PR merge | `merge_pr()` already handles "already merged" as a no-op |
| Worktree cleanup | `remove_worktree()` already handles "doesn't exist" as a no-op |
| Branch deletion | `delete_local_branch()` and `delete_remote_branch()` already handle "doesn't exist" |

**Key insight:** DBOS stores the return value of each completed step. On replay, completed steps return the stored value without re-executing. Only the step that was interrupted needs to re-run. So most idempotency concerns are already handled by DBOS's replay mechanism. The explicit idempotency above is a safety net for the edge case where a step partially executed before the crash.

---

## DBOS Queue Evaluation

The spec uses manual parent-managed concurrency (tracking active child workflow handles) instead of DBOS Queue. Justification:

**Why not DBOS Queue for agent concurrency:**
- Our DAG has dependency ordering — tasks must wait for their dependencies, not just a slot. DBOS Queue is FIFO or priority-ordered, but doesn't understand dependency graphs.
- The parent workflow already manages the DAG — it knows which tasks are ready, which are blocked, and which slots are available. Adding a queue between the parent and child workflows adds indirection without value.
- Priority changes (`/priority T-3 high`) need to affect the DAG scheduler's next-task selection, which is easier when the parent owns the decision directly.

**Where DBOS Queue could help (future):**
- Multi-job concurrency — if two jobs share a global agent slot pool, a DBOS Queue could manage the shared pool. Deferred to V2.

---

## query_knowledge Tool Registration

The Claude Agent SDK allows registering custom tools that agents can call during execution. `query_knowledge` is registered as follows:

```python
# In invoke_agent_step, when building ClaudeAgentOptions:
options = ClaudeAgentOptions(
    model=defn.model,
    system_prompt=full_system_prompt,
    allowed_tools=list(defn.tools),  # includes "query_knowledge"
    permission_mode="default",
    cwd=worktree_path,
    output_format=get_output_schema(role),
    # Custom tool definitions:
    custom_tools=[query_knowledge_tool.tool_definition()],
)
```

The `custom_tools` field passes the JSON schema definition to the SDK, which makes it available for the agent to call. When the agent calls `query_knowledge(query="...", scope="...")`, the SDK invokes our `QueryKnowledgeTool.query()` method and returns the result to the agent.

**Note:** The exact SDK field name for custom tool registration (`custom_tools`, `tools`, or `tool_definitions`) must be verified against the actual Claude Agent SDK documentation at implementation time. The schema shape from `tool_definition()` is already correct.

### Knowledge Context Size Limits

To prevent agent prompt bloat from injected knowledge:
- Memory index is capped at 50 lines / ~3KB (already enforced by `MemoryIndexBuilder` — max 10 topics per section)
- `query_knowledge` results are capped at 5 entries by default, configurable via the tool's `limit` parameter (max 50)
- Total injected context (system_prompt + knowledge index) should not exceed 20% of the model's context window. The bootstrap sets a `max_knowledge_tokens` based on the model tier.

### Degraded Knowledge Behavior

When SurrealDB and/or Ollama are unavailable (detected during bootstrap):
- `query_knowledge` tool is **OMITTED** from `allowed_tools` entirely — not registered with the SDK, so agents cannot call it. This avoids agents attempting calls that would fail.
- Memory index returns the empty template (already handled by `build_memory_index_safe` — returns `""` on failure)
- Bootstrap logs: `"Knowledge system unavailable — agents will work without knowledge context"`
- This is why `invoke_agent_step` conditionally includes `query_knowledge` in the tools list only when `knowledge_store is not None`

---

## Adaptation vs Rewrite

The spec's "modified" category means **adapt the existing tested logic**, not rewrite from scratch. Specifically:

| Module | Adaptation approach |
|--------|-------------------|
| `orchestrator/routing.py` | Add `async` keyword + `@DBOS.step()` decorator. The `route_intake()` logic, `classify_intake()`, and `build_routing_prompt()` remain unchanged. |
| `orchestrator/decomposition.py` | Add `async` + `@DBOS.step()`. The `decompose()`, `validate_decomposition()`, and `assign_peer_reviewers()` logic stays. |
| `orchestrator/task_workflow.py` | Restructure as `@DBOS.workflow()`. The revision loop, review chain ordering, and feedback formatting logic are preserved — the control flow wrapper changes. |
| `orchestrator/review.py` | Add `async` + `@DBOS.step()`. `get_review_chain()`, `execute_post_pr_review()`, and gate logic stay. |
| `orchestrator/escalation.py` | Add `async`. Replace manual question tracking with `DBOS.send()`/`recv()`. The escalation path logic and attempt_resolution logic stay. |
| `orchestrator/dag.py` | Replace `DAGExecutor` with DBOS parent workflow pattern. The DAG state tracking, ready-task detection, and dependency logic are preserved. |
| `agents/invoker.py` | Rewrite `invoke()` as `@DBOS.step()`. The `build_query_params()` logic is preserved. `QueryOptions` mapping is preserved. The actual SDK call wrapper is new. |

**Principle:** Convert boundaries first (sync→async, add decorators), behavior second (only change control flow where DBOS requires it). Run existing unit tests after each conversion to verify logic preservation.

---

## Test Migration

| Test Suite | Action | Reason |
|-----------|--------|--------|
| `tests/test_models.py` | Keep unchanged | Entity models don't change |
| `tests/test_state.py` | Keep unchanged | State machines don't change |
| `tests/test_config.py` | Keep unchanged | Config loading doesn't change |
| `tests/test_database.py` | **Delete** | daemon/database.py removed |
| `tests/test_daemon.py` | **Delete** | Daemon is removed |
| `tests/test_init_agents.py` | Keep unchanged | Agent initialization tests don't change |
| `tests/test_project_agents.py` | Keep unchanged | Project agent tests don't change |
| `tests/test_cli.py` | **Adapt** | Job commands change to use DBOS workflows |
| `tests/test_integration.py` | **Replace** | New end-to-end tests with DBOS |
| `tests/agents/*` | Keep unchanged | Agent library tests don't change |
| `tests/orchestrator/test_schemas.py` | Keep unchanged | Schema tests don't change |
| `tests/orchestrator/test_routing.py` | **Adapt** | Add async, mock DBOS context |
| `tests/orchestrator/test_decomposition.py` | **Adapt** | Add async, mock DBOS context |
| `tests/orchestrator/test_task_workflow.py` | **Adapt** | Restructure for DBOS workflow pattern |
| `tests/orchestrator/test_review.py` | **Adapt** | Add async, mock DBOS context |
| `tests/orchestrator/test_escalation.py` | **Adapt** | Add async, use DBOS send/recv mocks |
| `tests/orchestrator/test_dag.py` | **Adapt** | Replace DAGExecutor tests with DBOS parent workflow tests |
| `tests/orchestrator/test_jobs.py` | **Replace** | JobStore tests become DBOS workflow tests |
| `tests/orchestrator/test_cli_bridge.py` | **Delete** | cli_bridge is removed |
| `tests/orchestrator/test_integration.py` | **Replace** | New DBOS-based integration tests |
| `tests/git/*` | Keep unchanged | Git library tests don't change |
| `tests/knowledge/*` | Keep unchanged | Knowledge library tests don't change |
| `tests/concurrency/test_queue.py` | **Delete** | SQLite queue removed |
| `tests/concurrency/test_durable_sleep.py` | **Delete** | Replaced by DBOS.sleep_async |
| `tests/concurrency/test_rate_limit.py` | **Adapt** | Remove SQLite pause flag tests, keep error parsing |
| `tests/concurrency/test_rate_limit_invoke.py` | **Delete** | Replaced by in-step retry logic in invoke_agent_step |
| `tests/concurrency/test_priority.py` | Keep unchanged | Priority logic doesn't change |
| `tests/concurrency/test_approval.py` | Keep unchanged | Approval logic doesn't change |
| `tests/concurrency/test_config.py` | Keep unchanged | Config loading doesn't change |
| `tests/concurrency/test_status_display.py` | Keep unchanged | Formatter logic doesn't change |
| `tests/concurrency/test_integration.py` | **Replace** | New DBOS-based integration |
| `tests/concurrency/test_cli_priority.py` | **Adapt** | Update for new CLI structure |
| `tests/cli/test_git_commands.py` | Keep unchanged | Git CLI doesn't change |
| `tests/cli/test_knowledge_cmd.py` | Keep unchanged | Knowledge CLI doesn't change |
| `tests/cli/test_concurrency_cmd.py` | **Adapt** | Update for new CLI structure |

---

## Daemon Deprecation

V1 removes the daemon from the runtime path. Specific handling:

| Item | Action |
|------|--------|
| `daemon/server.py` | Delete source file |
| `daemon/process.py` | Delete source file |
| `daemon/database.py` | Delete source file |
| `daemon/__init__.py` | Keep empty (package may be reused in V2) |
| `DaemonConfig` in `settings.py` | Keep for now — the `port` field is harmless and may be useful for V2 |
| `daemon start/stop/status` CLI commands | Remove from `cli/main.py` registration. Delete `cli/commands/daemon_cmd.py` |
| `tests/test_daemon.py` | Delete |
| `fastapi` dependency | Move from runtime to dev-only in `pyproject.toml` |
| `uvicorn` dependency | Move from runtime to dev-only in `pyproject.toml` |

## Workflow Architecture

### Parent Workflow: execute_job

```python
@DBOS.workflow()
async def execute_job(job_id: str, spec: str, plan: str, project_name: str, config: dict) -> JobResult:
    # Check for pause/cancel between every major step
    await check_control_messages()

    # Step 1: Route intake
    routing = await route_intake_step(spec, plan)
    await emit_log(f"Routed as {routing.path.value}")
    await check_control_messages()

    # Step 2: Handle path-specific control flow
    if routing.path == RoutePath.RESEARCH:
        # Research: single agent call, no decomposition or PR
        result = await invoke_agent_step(
            role="planner_researcher_a",
            prompt=build_research_prompt(spec, plan),
            worktree_path=None,
            project_name=project_name,
        )
        return JobResult(status="completed", research_result=result)

    elif routing.path == RoutePath.SMALL_FIX:
        # Small fix: create single-task decomposition inline, skip full DAG
        task = TaskDecomposition(
            id="T-1",
            assigned_to=routing.recommended_role or "backend_engineer",
            description=spec,
            dependencies=[],
        )
        decomposition = Decomposition(tasks=[task], peer_assignments={})
        parent_workflow_id = DBOS.workflow_id
        handle = await DBOS.start_workflow_async(
            execute_task, job_id, parent_workflow_id, task, decomposition, project_name, config
        )
        result = await handle.get_result()
        await cleanup_step(job_id)
        return JobResult(status="completed")

    # Full project / OSS contribution: decompose and execute DAG
    decomposition = await decompose_step(spec, plan, routing)
    await emit_log(f"Decomposed into {len(decomposition.tasks)} tasks")

    # Step 3: Execute tasks via child workflows
    parent_workflow_id = DBOS.workflow_id
    task_handles = {}
    for task in get_ready_tasks(decomposition):
        handle = await DBOS.start_workflow_async(
            execute_task, job_id, parent_workflow_id, task, decomposition, project_name, config
        )
        task_handles[task.id] = handle

    # Wait for all tasks, launching new ones as dependencies complete
    await manage_dag_execution(job_id, decomposition, task_handles, project_name, config)

    # Step 4: Post-PR review
    await run_post_pr_review_step(job_id, decomposition)

    # Step 5: Cleanup
    await cleanup_step(job_id)

    return JobResult(status="completed")
```

### Child Workflow: execute_task

```python
@DBOS.workflow()
async def execute_task(job_id: str, parent_workflow_id: str, task: TaskDecomposition, decomposition: Decomposition, project_name: str, config: dict) -> TaskResult:
    # Create isolated worktree
    worktree = await create_worktree_step(task)

    max_revisions = config.get("pr", {}).get("max_fix_iterations", 3)
    revision_count = 0
    revision_feedback: str | None = None

    while revision_count <= max_revisions:
        # Check for operator comments before each agent invocation
        comments = []
        while True:
            msg = await DBOS.recv(topic="comment", timeout_seconds=0)
            if msg is None:
                break
            comments.append(msg)

        # Check for pause control messages
        await check_pause()

        # Build prompt, incorporating any operator comments
        prompt = build_task_prompt(task, revision_feedback)
        if comments:
            prompt += "\n\nOperator feedback:\n" + "\n".join(comments)

        # Invoke engineer agent
        impl = await invoke_agent_step(
            role=task.assigned_to,
            prompt=prompt,
            worktree_path=worktree,
            project_name=project_name,
        )

        # Handle questions — set event on self (visible to UI), wait for answer via recv
        if impl.status in ("needs_clarification", "blocked"):
            question = create_question(impl, task)
            await DBOS.set_event(f"question:{question.id}", question.model_dump())

            # Wait for operator answer — UI sends directly to this child workflow
            answer = await DBOS.recv(topic=f"answer:{question.id}")
            revision_feedback = f"Answer to your question: {answer}"
            continue

        # Peer review — look up reviewer from decomposition peer_assignments
        peer_reviewer = decomposition.peer_assignments.get(task.id)
        if not peer_reviewer:
            await emit_log(f"No peer reviewer assigned for {task.id}, skipping peer review")
        else:
            review = await invoke_agent_step(
                role=peer_reviewer,
            prompt=build_review_prompt(impl, task),
            worktree_path=worktree,
            project_name=project_name,
        )

        if review.needs_revision:
            revision_feedback = format_revision_feedback(review)
            revision_count += 1
            continue

        # EM review
        em_review = await invoke_agent_step(...)
        if em_review.needs_revision:
            revision_feedback = format_revision_feedback(em_review)
            revision_count += 1
            continue

        # Approved — create PR
        pr = await create_pr_step(task, worktree)
        await DBOS.set_event(f"pr:{task.id}", pr.model_dump())  # visible to UI via event polling
        return TaskResult(status="completed", pr=pr)

    # Max revisions exceeded
    return TaskResult(status="max_revisions_exceeded")
```

### Agent Invocation Step

```python
@DBOS.step(retries_allowed=False)
async def invoke_agent_step(role: str, prompt: str, worktree_path: str, project_name: str, max_retries: int = 3) -> BaseModel:
    # 1. Build knowledge context
    knowledge_index = await build_memory_index_safe(knowledge_store, project_name)

    # 2. Get agent definition from registry
    defn = agent_registry.get(role)

    # 3. Build full prompt with knowledge injection
    full_system_prompt = f"{defn.prompt}\n\n{knowledge_index}"

    # 4. Build SDK options — omit query_knowledge if knowledge system unavailable
    tools = list(defn.tools)
    if knowledge_store is not None:
        tools.append("query_knowledge")

    # 5. Call Claude Agent SDK with manual rate-limit retry
    #    DBOS step retry uses static decorator args and cannot honor dynamic
    #    retry-after headers from Claude. We handle retry ourselves inside the step.
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            result = await claude_sdk_query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    model=defn.model,
                    system_prompt=full_system_prompt,
                    allowed_tools=tools,
                    permission_mode="default",
                    cwd=worktree_path,
                    output_format=get_output_schema(role),
                ),
            )
            break
        except RateLimitError as e:
            last_error = e
            backoff_seconds = parse_retry_after(e) or (60 * (2 ** attempt))
            await DBOS.sleep_async(backoff_seconds)
        except AgentInvocationError:
            raise  # Auth errors, context exceeded, model unavailable — propagate immediately
    else:
        raise last_error  # Max retries exceeded on rate limit

    # 6. Extract knowledge from response (best-effort, don't fail the step)
    try:
        await extract_knowledge_from_response(result, project_name, role)
    except Exception:
        pass  # Knowledge extraction failure should not block task completion

    # 7. Return typed result
    return parse_agent_result(role, result)
```

**`claude_sdk_query` definition:**

```python
async def claude_sdk_query(prompt: str, options: ClaudeAgentOptions) -> AgentResponse:
    """Call the Claude Agent SDK's query API.

    This is a thin wrapper around the real SDK. It:
    1. Imports claude_agent_sdk lazily (allows tests without SDK installed)
    2. Calls query(prompt=prompt, options=options)
    3. Iterates the response stream to find the ResultMessage
    4. Checks is_error and structured_output
    5. Returns the parsed AgentResponse

    The calling code in invoke_agent_step handles rate-limit retry —
    this function just makes the call and raises on failure.
    """
    from claude_agent_sdk import query

    async for message in query(prompt=prompt, options=options):
        if hasattr(message, 'result'):
            if message.is_error:
                raise AgentInvocationError(message.result)
            return AgentResponse(
                result=message.structured_output or message.result,
                session_id=getattr(message, 'session_id', None),
            )
    raise AgentInvocationError("No ResultMessage received from SDK")
```

Rate limit retry is handled INSIDE the step body: catch `RateLimitError`, parse the `retry-after` header, sleep with `await DBOS.sleep_async(seconds)`, then retry within the loop. The step has `retries_allowed=False` — DBOS does not retry it automatically. This allows honoring dynamic backoff values from Claude's API response headers.

---

## Bootstrap Sequence

```python
# orchestrator/bootstrap.py

async def bootstrap(spec: str, plan: str) -> WorkflowHandle:
    """Initialize all services and start the job workflow."""

    # 1. Load config
    config = load_and_merge_config()

    # 2. Initialize DBOS
    # DBOS v2.16+ uses SQLite by default (no PostgreSQL required).
    # Canonical database path: ~/.devteam/devteam_system.sqlite
    # Configured explicitly to avoid ambiguity between ./dbos.sqlite and ~/.devteam/
    DBOS(config={
        "name": "devteam",
        "system_database_url": "sqlite:///devteam_system.sqlite",
    })
    DBOS.launch()

    # 3. Connect knowledge store (graceful degradation)
    knowledge_store = None
    try:
        knowledge_store = KnowledgeStore(config.knowledge.surrealdb_url)
        await knowledge_store.connect(
            username=config.knowledge.surrealdb_username,
            password=config.knowledge.surrealdb_password,
        )
    except ConnectionError:
        logger.warning("Knowledge store unavailable — proceeding without knowledge")

    # 4. Connect embedder (graceful degradation)
    embedder = None
    try:
        embedder = create_embedder_from_config(config.knowledge)
        if not await embedder.is_available():
            embedder = None
    except Exception:
        logger.warning("Ollama unavailable — proceeding without embeddings")

    # 5. Load agent registry
    registry = AgentRegistry.load(get_bundled_templates_dir())

    # 6. Register services as module-level singletons
    # DBOS steps are plain decorated functions — they access shared services
    # through module globals, not dependency injection. This is the standard
    # DBOS pattern (similar to Flask's app context or FastAPI's Depends).
    set_knowledge_store(knowledge_store)
    set_embedder(embedder)
    set_agent_registry(registry)
    set_config(config)

    # 7. Start the workflow
    handle = await DBOS.start_workflow_async(
        execute_job,
        job_id=generate_job_id(),
        spec=spec,
        plan=plan,
        project_name=config.general.project_name,
        config=config.model_dump(),
    )

    return handle
```

---

## Interactive Terminal Implementation

```python
# cli/interactive.py

async def run_interactive_session(handle: WorkflowHandle):
    """Run the interactive terminal UI for a workflow."""

    app = create_prompt_toolkit_app(handle)

    # Two concurrent tasks:
    # 1. Poll DBOS events and render to log panel
    # 2. Read operator input and dispatch commands

    async with asyncio.TaskGroup() as tg:
        tg.create_task(poll_and_render_events(handle, app))
        tg.create_task(read_and_dispatch_input(handle, app))
```

**Event polling:**
- Uses `DBOS.get_all_events_async(workflow_id)` to fetch new events
- Events are typed: `routed`, `decomposed`, `task_started`, `task_completed`, `question`, `pr_created`, `error`
- Each event type has a formatter that produces the log line
- `/verbose` mode switches a task's formatter to stream the full agent response

**Command dispatch:**
- `/answer Q-1 text` → `await DBOS.send_async(destination_id=child_workflow_id, message=text, topic="answer:Q-1")`
- `/pause` → `await DBOS.send_async(destination_id=parent_workflow_id, message=True, topic="control:pause")` — also fans out to all active child workflows
- `/resume` → `await DBOS.send_async(destination_id=parent_workflow_id, message=True, topic="control:resume")` — also fans out to all active child workflows
- `/cancel` → cancel workflow + trigger cleanup workflow
- `/priority T-3 high` → `await DBOS.send_async(destination_id=parent_workflow_id, message={"task_id": "T-3", "priority": "high"}, topic="control:priority")`
- `/status` → read DBOS workflow status + child workflow statuses

**Tier 1 blocking (= Tier 2 question + global pause):**
- When the UI detects a Tier 1 question (from polling child events where `question_data.tier == 1`), it:
  - Sends `control:pause` to the parent workflow and all active child workflows (same as `/pause`)
  - Pauses event rendering
  - Changes the input prompt to `BLOCKING Q-1> `
  - Waits for the operator's response
  - Sends the answer via `DBOS.send_async(destination_id=child_workflow_id, ...)`
  - Sends `control:resume` to the parent workflow and all active child workflows (same as `/resume`)
  - Resumes event rendering
- This ensures ALL agent work pauses when a Tier 1 question is raised, not just the child that raised it

---

## Concurrency Model

**Agent concurrency** is controlled by `config.general.max_concurrent_agents` (default 3). The parent workflow's DAG execution loop limits how many child task workflows are running simultaneously by tracking active handles and only launching new ones when a slot opens.

```python
async def manage_dag_execution(job_id, decomposition, initial_handles, project_name, config):
    max_concurrent = config["general"]["max_concurrent_agents"]
    active = dict(initial_handles)  # task_id -> handle
    completed = {}
    priority_overrides = {}  # task_id -> Priority

    while active or has_pending_tasks(decomposition, completed):
        # Check for pause control messages
        await check_pause()

        # Check for priority override messages (only affect not-yet-started tasks)
        while True:
            msg = await DBOS.recv(topic="control:priority", timeout_seconds=0)
            if msg is None:
                break
            # msg = {"task_id": "T-3", "priority": "high"}
            priority_overrides[msg["task_id"]] = Priority(msg["priority"])

        # Wait for any active task to complete
        if active:
            done_id, result = await wait_for_any(active)
            completed[done_id] = result
            del active[done_id]

        # Launch newly ready tasks up to concurrency limit
        # Priority overrides affect ordering of ready tasks
        ready = get_ready_tasks(decomposition, completed)
        ready = sorted(ready, key=lambda t: priority_overrides.get(t.id, t.priority), reverse=True)
        for task in ready:
            if len(active) >= max_concurrent:
                break
            if task.id not in active and task.id not in completed:
                handle = await DBOS.start_workflow_async(
                    execute_task, job_id, DBOS.workflow_id, task, decomposition, project_name, config
                )
                active[task.id] = handle
```

**`/priority` command:** The UI sends priority changes to the parent workflow: `await DBOS.send_async(destination_id=parent_workflow_id, message={"task_id": "T-3", "priority": "high"}, topic="control:priority")`. Priority changes only affect not-yet-started tasks — tasks already running are unaffected. The parent checks for priority messages before selecting the next task to launch.

**Rate limiting** is handled by custom retry logic INSIDE `invoke_agent_step`. The step has `retries_allowed=False` — DBOS does not retry it automatically. When the Claude API returns a rate limit error, the step catches `RateLimitError`, parses the `retry-after` header via `parse_retry_after()` (from `concurrency/rate_limit.py`), sleeps with `await DBOS.sleep_async(seconds)` (durable — survives crash), then retries within the step body. This allows honoring dynamic backoff values from the API. No global pause flag needed.

**Approval gates** remain as configured in `config.approval`. Before side-effecting git operations (commit, push, open_pr, merge), the workflow checks `check_approval(action, config)`. If `manual`, it emits a Tier 1 question. If `never`, it skips. If `auto`, it proceeds. `push_to_main` is always `never`.

---

## What Gets Removed

| Module | Reason |
|--------|--------|
| `daemon/server.py` | No FastAPI daemon in V1 |
| `daemon/process.py` | No PID file management needed |
| `daemon/database.py` | DBOS manages its own SQLite |
| `orchestrator/cli_bridge.py` | JobStore replaced by DBOS, commands handled by terminal UI |
| `orchestrator/jobs.py` | Job dataclass and execute_job replaced by DBOS workflow |
| `concurrency/queue.py` | DBOS workflow concurrency replaces SQLite queue |
| `concurrency/durable_sleep.py` | `DBOS.sleep_async()` replaces custom durable sleep |
| `concurrency/rate_limit.py` (pause flag functions) | Pause coordination replaced by DBOS events |
| `concurrency/invoke.py` | Custom in-step retry replaces the external retry wrapper |

> **Keep in `rate_limit.py`:** `_parse_reset_seconds()` and `handle_rate_limit_error()` -- error parsing is still needed for the custom retry logic inside `invoke_agent_step` to parse `retry-after` headers and determine backoff duration.

## What Gets Kept (No Changes)

| Module | Why |
|--------|-----|
| `git/*` (all 8 modules) | Library code, works with any caller |
| `knowledge/*` (all 6 modules) | Library code, called from DBOS steps |
| `agents/contracts.py` | Structured output types, unchanged |
| `agents/registry.py` | Agent template loading, unchanged |
| `agents/template_manager.py` | Template copying, unchanged |
| `models/entities.py` | Entity models and Priority enum, unchanged |
| `models/state.py` | State transitions, unchanged |
| `config/settings.py` | **Moved to Modified** — see below |
| `concurrency/priority.py` | Priority ordering, used by DAG scheduler |
| `concurrency/approval.py` | Approval gates, called from workflow steps |
| `concurrency/config.py` | Config loading for concurrency settings |
| `concurrency/status_display.py` | Formatters, called from terminal UI |

## What Gets Modified

| Module | Change |
|--------|--------|
| `orchestrator/routing.py` | Convert to async, add `@DBOS.step()` |
| `orchestrator/decomposition.py` | Convert to async, add `@DBOS.step()` |
| `orchestrator/task_workflow.py` | Rewrite as `@DBOS.workflow()`, use `send`/`recv` for questions |
| `orchestrator/review.py` | Convert to async, add `@DBOS.step()` |
| `orchestrator/escalation.py` | Convert to async, route through `DBOS.send()` for tier-based questions |
| `orchestrator/dag.py` | Replace with DBOS parent/child workflow pattern |
| `agents/invoker.py` | Rewrite as `@DBOS.step()`, call real SDK, inject knowledge |
| `cli/commands/job_cmd.py` | `start` launches DBOS workflow + interactive session |
| `config/settings.py` | Add DBOS database path config, polling interval config, interactive UI settings |
| `cli/main.py` | Wire interactive session, remove daemon commands |

## What Gets Added

| Module | Purpose |
|--------|---------|
| `orchestrator/bootstrap.py` | Config → DBOS.launch() → connect services → start workflow |
| `cli/interactive.py` | prompt_toolkit session (log panel + input line) |
| `orchestrator/events.py` | Event types and formatters for workflow → UI communication |

---

## Dependencies

**Add:**
- `prompt_toolkit` — interactive terminal UI

**Keep:**
- `dbos` — already in pyproject.toml, now actually used
- `surrealdb` — knowledge store
- `httpx` — Ollama API, used by embedder
- `pyyaml` — agent template parsing

**Remove (from runtime, keep as dev):**
- `fastapi` — no longer needed for V1 runtime
- `uvicorn` — no longer needed for V1 runtime

---

## Testing Strategy

**Existing tests:** The 1153 tests for library modules (git, knowledge, concurrency, agents, contracts) remain unchanged. They test isolated behavior and continue to work.

**New tests:**

| Category | What to test |
|----------|-------------|
| DBOS workflow | execute_job and execute_task with mocked agent steps — verify step ordering, DAG execution, question flow |
| Agent invocation | invoke_agent_step with mocked SDK — verify knowledge injection, output parsing, retry on rate limit |
| Bootstrap | Config loading → service initialization → workflow start |
| Interactive UI | Command parsing and dispatch (unit test without terminal) |
| Crash recovery | Start workflow → kill → resume → verify continuation from last step |
| End-to-end | Full flow with mocked agents: start → route → decompose → execute → review → PR → cleanup |

**DBOS test pattern:** DBOS supports testing by calling `DBOS.launch()` in test fixtures with a temp SQLite path. Workflows and steps can be called directly in tests.

---

## Migration Path

This is not a flag-day rewrite. The work can be done in phases:

**Phase A: DBOS Foundation**
- Add `@DBOS.workflow()` and `@DBOS.step()` decorators to orchestrator modules
- Convert sync functions to async
- Replace JobStore with DBOS state
- Remove daemon/server.py, daemon/process.py, daemon/database.py
- Tests: verify workflows execute with mocked steps

**Phase B: Agent Invocation**
- Rewrite invoker to call real Claude Agent SDK
- Add knowledge injection (memory index into system_prompt)
- Register query_knowledge as a callable tool
- Wire DBOS step retry for rate limits
- Tests: verify invocation shape, knowledge injection, retry behavior

**Phase C: Interactive Terminal**
- Add prompt_toolkit dependency
- Build log panel + input line
- Wire event polling from DBOS
- Implement all `/` commands
- Wire Tier 1/Tier 2 question handling
- Tests: command parsing, event formatting

**Phase D: Git Integration**
- Wire worktree creation into task workflow
- Wire PR creation, review, merge into steps
- Wire cleanup into workflow completion and cancellation
- Tests: verify git operations execute at correct workflow points

**Phase E: End-to-End + Cleanup Recovery**
- Full integration test with mocked agents
- Crash recovery test
- Multi-task parallel execution test
- Question flow test (Tier 1 and Tier 2)
- Orphaned worktree detection: on `bootstrap()` and `resume`, scan for worktrees not attached to any active DBOS workflow and offer to clean them
- Orphaned branch/PR detection: on resume, check for branches/PRs that belong to completed or failed tasks and clean up

---

## Success Criteria

- [ ] `devteam start --spec X --plan Y` launches a DBOS workflow and enters the interactive terminal
- [ ] Operator sees real-time progress in the log panel
- [ ] Operator can answer questions, inject comments, pause/resume, cancel
- [ ] Agents work in parallel up to `max_concurrent_agents`
- [ ] Each task gets its own git worktree, cleaned up on completion/cancel
- [ ] Knowledge index is injected into every agent invocation
- [ ] Rate limit errors trigger DBOS step retry with exponential backoff
- [ ] Crash + `devteam resume` picks up from the last completed step
- [ ] All approval gates are enforced (push_to_main always never)
- [ ] Tier 1 questions block all work, Tier 2 questions let other branches continue
