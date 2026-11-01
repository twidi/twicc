# SDK Integration - Phase 1: Initial Implementation

**Date:** 2026-01-31
**Status:** COMPLETED
**Author:** Brainstorming session

## Task Tracking

- [x] Task 1: Module agent backend
- [x] Task 2: Intégration lifecycle serveur
- [x] Task 3: WebSocket backend
- [x] Task 4: Store frontend
- [x] Task 5: UI frontend
- [x] Task 6: Fix SDK connection issue

---

## Issues

### Issue 1: ProcessTransport is not ready for writing

**Date:** 2026-02-01
**Status:** Fixed

**Symptom:** When sending a message to resume a session (no active process), the process fails immediately with:
```
Failed to start process: ProcessTransport is not ready for writing
```

**Root cause:** The SDK has two transport modes:

1. **Print mode**: When `connect(prompt)` is called with a string, the `SubprocessCLITransport` runs Claude with `--print -- <prompt>` and immediately closes stdin after process start. This is for one-shot queries.

2. **Streaming mode**: When `connect()` is called without a prompt (or with an `AsyncIterable`), the transport runs Claude with `--input-format stream-json` and keeps stdin open for bidirectional communication.

The problem was that `ClaudeSDKClient` always sets `is_streaming_mode=True` in its internal `Query` object, regardless of how the transport was configured. When `connect(prompt)` was called with a string:
- Transport entered print mode and closed stdin
- Query tried to send an `initialize` control request via `transport.write()`
- `write()` failed because `_stdin_stream` was `None` (stdin was closed)

**Fix:** Changed the sequence in `ClaudeProcess.start()`:
```python
# Before (incorrect):
await self._client.connect(prompt)  # Enters print mode, closes stdin

# After (correct):
await self._client.connect()  # Enters streaming mode, keeps stdin open
await self._client.query(prompt)  # Sends message via the streaming protocol
```

The `query()` method properly formats the prompt as a user message and sends it via the streaming protocol.

---

## Decisions made during implementation

### Task 1: Agent module backend

1. **SDK API clarification**: The SDK uses `ClaudeSDKClient` with:
   - `connect()` - Initial connection (no prompt for streaming mode)
   - `query(prompt)` - Send messages (both initial and follow-ups)
   - `receive_messages()` / `receive_response()` - Async iterator for messages
   - `disconnect()` - Clean up

   **Important:** Do NOT pass a string prompt to `connect()`. This puts the transport in "print mode" which closes stdin immediately, but `ClaudeSDKClient` always uses streaming mode for the control protocol. Always call `connect()` first, then `query(prompt)` to send messages.

2. **ProcessInfo includes error field**: Added `error: str | None` to `ProcessInfo` to communicate error details when a process dies (useful for WebSocket broadcast to frontend).

3. **ProcessState uses StrEnum**: Changed from `Enum` to `StrEnum` for cleaner JSON serialization (values serialize as strings directly).

4. **State change callback is async**: The `on_state_change` callback passed to `ClaudeProcess.start()` is async to support WebSocket broadcasts that may involve I/O.

5. **Broadcast callback on ProcessManager**: Added `set_broadcast_callback()` method to ProcessManager for WebSocket integration in later tasks.

6. **Lock for thread safety**: ProcessManager uses `asyncio.Lock()` to protect `_processes` dict from concurrent access during send_message and shutdown operations.

### Task 2: Server lifecycle integration

1. **Singleton pattern for ProcessManager**: Used a global `_process_manager` variable with `get_process_manager()` getter function. This follows the same pattern as the watcher module (`_stop_event` with `get_stop_event()`).

2. **Lazy initialization**: The ProcessManager is created on first access via `get_process_manager()`, not at server startup. This aligns with the spec requirement "start fresh, no active processes" - the manager starts empty by default.

3. **Shutdown placement**: ProcessManager shutdown is called after all other task cleanups in `run.py` because:
   - Claude processes may still be writing to JSONL files
   - Watcher should stop first to avoid trying to process files during shutdown
   - The shutdown has its own timeout (5s default) so it won't block indefinitely

4. **No explicit startup task**: Unlike the watcher which runs as a continuous async task, the ProcessManager doesn't need a "start" task - it's event-driven (responds to WebSocket messages). The singleton is accessed as needed by the WebSocket consumer.

### Task 3: WebSocket backend

1. **Broadcast callback setup in connect()**: The ProcessManager's broadcast callback is set in the WebSocket consumer's `connect()` method. This is idempotent (safe to call multiple times) since `set_broadcast_callback` simply overwrites the previous callback.

2. **Error response type**: Added a generic `error` message type for client-side error handling. Format: `{"type": "error", "message": "..."}`. Used for validation errors and ProcessManager exceptions.

3. **Project directory lookup**: Created a local `get_project_directory()` helper with `@sync_to_async` decorator to fetch the project's `directory` field. Returns None if project not found or has no directory, which triggers an error response to the client.

4. **ProcessState serialization**: Since ProcessState is a StrEnum, it serializes directly as a string when included in JSON. No need for `.value` conversion.

5. **Active processes on connect**: The `active_processes` message is sent directly to the connecting client (via `self.send_json`), not broadcast to all clients. This provides the initial state only to the new connection.

### Task 4: Store frontend

1. **Process states in top-level state, not localState**: The `processStates` map is stored in the top-level state (alongside `projects`, `sessions`, `sessionItems`) rather than in `localState`. This is because process states come from the server via WebSocket, making them server data like other top-level state.

2. **Dead processes removed from map**: When a `process_state` message arrives with `state: 'dead'`, the entry is removed from `processStates` rather than kept with a 'dead' state. This simplifies the UI logic - if there's no entry, it means no active process (same as dead). The UI can then treat both "no process" and "dead" identically (textarea/button enabled for auto-resume).

3. **setActiveProcesses clears and rebuilds**: The `setActiveProcesses` action completely clears and rebuilds the `processStates` map rather than merging. This ensures proper sync with server state on reconnection and avoids stale entries.

### Task 5: UI frontend

1. **English labels instead of French**: The spec mentioned French labels ("Envoyer", "Démarrage...", "Claude travaille...") but CLAUDE.md specifies "All code content (UI strings, comments, variable names) must be in English". Following CLAUDE.md, used English labels: "Send", "Starting...", "Claude is working...".

2. **MessageInput only for main sessions**: The message input is only shown for main sessions (when `parentSessionId` is null), not for subagent sessions. Subagents are managed by Claude itself, not by user interaction.

3. **WebSocket send function access**: Created a module-level `sendWsMessage()` function exported from `useWebSocket.js` to allow components to send WebSocket messages without needing to be within the composable context. The send function is set when WebSocket connects and cleared when it disconnects.

4. **Keyboard shortcut**: Added Cmd/Ctrl+Enter to send messages, following common chat application patterns.

5. **Layout with flexbox**: The MessageInput component is placed at the bottom of the session content. Empty states and loading states use `flex: 1` to push the MessageInput to the bottom of the container.

### Task 6: Fix SDK connection issue

1. **SDK transport modes**: The `SubprocessCLITransport` has two modes:
   - **Print mode** (`connect(prompt)` with string): Runs Claude with `--print -- <prompt>`, closes stdin immediately
   - **Streaming mode** (`connect()` without prompt): Runs Claude with `--input-format stream-json`, keeps stdin open

2. **ClaudeSDKClient always uses streaming mode**: The `Query` object inside `ClaudeSDKClient` is always created with `is_streaming_mode=True`, which means it will try to send control requests via `transport.write()`. If the transport is in print mode, stdin is closed and the write fails.

3. **Correct usage pattern**: Always call `connect()` first (without a prompt) to enter streaming mode, then call `query(prompt)` to send messages. The `query()` method properly formats the message and sends it via the streaming protocol.

## Resolved questions and doubts

1. **Q: How does the SDK handle resuming sessions?**
   A: Use `ClaudeAgentOptions(resume=session_id)` - the `resume` parameter takes the session ID to continue.

2. **Q: How to detect when Claude finishes responding?**
   A: Consume messages via `receive_messages()` and detect `ResultMessage` which indicates completion.

3. **Q: Can we pass prompt in connect()?**
   A: **NO for string prompts.** Passing a string to `connect(prompt)` puts the transport in "print mode" which closes stdin immediately. Since `ClaudeSDKClient` always uses streaming mode for control protocol, the initialization will fail with "ProcessTransport is not ready for writing". Always call `connect()` first, then `query(prompt)` to send messages.

4. **Fix: Empty string check for project directory**
   In `asgi.py`, the check `if cwd is None:` was changed to `if not cwd:` to catch both None (project not found) and empty strings (project has `directory=""`). Both cases should trigger the error response "Project not found or has no directory configured".

5. **Fix: Exception propagation in ClaudeProcess.start() and send()**
   The spec (section 3.1) states "Errors are logged and reported, never propagated". The `raise` statements after `_handle_error()` calls were removed. When a process error occurs, `_handle_error()` logs the error, transitions to DEAD state, cleans up resources, and broadcasts via WebSocket. The caller does not receive an exception.

6. **Fix: Concurrency model in ProcessManager._on_state_change()**
   The `_on_state_change` callback is called in two contexts: (1) synchronously from `send_message()` while holding the lock, and (2) asynchronously from the message loop background task without the lock. The lock acquisition for dead process cleanup was removed to prevent deadlocks in context 1. The cleanup is safe without a lock because: in asyncio, operations between await points are atomic (no preemption), and the identity check ensures we only delete the exact process instance.

7. **Fix: Dead code removal in ProcessManager.send_message()**
   With process errors no longer propagating from `start()`, the `try/except` block that cleaned up failed processes became dead code. It was removed. Cleanup now happens via the state change callback, which is called by `_handle_error()` in ClaudeProcess.

---

## Overview

This spec documents the integration of the Claude Agent SDK (Python) into TwiCC to enable bidirectional communication with Claude Code, not just viewing conversations.

Currently, TwiCC is a viewer: it watches JSONL files created by Claude Code CLI and displays them. The goal is to add the ability to **send messages** to Claude, creating a full replacement for the CLI.

**Prérequis:** Voir `2026-01-31-sdk-integration-phase0-research-draft.md` pour l'analyse du SDK (architecture, API, types de messages, options).

### Scope Phase 1

Cette spec couvre uniquement la possibilité de **répondre à des sessions existantes**. L'utilisateur navigue vers une session existante et peut envoyer des messages pour continuer la conversation.

Ce qui est inclus :
- Lancer un process Claude pour reprendre une session existante
- Envoyer des messages à un process actif
- Recevoir les changements d'état du process
- Auto-resume transparent (si le process n'est pas actif, le backend le relance)

D'autres fonctionnalités seront ajoutées dans les phases suivantes.

### Terminologie

- **Session** : Une conversation Claude, identifiée par un `session_id`, persistée dans un fichier JSONL
- **Process** : Un process Claude en cours d'exécution qui gère une session. Une session peut exister sans process actif.

---

## 1. Design Decisions

### 1.1 Permission Mode

**Decision:** Use `permission_mode="bypassPermissions"`.

**Rationale:** Simplifies initial implementation. No need to handle permission requests from SDK. Focus on getting the process management working first.

### 1.2 Process Management

**Decision:** Store active processes in memory, not in database.

**Rationale:** Processes must die when our server stops. No persistence needed.

**What to track per process:**
- `session_id`: Claude's session ID
- `project_id`: Which project this belongs to
- `state`: Current state (see 4.6)
- `last_activity`: Timestamp for future timeout management

### 1.3 Multi-Project Support

**Decision:** Sessions are tied to projects. The project's `directory` field provides the working directory for Claude.

---

## 2. Implementation Architecture

### 2.1 Backend Components

```
src/twicc/
├── agent/
│   ├── __init__.py
│   ├── manager.py      # ProcessManager: tracks all active processes
│   ├── process.py      # ClaudeProcess: wraps one SDK client
│   └── states.py       # State machine definitions
```

### 2.2 ProcessManager

```python
from enum import Enum
from typing import NamedTuple
import asyncio

class ProcessState(Enum):
    STARTING = "starting"
    ASSISTANT_TURN = "assistant_turn"
    USER_TURN = "user_turn"
    DEAD = "dead"

class ProcessInfo(NamedTuple):
    session_id: str
    project_id: str
    state: ProcessState
    last_activity: float

class ProcessManager:
    """Manages all active Claude processes."""

    _processes: dict[str, "ClaudeProcess"]  # session_id → process

    async def send_message(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str
    ) -> None:
        """
        Send a message to a session.
        If no active process exists, start one with resume option.
        """
        ...

    def get_active_processes(self) -> list[ProcessInfo]:
        """List all active processes."""
        ...

    async def shutdown(self) -> None:
        """Kill all processes (called at server shutdown)."""
        ...
```

### 2.3 ClaudeProcess

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, ResultMessage

class ClaudeProcess:
    """Wraps a single Claude SDK client."""

    def __init__(self, session_id: str, project_id: str, cwd: str):
        self.session_id = session_id
        self.project_id = project_id
        self.state = ProcessState.STARTING
        self.last_activity = time.time()

        self._options = ClaudeAgentOptions(
            cwd=cwd,
            permission_mode="bypassPermissions",
            resume=session_id,
        )
        self._client: ClaudeSDKClient | None = None

    async def start(self, prompt: str) -> None:
        """
        Start process and send first message.
        """
        self._client = ClaudeSDKClient(options=self._options)
        await self._client.connect()
        self.state = ProcessState.ASSISTANT_TURN
        await self._client.query(prompt)

    async def send(self, text: str) -> None:
        """Send a follow-up message."""
        if self._client is None:
            raise RuntimeError("Process not started")

        self.state = ProcessState.ASSISTANT_TURN
        self.last_activity = time.time()
        await self._client.query(text)

    async def run_message_loop(self, on_state_change: Callable) -> None:
        """
        Background task that processes messages.
        We don't use the message content (JSONL watcher does that),
        but we need to consume them to track state.
        """
        async for msg in self._client.receive_messages():
            if isinstance(msg, ResultMessage):
                self.state = ProcessState.USER_TURN
                self.last_activity = time.time()
                on_state_change(self)

    async def kill(self) -> None:
        """Terminate the process."""
        if self._client:
            await self._client.disconnect()
        self.state = ProcessState.DEAD
```

### 2.4 WebSocket Messages

#### Frontend → Backend

```json
{
    "type": "send_message",
    "session_id": "claude-conv-xxx",
    "project_id": "proj-xyz",
    "text": "Now do this..."
}
```

#### Backend → Frontend

```json
// Process state changed
{
    "type": "process_state",
    "session_id": "claude-conv-xxx",
    "project_id": "proj-xyz",
    "state": "starting" | "assistant_turn" | "user_turn" | "dead"
}

// Active processes list (on connect)
{
    "type": "active_processes",
    "processes": [
        {
            "session_id": "claude-conv-xxx",
            "project_id": "proj-xyz",
            "state": "user_turn"
        }
    ]
}
```

### 2.5 Integration with Existing Watcher

The existing JSONL watcher continues to work unchanged. It detects new files and updates, broadcasts via WebSocket, and the frontend renders the conversation.

The new agent module is **additive**:
- Watcher handles: reading JSONL → DB → WebSocket broadcast of conversation content
- Agent handles: starting processes, sending messages, tracking state

They don't need to communicate directly. The JSONL file is the shared interface.

---

## 3. Error Handling and Crash Isolation

### 3.1 Critical Requirement: Process Isolation

**A crashing Claude process must NEVER crash the backend.**

Each Claude process runs in complete isolation:
- Wrapped in try/except at every level
- Errors are logged and reported, never propagated
- Process death triggers cleanup, not cascading failure

### 3.2 Error Handling Flow

1. Process crashes or returns error
2. Catch exception in ClaudeProcess
3. Set state to "dead"
4. Log error details
5. Send WebSocket notification to frontend:
   ```json
   {
       "type": "process_state",
       "session_id": "claude-conv-xxx",
       "project_id": "proj-xyz",
       "state": "dead",
       "error": "Process terminated unexpectedly"
   }
   ```
6. Clean up resources (close subprocess, remove from manager)
7. Backend continues running normally

### 3.3 No Automatic Retry

**Decision:** No automatic retry of failed processes.

User must explicitly send a new message to restart. This prevents:
- Infinite retry loops
- Unexpected behavior
- Resource exhaustion

### 3.4 Server Startup and Shutdown

**At startup:**
- Start fresh, no active processes
- No attempt to resume previous processes
- ProcessManager initializes empty

**At shutdown (graceful):**
- Send interrupt signal to all active processes
- Wait a few seconds max (not waiting for Claude to finish responding)
- Force kill any remaining processes

**Rationale:** User might be stopping the server specifically to kill runaway Claude processes. Don't block shutdown waiting for responses that may take a long time to arrive.

### 3.5 Concurrent Processes

**Decision:** No artificial limit on concurrent processes.

- User can have as many active processes as they want
- If someone crashes the server by running too many, that's on them

### 3.6 Auto-Resume Behavior

**Decision:** Transparent auto-resume when sending a message.

**UX :**
- Un seul bouton "Envoyer" (pas de distinction visible Reply/Resume)
- Le backend gère la logique de façon transparente

**Backend logic:**
```python
async def send_message(self, session_id: str, project_id: str, cwd: str, text: str):
    if session_id not in self._processes:
        # No active process, create one with resume
        process = ClaudeProcess(session_id, project_id, cwd)
        self._processes[session_id] = process
        await process.start(text)
    else:
        # Process exists, just send message
        await self._processes[session_id].send(text)
```

---

## 4. Process States

### 4.1 State Machine

4 états pour le cycle de vie d'un process :

| State            | Meaning                  | Transition trigger                   |
|------------------|--------------------------|--------------------------------------|
| `starting`       | Process is starting      | Process créé, avant envoi du message |
| `assistant_turn` | Claude is working        | Message envoyé au process            |
| `user_turn`      | Waiting for user message | Received `ResultMessage`             |
| `dead`           | Process terminated       | Error, kill, or server shutdown      |

### 4.2 State Diagram

```
[send_message, no active process] → starting
                                        ↓ (message sent to process)
                                  assistant_turn ←──────┐
                                        ↓ (received result)  │
                                    user_turn ───────────┘ (user sends message)
                                        ↓ (kill/error/shutdown)
                                      dead
```

### 4.3 Notes

- `starting` : brief, le temps de lancer le process SDK et d'envoyer le message
- `assistant_turn` : peut être long (Claude travaille)
- `user_turn` : prêt à recevoir un message utilisateur
- `dead` : terminal, un nouveau message relancera un process (auto-resume)

---

## 5. Implementation Details

### 5.1 WebSocket Message Format

Le projet a déjà des messages WebSocket existants. On gardera le même style de format. Les structures de la section 2.4 sont indicatives ; le format exact sera aligné sur l'existant lors de l'implémentation.

### 5.2 ProcessManager : Tâche asynchrone

**Decision:** Le ProcessManager tourne comme tâche asynchrone dans le process principal.

**Rationale:**
- On utilise Django Channels avec `InMemoryChannelLayer` (pas Redis) → le ProcessManager doit être dans le même process pour accéder au WebSocket
- Le ProcessManager **n'écrit jamais dans la base de données** → pas de conflit SQLite
- Même pattern que les autres composants (watcher, etc.)

**Architecture:**
```
run.py
├── Uvicorn (ASGI) ─── Django + Channels
├── watchfiles (file watcher, async task)
└── ProcessManager (async task) ─── gère les ClaudeSDKClient
```

**Note:** Les process Claude eux-mêmes sont des sous-process (lancés par le SDK), mais le ProcessManager qui les gère est une tâche async. Attention à éviter les opérations bloquantes dans le ProcessManager pour ne pas bloquer l'event loop.

**Location:** `src/twicc/agent/` (module séparé)

### 5.3 Frontend : Comportement UI

**Composants à ajouter :**
- Textarea + bouton "Envoyer" en bas de la conversation
- Affichage de l'état du process

**Store :**
Le store Vue doit gérer l'état des process actifs :
- Map `session_id → ProcessState`
- Mise à jour via WebSocket (`process_state`, `active_processes`)
- Utilisé pour déterminer l'état du textarea/bouton

**Règles de blocage :**

| État process     | Textarea | Bouton   | Comportement                        |
|------------------|----------|----------|-------------------------------------|
| `starting`       | disabled | disabled | "Démarrage..."                      |
| `assistant_turn` | disabled | disabled | "Claude travaille..."               |
| `user_turn`      | enabled  | enabled  | "Envoyer"                           |
| `dead` ou aucun  | enabled  | enabled  | "Envoyer" (auto-resume transparent) |

**Logique :**
- Pas de gestion d'envoi pendant que l'assistant travaille
- Un seul label "Envoyer" — le backend gère l'auto-resume de façon transparente
- L'état du process est reçu via WebSocket

---

## 6. Implementation Tasks

### Task 1 : Module agent backend

Créer le module `src/twicc/agent/` avec :
- `states.py` : Enum `ProcessState`
- `process.py` : Classe `ClaudeProcess` (wrapper SDK)
- `manager.py` : Classe `ProcessManager` (singleton, gestion des process)

**Note :** Le code présenté dans les sections 2.2 et 2.3 est donné à titre indicatif. L'agent devra explorer le code source du SDK (`claude-agent-sdk`) pour déterminer l'API exacte à utiliser.

### Task 2 : Intégration lifecycle serveur

Modifier `run.py` pour :
- Lancer le ProcessManager comme tâche async
- Gérer le shutdown graceful (kill des process actifs)

### Task 3 : WebSocket backend

Modifier le consumer WebSocket existant pour :
- Recevoir `send_message` du frontend
- Émettre `process_state` quand l'état change
- Émettre `active_processes` à la connexion

### Task 4 : Store frontend

Ajouter dans le store Vue :
- Map `session_id → state` pour les process actifs
- Handlers pour les messages WebSocket `process_state` et `active_processes`

### Task 5 : UI frontend

- Composant textarea + bouton "Envoyer" en bas de la conversation
- Logique de blocage selon l'état du process
- Envoi du message via WebSocket

---

## 7. Summary

| Topic                | Decision                                     |
|----------------------|----------------------------------------------|
| Permission mode      | `bypassPermissions` - no permission handling |
| Process storage      | In-memory only, not persisted to DB          |
| Process states       | starting → assistant_turn → user_turn → dead |
| Crash handling       | Total isolation, never crashes backend       |
| Server shutdown      | Graceful but fast (few seconds max)          |
| Concurrent processes | Unlimited, no artificial limit               |
| Resume behavior      | Transparent auto-resume on message send      |
| Content type         | Text only                                    |
