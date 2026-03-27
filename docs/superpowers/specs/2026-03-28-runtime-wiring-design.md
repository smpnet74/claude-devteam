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
2. Calls `DBOS.launch()` (initializes SQLite at `~/.devteam/devteam.sqlite`)
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

If the process dies (Ctrl+C, crash, terminal close):
```bash
devteam resume W-1
```
- Calls `DBOS.launch()` → `DBOS.resume_workflow(workflow_id)`
- DBOS replays from the last completed step (not from the beginning)
- Reconnects to SurrealDB/Ollama
- Re-enters the interactive terminal
- Worktrees and branches are still on disk — work is not lost

---

## Workflow Architecture

### Parent Workflow: execute_job

```python
@DBOS.workflow()
async def execute_job(job_id: str, spec: str, plan: str, config: dict) -> JobResult:
    # Step 1: Route intake
    routing = await route_intake_step(spec, plan)
    emit_event(job_id, "routed", routing.path.value)

    # Step 2: Decompose (skip for small_fix/research)
    if routing.path in (RoutePath.FULL_PROJECT, RoutePath.OSS_CONTRIBUTION):
        decomposition = await decompose_step(spec, plan, routing)
        emit_event(job_id, "decomposed", f"{len(decomposition.tasks)} tasks")

    # Step 3: Execute tasks via child workflows
    task_handles = {}
    for task in get_ready_tasks(decomposition):
        handle = await DBOS.start_workflow_async(
            execute_task, job_id, task, config
        )
        task_handles[task.id] = handle

    # Wait for all tasks, launching new ones as dependencies complete
    await manage_dag_execution(job_id, decomposition, task_handles, config)

    # Step 4: Post-PR review
    await run_post_pr_review_step(job_id, decomposition)

    # Step 5: Cleanup
    await cleanup_step(job_id)

    return JobResult(status="completed")
```

### Child Workflow: execute_task

```python
@DBOS.workflow()
async def execute_task(job_id: str, task: TaskDecomposition, config: dict) -> TaskResult:
    # Create isolated worktree
    worktree = await create_worktree_step(task)

    max_revisions = config.get("max_revisions", 3)
    revision_count = 0

    while revision_count <= max_revisions:
        # Invoke engineer agent
        impl = await invoke_agent_step(
            role=task.assigned_to,
            prompt=build_task_prompt(task, revision_feedback),
            worktree_path=worktree,
            project=config["project"],
        )

        # Handle questions
        if impl.status in ("needs_clarification", "blocked"):
            question = create_question(impl, task)
            await DBOS.set_event_async(job_id, f"question:{question.id}", question)

            # Wait for operator answer via DBOS messaging
            answer = await DBOS.recv_async(f"answer:{question.id}", timeout=None)
            revision_feedback = f"Answer to your question: {answer}"
            continue

        # Peer review
        review = await invoke_agent_step(
            role=task.peer_reviewer,
            prompt=build_review_prompt(impl, task),
            worktree_path=worktree,
            project=config["project"],
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
        await DBOS.set_event_async(job_id, f"pr:{task.id}", pr)
        return TaskResult(status="completed", pr=pr)

    # Max revisions exceeded
    return TaskResult(status="max_revisions_exceeded")
```

### Agent Invocation Step

```python
@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=60, backoff_rate=2.0)
async def invoke_agent_step(role: str, prompt: str, worktree_path: str, project: str) -> BaseModel:
    # 1. Build knowledge context
    knowledge_index = await build_memory_index_safe(knowledge_store, project)

    # 2. Get agent definition from registry
    defn = agent_registry.get(role)

    # 3. Build full prompt with knowledge injection
    full_system_prompt = f"{defn.prompt}\n\n{knowledge_index}"

    # 4. Call Claude Agent SDK
    result = await claude_sdk_query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            model=defn.model,
            system_prompt=full_system_prompt,
            allowed_tools=list(defn.tools) + ["query_knowledge"],
            permission_mode="default",
            cwd=worktree_path,
            output_format=get_output_schema(role),
        ),
    )

    # 5. Extract knowledge from response
    await extract_knowledge_from_response(result, project, role)

    # 6. Return typed result
    return parse_agent_result(role, result)
```

DBOS retry handles rate limit errors: if the step throws, DBOS waits with exponential backoff and retries automatically. No custom retry wrapper needed.

---

## Bootstrap Sequence

```python
# orchestrator/bootstrap.py

async def bootstrap(spec: str, plan: str) -> WorkflowHandle:
    """Initialize all services and start the job workflow."""

    # 1. Load config
    config = load_and_merge_config()

    # 2. Initialize DBOS
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
- `/answer Q-1 text` → `await DBOS.send_async(workflow_id, "answer:Q-1", text)`
- `/pause` → `await DBOS.cancel_workflow_async(workflow_id)` (DBOS preserves state)
- `/cancel` → cancel workflow + trigger cleanup workflow
- `/status` → read DBOS workflow status + child workflow statuses

**Tier 1 blocking:**
- When a Tier 1 question event arrives, the UI:
  - Pauses event rendering
  - Changes the input prompt to `BLOCKING Q-1> `
  - Waits for the operator's response
  - Sends the answer via `DBOS.send_async()`
  - Resumes event rendering

---

## Concurrency Model

**Agent concurrency** is controlled by `config.general.max_concurrent_agents` (default 3). The parent workflow's DAG execution loop limits how many child task workflows are running simultaneously by tracking active handles and only launching new ones when a slot opens.

```python
async def manage_dag_execution(job_id, decomposition, initial_handles, config):
    max_concurrent = config["general"]["max_concurrent_agents"]
    active = dict(initial_handles)  # task_id -> handle
    completed = {}

    while active or has_pending_tasks(decomposition, completed):
        # Wait for any active task to complete
        if active:
            done_id, result = await wait_for_any(active)
            completed[done_id] = result
            del active[done_id]

        # Launch newly ready tasks up to concurrency limit
        for task in get_ready_tasks(decomposition, completed):
            if len(active) >= max_concurrent:
                break
            if task.id not in active and task.id not in completed:
                handle = await DBOS.start_workflow_async(execute_task, ...)
                active[task.id] = handle
```

**Rate limiting** is handled by DBOS step retry. When the Claude API returns a rate limit error, the `invoke_agent_step` throws, DBOS catches it, and retries after the configured backoff. No global pause flag needed — DBOS manages this per-step.

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
| `concurrency/rate_limit.py` (pause flag parts) | DBOS step retry replaces global pause coordination |
| `concurrency/invoke.py` | DBOS step retry replaces custom retry wrapper |

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
| `config/settings.py` | Config loading, unchanged |
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

**Phase E: End-to-End**
- Full integration test with mocked agents
- Crash recovery test
- Multi-task parallel execution test
- Question flow test (Tier 1 and Tier 2)

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
