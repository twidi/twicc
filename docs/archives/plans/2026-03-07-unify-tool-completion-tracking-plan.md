# Unify Tool Completion Tracking — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove duplicated completion fields from AgentLink, unify Bash and Agent tool completion tracking through ToolResultLink, and simplify the WS/API surface.

**Architecture:** AgentLink becomes a pure mapping table (tool_use_id → agent_id). ToolResultLink is the single source of truth for tool completion state. The frontend uses a unified `toolStates` cache for both Bash and Agent running detection. Synthetic process states remain for agents (SessionHeader indicator, tooltip duration).

**Tech Stack:** Django models + migrations, Django views, Python (compute.py, sessions_watcher.py), Vue 3 + Pinia store, WebSocket messages.

**Design doc:** `docs/plans/2026-03-07-unify-tool-completion-tracking-design.md`

---

### Task 1: Simplify AgentLink model + migration

**Files:**
- Modify: `src/twicc/core/models.py:435-475` (AgentLink class)
- Create: `src/twicc/core/migrations/0047_simplify_agent_link.py`

**Step 1: Remove fields from AgentLink model**

In `src/twicc/core/models.py`, modify the `AgentLink` class to remove `is_background`, `result_count`, `started_at`, `completed_at`, and the `is_done` and `duration` properties:

```python
class AgentLink(models.Model):
    """Links a Task tool_use to its spawned subagent within a session."""

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="agent_links",
    )
    tool_use_line_num = models.PositiveIntegerField()  # Line containing the assistant message with Task tool_use
    tool_use_id = models.CharField(max_length=255)  # The specific Task tool_use ID
    agent_id = models.CharField(max_length=255)  # The subagent ID

    class Meta:
        indexes = [
            models.Index(
                fields=["session", "tool_use_id"],
                name="idx_agent_link_lookup",
            ),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.tool_use_id} -> {self.agent_id}"
```

**Step 2: Create the migration**

Run: `cd /home/twidi/dev/twicc-poc && uv run python -m django makemigrations core --name simplify_agent_link`

Verify migration removes the 4 fields.

**Step 3: Commit**

```bash
git add src/twicc/core/models.py src/twicc/core/migrations/0047_simplify_agent_link.py
git commit -m "refactor: simplify AgentLink model, remove duplicated completion fields"
```

---

### Task 2: Rename BashToolUpdate → ToolResultUpdate + simplify AgentLinkUpdate

**Files:**
- Modify: `src/twicc/compute.py:33-49` (NamedTuples)
- Modify: `src/twicc/sessions_watcher.py` (imports and references)

**Step 1: Update NamedTuples in compute.py**

Replace `AgentLinkUpdate` and `BashToolUpdate` at lines 33-49:

```python
class AgentLinkUpdate(NamedTuple):
    """Describes a new AgentLink creation to broadcast to the frontend."""
    parent_session_id: str
    agent_id: str
    tool_use_id: str


class ToolResultUpdate(NamedTuple):
    """Describes a tool completion state change to broadcast to the frontend."""
    session_id: str
    tool_use_id: str
    result_count: int
    completed_at: datetime | None  # Timestamp of the latest tool_result
```

**Step 2: Update all references from BashToolUpdate to ToolResultUpdate**

In `src/twicc/compute.py`: rename all `BashToolUpdate` → `ToolResultUpdate`. In the `create_tool_result_link_live` function, change the variable name `bash_update` → `tool_update` and expand the condition from `tool_name == 'Bash'` to `tool_name in TRACKED_TOOL_NAMES` (define `TRACKED_TOOL_NAMES = frozenset({'Bash'}) | AGENT_TOOL_NAMES` near the top, after `AGENT_TOOL_NAMES`).

In `src/twicc/sessions_watcher.py`:
- Update import: `BashToolUpdate` → `ToolResultUpdate`
- Rename variable `bash_tool_updates` → `tool_result_updates` everywhere
- Update the return type annotation and docstring

**Step 3: Commit**

```bash
git add src/twicc/compute.py src/twicc/sessions_watcher.py
git commit -m "refactor: rename BashToolUpdate to ToolResultUpdate, simplify AgentLinkUpdate"
```

---

### Task 3: Simplify agent link creation functions in compute.py

**Files:**
- Modify: `src/twicc/compute.py:1754-2160` (live agent link functions)

**Step 1: Remove `_increment_agent_link_result_count`**

Delete the function at lines 1824-1861 entirely.

**Step 2: Update `create_tool_result_link_live`**

Remove the call to `_increment_agent_link_result_count` (line 1801). The function now returns `tuple[ToolResultUpdate | None]` (single value, not a tuple of two). Wait — actually keep returning a tuple but change it to `tuple[ToolResultUpdate | None]` only. The `AgentLinkUpdate` is no longer emitted from here.

Updated function signature and logic:

```python
def create_tool_result_link_live(
    session_id: str, item: SessionItem, parsed_json: dict
) -> ToolResultUpdate | None:
    """
    Create a ToolResultLink for a tool_result item during live sync.

    Searches the session for the item containing the matching tool_use
    and creates the link entry.

    Returns a ToolResultUpdate if the tool is tracked (Bash/Agent), None otherwise.
    """
```

Remove the `agent_update` variable and the call to `_increment_agent_link_result_count`. Change the Bash-only check to use `TRACKED_TOOL_NAMES`:

```python
            if not created:
                return None

            # Emit ToolResultUpdate for tracked tools (Bash, Task, Agent)
            if tool_name in TRACKED_TOOL_NAMES:
                links = ToolResultLink.objects.filter(
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                )
                result_count = links.count()
                max_timestamp = links.order_by('-tool_result_at').values_list('tool_result_at', flat=True).first()
                return ToolResultUpdate(
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                    result_count=result_count,
                    completed_at=max_timestamp,
                )

            return None
```

**Step 3: Simplify `create_agent_link_from_tool_result`**

Remove `is_background`, `started_at`, `completed_at` from the `get_or_create` defaults and from the returned `AgentLinkUpdate`:

```python
                obj, created = AgentLink.objects.get_or_create(
                    session_id=session_id,
                    tool_use_line_num=candidate.line_num,
                    tool_use_id=tool_use_id,
                    defaults={"agent_id": agent_id},
                )
                mark_agent_link_done(session_id, agent_id)
                if created:
                    return AgentLinkUpdate(
                        parent_session_id=session_id,
                        agent_id=agent_id,
                        tool_use_id=tool_use_id,
                    )
```

**Step 4: Simplify `create_agent_link_from_subagent`**

Same treatment — remove `is_background`, `started_at` from defaults and `AgentLinkUpdate`:

```python
                    obj, created = AgentLink.objects.get_or_create(
                        session_id=parent_session_id,
                        tool_use_line_num=candidate.line_num,
                        tool_use_id=tu_id,
                        defaults={"agent_id": agent_id},
                    )
                    if created:
                        mark_agent_link_done(parent_session_id, agent_id)
                        return AgentLinkUpdate(
                            parent_session_id=parent_session_id,
                            agent_id=agent_id,
                            tool_use_id=tu_id,
                        )
```

**Step 5: Simplify `create_agent_link_from_tool_use`**

Same treatment:

```python
                    _, created = AgentLink.objects.get_or_create(
                        session_id=session_id,
                        tool_use_line_num=item.line_num,
                        tool_use_id=tu_id,
                        defaults={"agent_id": subagent.id},
                    )
                    if created:
                        mark_agent_link_done(session_id, subagent.id)
                        updates.append(AgentLinkUpdate(
                            parent_session_id=session_id,
                            agent_id=subagent.id,
                            tool_use_id=tu_id,
                        ))
```

**Step 6: Commit**

```bash
git add src/twicc/compute.py
git commit -m "refactor: simplify agent link creation, remove result_count tracking from AgentLink"
```

---

### Task 4: Simplify background compute for agent links

**Files:**
- Modify: `src/twicc/compute.py:1570-1710` (background compute session loop)

**Step 1: Simplify agent_links_to_create dict building**

At line ~1597, remove `is_background` and `started_at` from the dict:

```python
                agent_links_to_create.append({
                    'session_id': session_id,
                    'tool_use_line_num': line_num,
                    'tool_use_id': tu_id,
                    'agent_id': agent_id,
                })
```

**Step 2: Remove the result_count/completed_at post-processing block**

Delete lines 1656-1667 (the block that computes `result_count` and `completed_at` for agent links from tool_result_links). This is no longer needed since AgentLink has no such fields.

Also remove the `tool_result_timestamps` dict that was only used for this block. Search for its initialization and usage:
- Initialization: around line ~1540 (`tool_result_timestamps: dict[str, datetime] = {}`)
- Population: around line ~1588-1590

Remove both.

**Step 3: Remove `task_tool_use_map` `is_background` and `started_at` tracking**

The `task_tool_use_map` currently stores `(line_num, is_background, started_at)`. Simplify to `(line_num,)` or just `line_num`:

At the population site (~line 1574):
```python
            task_tool_use_map[tu_id] = item.line_num
```

At the consumption site (~line 1596):
```python
            if tu_id in task_tool_use_map:
                line_num = task_tool_use_map[tu_id]
                agent_links_to_create.append({
                    'session_id': session_id,
                    'tool_use_line_num': line_num,
                    'tool_use_id': tu_id,
                    'agent_id': agent_id,
                })
```

**Step 4: Commit**

```bash
git add src/twicc/compute.py
git commit -m "refactor: simplify background compute agent link creation"
```

---

### Task 5: Update sessions_watcher.py — WS broadcast changes

**Files:**
- Modify: `src/twicc/sessions_watcher.py:380-410` (WS broadcast section)
- Modify: `src/twicc/sessions_watcher.py:700-720` (tool result link processing)

**Step 1: Update the tool result link processing**

At ~line 702-707, `create_tool_result_link_live` now returns a single `ToolResultUpdate | None` (not a tuple). Update:

```python
        # Tool result links (tool_result items are DEBUG_ONLY)
        if is_tool_result_item(parsed):
            tool_update = create_tool_result_link_live(session.id, item, parsed)
            if tool_update:
                tool_result_updates.append(tool_update)
            # Also check for agent links (Task tool_result with agentId)
            if update := create_agent_link_from_tool_result(session.id, item, parsed):
                agent_link_updates.append(update)
```

**Step 2: Simplify agent link WS broadcast**

At ~line 387-399, simplify the `subagent_state_changed` broadcast to `agent_link_created`:

```python
            # Broadcast agent link creations (new subagent linked to tool_use)
            for update in agent_link_updates:
                await broadcast_message(channel_layer, {
                    "type": "agent_link_created",
                    "parent_session_id": update.parent_session_id,
                    "agent_session_id": update.agent_id,
                    "tool_use_id": update.tool_use_id,
                    "project_id": parsed.project_id,
                })
```

**Step 3: Rename bash_tool_state broadcast to tool_state**

At ~line 401-409:

```python
            # Broadcast tool state changes (Bash, Agent completion tracking)
            for update in tool_result_updates:
                await broadcast_message(channel_layer, {
                    "type": "tool_state",
                    "session_id": update.session_id,
                    "tool_use_id": update.tool_use_id,
                    "result_count": update.result_count,
                    "completed_at": update.completed_at.isoformat() if update.completed_at else None,
                })
```

**Step 4: Commit**

```bash
git add src/twicc/sessions_watcher.py
git commit -m "refactor: rename WS messages to agent_link_created and tool_state"
```

---

### Task 6: Update backend API — views + urls

**Files:**
- Modify: `src/twicc/views.py:515-570` (subagents_state + bash_tool_states views)
- Modify: `src/twicc/urls.py:27-28` (URL routes)

**Step 1: Simplify `subagents_state` view**

```python
def subagents_state(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/subagents/

    Returns the agent links for a session: tool_use_id → agent_id mappings.
    """
    try:
        session = Session.objects.get(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    # Reject if the session is itself a subagent
    if session.parent_session_id is not None:
        raise Http404("Session not found")

    links = AgentLink.objects.filter(session=session).order_by("id")
    result = [
        {
            "agent_id": link.agent_id,
            "tool_use_id": link.tool_use_id,
        }
        for link in links
    ]
    return JsonResponse(result, safe=False)
```

**Step 2: Rename `bash_tool_states` to `tool_states` and expand scope**

```python
def tool_states(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/tool-states/

    Returns the completion state of each tracked tool_use (Bash, Task, Agent)
    in the session: result_count and completed_at (max tool_result timestamp).

    Response: {"tools": {"toolu_xxx": {"result_count": 2, "completed_at": "..."}, ...}}
    """
    try:
        session = Session.objects.get(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    from django.db.models import Count, Max

    TRACKED_TOOLS = {'Bash', 'Task', 'Agent'}

    links = (
        ToolResultLink.objects.filter(session=session, tool_name__in=TRACKED_TOOLS)
        .values('tool_use_id')
        .annotate(result_count=Count('id'), completed_at=Max('tool_result_at'))
    )

    tools = {}
    for entry in links:
        tools[entry['tool_use_id']] = {
            'result_count': entry['result_count'],
            'completed_at': entry['completed_at'].isoformat() if entry['completed_at'] else None,
        }

    return JsonResponse({"tools": tools})
```

**Step 3: Update URL route**

In `src/twicc/urls.py`, change line 28:
```python
    path("api/projects/<str:project_id>/sessions/<str:session_id>/tool-states/", views.tool_states),
```

**Step 4: Commit**

```bash
git add src/twicc/views.py src/twicc/urls.py
git commit -m "refactor: simplify subagents API, rename bash-tool-states to tool-states"
```

---

### Task 7: Bump CURRENT_COMPUTE_VERSION

**Files:**
- Modify: `src/twicc/settings.py:131`

**Step 1: Bump version**

```python
CURRENT_COMPUTE_VERSION = 57  # Bump when display rules change to trigger recomputation
```

**Step 2: Commit**

```bash
git add src/twicc/settings.py
git commit -m "chore: bump CURRENT_COMPUTE_VERSION to 57"
```

---

### Task 8: Frontend store — rename bashToolStates → toolStates

**Files:**
- Modify: `frontend/src/stores/data.js`

**Step 1: Rename in localState initialization**

Find `bashToolStates: {},` and rename to `toolStates: {},`.

**Step 2: Rename getter**

Rename `getBashToolState` → `getToolState`. Update references:
```js
        getToolState: (state) => (sessionId, toolUseId) => {
            const sessionStates = state.localState.toolStates[sessionId]
            if (!sessionStates) return null
            return sessionStates[toolUseId] || null
        },
```

**Step 3: Rename actions**

Rename `setBashToolState` → `setToolState`:
```js
        setToolState(sessionId, toolUseId, resultCount, completedAt) {
            if (!this.localState.toolStates[sessionId]) {
                this.localState.toolStates[sessionId] = {}
            }
            this.localState.toolStates[sessionId][toolUseId] = { resultCount, completedAt }
        },
```

Rename `fetchBashToolStates` → `fetchToolStates`, update URL:
```js
        async fetchToolStates(projectId, sessionId) {
            try {
                const url = `/api/projects/${projectId}/sessions/${sessionId}/tool-states/`
                const response = await apiFetch(url)
                if (!response.ok) return

                const data = await response.json()
                if (data.tools && Object.keys(data.tools).length > 0) {
                    const states = {}
                    for (const [toolUseId, state] of Object.entries(data.tools)) {
                        states[toolUseId] = {
                            resultCount: state.result_count,
                            completedAt: state.completed_at,
                        }
                    }
                    this.localState.toolStates[sessionId] = states
                }
            } catch (error) {
                console.error('Failed to fetch tool states:', error)
            }
        },
```

**Step 4: Update session clear**

Find where `bashToolStates` is cleaned up on session clear and rename to `toolStates`.

**Step 5: Simplify `fetchSubagentsState`**

Update to only populate agent links + create synthetic process states using `toolStates` for is_done check:

```js
        async fetchSubagentsState(projectId, sessionId) {
            try {
                const url = `/api/projects/${projectId}/sessions/${sessionId}/subagents/`
                const response = await apiFetch(url)
                if (!response.ok) return

                const agents = await response.json()

                for (const agent of agents) {
                    // Populate agent link cache (tool_use_id → agent_id mapping)
                    this.setAgentLink(sessionId, agent.tool_use_id, agent.agent_id)

                    // Check if agent is still running using toolStates
                    const toolState = this.localState.toolStates[sessionId]?.[agent.tool_use_id]
                    const resultCount = toolState?.resultCount || 0
                    // We don't know is_background from the API anymore,
                    // but if resultCount is 0, the agent is definitely still running.
                    // If resultCount >= 1, it might be done (non-background) or still running (background).
                    // We create synthetic for resultCount === 0 only; the ToolUseContent component
                    // handles the running state reactively using its own props.input.run_in_background.
                    // For synthetic process state, we just need to know "has any result arrived?".
                    // A resultCount of 0 means definitely still running → create synthetic.
                    // Any resultCount > 0 → the component will determine running state from its own context.
                    if (resultCount === 0) {
                        this.setSyntheticProcessState(agent.agent_id, projectId, null)
                    }
                }
            } catch (error) {
                console.error('Failed to fetch subagents state:', error)
            }
        },
```

Wait — we need `started_at` for the synthetic process state's `state_changed_at` (used for the tooltip duration). But we removed `started_at` from AgentLink. We can get it from the tool_use item's timestamp. But that's not available at fetch time in the store...

Actually, let's reconsider: for `fetchSubagentsState` (session reload), we don't have `started_at`. But the `ToolUseContent` component has `props.timestamp` which it already uses for `bashStartedAt`. So the tooltip duration for agents should use the same pattern: `props.timestamp` converted to unix, not `agentProcessState.state_changed_at`. This means the synthetic process state's `state_changed_at` is not needed for the tooltip — the component can use `props.timestamp` directly.

So the synthetic process state only needs to exist (with `synthetic: true` and `state: 'assistant_turn'`) for:
1. `SessionHeader` stop button guard (`!ps.synthetic`)
2. `ToolUseContent` to show the pulsing robot icon

Both only need to know "is this agent running?". The duration comes from `props.timestamp`.

Updated `fetchSubagentsState`:
```js
        async fetchSubagentsState(projectId, sessionId) {
            try {
                const url = `/api/projects/${projectId}/sessions/${sessionId}/subagents/`
                const response = await apiFetch(url)
                if (!response.ok) return

                const agents = await response.json()

                for (const agent of agents) {
                    this.setAgentLink(sessionId, agent.tool_use_id, agent.agent_id)

                    // Create synthetic process state if agent has no results yet
                    const toolState = this.localState.toolStates[sessionId]?.[agent.tool_use_id]
                    const resultCount = toolState?.resultCount || 0
                    if (resultCount === 0) {
                        this.setSyntheticProcessState(agent.agent_id, projectId, null)
                    }
                }
            } catch (error) {
                console.error('Failed to fetch subagents state:', error)
            }
        },
```

**Step 6: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "refactor: rename bashToolStates to toolStates, simplify fetchSubagentsState"
```

---

### Task 9: Frontend WS handler — update message types

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js:378-400`

**Step 1: Replace `subagent_state_changed` handler with `agent_link_created`**

```js
            case 'agent_link_created': {
                // New agent link created — populate cache and create synthetic process state
                const agentSessionId = msg.agent_session_id
                if (msg.tool_use_id && msg.parent_session_id) {
                    store.setAgentLink(msg.parent_session_id, msg.tool_use_id, agentSessionId)
                }
                // Agent just started → create synthetic process state
                store.setSyntheticProcessState(agentSessionId, msg.project_id, null)
                break
            }
```

**Step 2: Replace `bash_tool_state` handler with `tool_state`**

```js
            case 'tool_state': {
                // Update tool state for spinner/running display
                store.setToolState(msg.session_id, msg.tool_use_id, msg.result_count, msg.completed_at)

                // For agent tools: remove synthetic process state when done
                // Look up agent_id from agentLinks cache for all sessions
                // (msg.session_id is the parent session where the tool_use lives)
                const agentId = store.getAgentLink(msg.session_id, msg.tool_use_id)
                if (agentId && msg.result_count >= 1) {
                    // At least one result arrived — agent may be done.
                    // We remove synthetic here; the component's isAgentRunning computed
                    // will handle the exact threshold (1 vs 2 for background).
                    // This is safe because setSyntheticProcessState won't overwrite real states.
                    store.removeSyntheticProcessState(agentId)
                }
                break
            }
```

**Step 3: Commit**

```bash
git add frontend/src/composables/useWebSocket.js
git commit -m "refactor: update WS handlers for agent_link_created and tool_state"
```

---

### Task 10: Frontend SessionItemsList — rename fetch call

**Files:**
- Modify: `frontend/src/components/SessionItemsList.vue:400`

**Step 1: Rename fetchBashToolStates to fetchToolStates**

Find `store.fetchBashToolStates(` and rename to `store.fetchToolStates(`.

Ensure `fetchToolStates` is called **before** `fetchSubagentsState` (since `fetchSubagentsState` now reads from `toolStates` to determine if agents are done). If they're currently parallel or in the wrong order, make `fetchToolStates` await first, then `fetchSubagentsState`.

**Step 2: Commit**

```bash
git add frontend/src/components/SessionItemsList.vue
git commit -m "refactor: rename fetchBashToolStates to fetchToolStates in SessionItemsList"
```

---

### Task 11: Frontend ToolUseContent — unify running detection

**Files:**
- Modify: `frontend/src/components/items/content/ToolUseContent.vue:428-490`

**Step 1: Unify the tool state lookup**

Replace the separate Bash and Agent sections with a unified approach. The `toolState` computed is used by both:

```js
// --- Tool running state (unified for Bash and Agent) ---

const isBash = computed(() => props.name === 'Bash')
const isBackground = computed(() => !!props.input?.run_in_background)
const toolState = computed(() => (isBash.value || isTask.value) ? dataStore.getToolState(props.sessionId, props.toolId) : null)

// --- Bash tool spinner ---

const isBashRunning = computed(() => {
    if (!isBash.value) return false
    const resultCount = toolState.value?.resultCount || 0
    const requiredCount = isBackground.value ? 2 : 1
    return resultCount < requiredCount
})
const bashStartedAt = computed(() => {
    if (!isBash.value || !props.timestamp) return null
    return new Date(props.timestamp).getTime() / 1000
})
const bashSpinnerId = computed(() => `bash-spinner-${props.toolId}`)

// --- View Agent button for Task tool_use ---

const isTask = computed(() => AGENT_TOOL_NAMES.has(props.name))
```

(Note: `isTask` must be defined before `toolState` uses it, so reorder: define `isTask` first, then `toolState`.)

Correct order:
```js
const isBash = computed(() => props.name === 'Bash')
const isTask = computed(() => AGENT_TOOL_NAMES.has(props.name))
const isBackground = computed(() => !!props.input?.run_in_background)
const toolState = computed(() => (isBash.value || isTask.value) ? dataStore.getToolState(props.sessionId, props.toolId) : null)
```

**Step 2: Replace agent running detection**

Remove the old `agentProcessState` and `isAgentRunning` computeds. Replace with:

```js
const agentId = computed(() => dataStore.getAgentLink(props.sessionId, props.toolId))

const isAgentRunning = computed(() => {
    if (!isTask.value || !agentId.value) return false
    const resultCount = toolState.value?.resultCount || 0
    const requiredCount = isBackground.value ? 2 : 1
    return resultCount < requiredCount
})

// Started at — same pattern as Bash, using the tool_use timestamp
const toolStartedAt = computed(() => {
    if (!props.timestamp) return null
    return new Date(props.timestamp).getTime() / 1000
})

const viewAgentButtonId = computed(() => `view-agent-${props.toolId}`)
```

**Step 3: Update the template**

Replace the agent tooltip to use `toolStartedAt` instead of `agentProcessState.state_changed_at`:

```html
            <!-- View Agent indicator for Task tool_use (only in regular sessions) -->
            <template v-if="isTask && !parentSessionId">
                <!-- Agent not yet started: spinner -->
                <wa-spinner v-if="!agentId" class="agent-starting-spinner"></wa-spinner>
                <!-- Agent started: View Agent button (with pulsing robot if still running) -->
                <template v-else>
                    <AppTooltip v-if="isAgentRunning && toolStartedAt" :for="viewAgentButtonId">
                        Agent running for <ProcessDuration :state-changed-at="toolStartedAt" />
                    </AppTooltip>
                    <wa-button
                        :id="viewAgentButtonId"
                        size="small"
                        variant="brand"
                        appearance="outlined"
                        @click.stop="navigateToSubagent"
                    >
                        <wa-icon v-if="isAgentRunning" slot="start" name="robot" class="agent-running-icon"></wa-icon>
                        View Agent
                    </wa-button>
                </template>
            </template>
            <!-- Bash tool running spinner -->
            <template v-if="isBashRunning">
                <AppTooltip v-if="toolStartedAt" :for="bashSpinnerId">
                    Running for <ProcessDuration :state-changed-at="toolStartedAt" />
                </AppTooltip>
                <wa-spinner :id="bashSpinnerId" class="bash-running-spinner"></wa-spinner>
            </template>
```

Note: `bashStartedAt` can be replaced with `toolStartedAt` since both use `props.timestamp`. Remove the separate `bashStartedAt` computed.

**Step 4: Remove unused imports**

If `PROCESS_STATE` is no longer used in this file (was used for `agentProcessState`), remove it from the import.

**Step 5: Commit**

```bash
git add frontend/src/components/items/content/ToolUseContent.vue
git commit -m "refactor: unify Bash and Agent running detection via toolStates"
```

---

### Task 12: Verify and clean up

**Step 1: Search for remaining references to old names**

Search the codebase for any remaining references to:
- `bashToolState` / `bashToolStates` / `getBashToolState` / `setBashToolState` / `fetchBashToolStates`
- `bash_tool_state` (WS message type)
- `bash-tool-states` (API URL)
- `subagent_state_changed` (WS message type)
- `agentProcessState` (frontend)
- `BashToolUpdate` (backend)

Fix any remaining references.

**Step 2: Verify no circular imports or missing references**

Run: `cd /home/twidi/dev/twicc-poc && uv run python -c "from twicc.compute import ToolResultUpdate, AgentLinkUpdate; print('OK')"`

**Step 3: Commit any remaining fixes**

```bash
git add -u
git commit -m "chore: clean up remaining old references"
```

---

### Task 13: Update CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

**Step 1: Add entry under [1.0.4] Unreleased → Changed**

```markdown
- Unified tool completion tracking: Bash and Agent running state now derived from ToolResultLink (simplified AgentLink model, renamed API and WebSocket messages)
```

**Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: update changelog for unified tool completion tracking"
```
