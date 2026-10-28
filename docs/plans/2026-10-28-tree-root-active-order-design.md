# Tree Root Active Order — Design

**Status:** reviewed by independent adversarial subagents (8 rounds; the last one PASS, its three Minor findings fixed). Written with the user 2026-10-28.
**Date:** 2026-10-28
**Scope:** frontend only. The order of tree roots among active entries in tree mode: the sidebar session list (`components/session/list/SessionList.vue`) and the icon rail in tree mode (`utils/railActiveSessionsTree.js`, called from `composables/useRailActiveSessions.js`). Flat mode does not change.
**Baseline:** `main` at `14c2703a9`.

---

## 1. Why

In flat mode, active sessions are ordered by the start time of their process (`processState.started_at`, `utils/sessionSort.js`). Apart from the optimistic `STARTING` state (`started_at: null`, `stores/data.js` `setProcessState`), which is set before the backend answers and also overwrites a live process for a moment (the retry of `components/session/detail/items/ApiError.vue`), the value does not change while the process lives, so active sessions keep their place while they work.

In tree mode, `buildSessionTree` (`utils/sessionTree.js`) orders a root by its effective `activeKey`, which is the **maximum** `started_at` of its own process and of every active node below it. A root whose own process is not running (for example an orchestration leader whose workers run) therefore jumps to the top of the active entries each time one of its descendants starts or restarts a process. The user sees the root move.

The intent, as in flat mode: **a root keeps its place among active entries for as long as its tree stays active.** Order inside a subtree is out of scope.

## 2. Rule

**Definitions.**
- A **process identity** is the pair (session id, `started_at`) of a live process state (`processStates[id]` present) that is not synthetic and not in the `STARTING` state. A real agent's `started_at` is fixed at its construction (`time.time()`, `src/twicc/agent/base_agent.py`) and sent with every state, so a new value means a new process. `STARTING` frames are excluded because they can carry a value that belongs to no process: the optimistic client state has `null`, and the Claude Code manager's override frame after a settings kill sends a monotonic-clock value (`asyncio.get_event_loop().time()`, `providers/claude_code/agent/manager.py`); the real identity arrives with the agent's next state.
- The **ancestry** of a live process is the chain of sessions above its session in the session tree, as `buildSessionTree` sees it: the tree-parent chain (`treeParentId`, so a `tree_detached` session ends it) over the loaded session rows, stopping at a blocking session and at any link whose row is not loaded. One bridge only: when the live process's **own** row is not loaded yet, its first link is the first entry of its `spawn_ancestors` (sent by the backend with every process state); from there the walk follows the loaded rows with the same rules and ends at the next unloaded link. So the tracker never links a process to a session the tree would not link it to once the rows are loaded (a hidden or sub-agent parent never loads and ends the chain, as in the tree).
- A session X **holds** an identity when the identity's session is X or X is in its ancestry. This does not depend on any view, scope, filter or candidate list.

**Windows.** One window per session X that holds at least one identity:
- It **opens** when X starts holding a real identity while it has no window. Its key is X's own `started_at` when X holds its own identity, otherwise the smallest `started_at` among the identities X holds.
- It **stays open** while X still holds at least one of the identities it held at the previous update; the key then does not change, whatever other processes start, stop or restart below X (A starts, then B while A runs, then A stops: B carried the window, the key stays A's start). Its held set is then refreshed to the identities X holds now.
- It **closes** when none of them is still held: every held process stopped or restarted (a restart in place gives a new `started_at`, for example after a WebSocket reconnect, where `setActiveProcesses` patches the live entry instead of deleting it), or the live ones are no longer below X (a real detach or reparent). If X still holds other identities, a new window opens at once with a new key; otherwise X has no window.
- **`STARTING` over a live process.** A held identity (id, t) whose session goes to `STARTING` for a moment (the `ApiError` retry sets it with `started_at: null`) stays held unchanged; when a non-`STARTING` state comes back, it is the same identity only if its `started_at` equals t.

**Use of the key.** In tree mode, an active root of a caller's tree (as decided today by that tree) sorts by its window key when it has a window, and by today's `activeKey` otherwise (every live process below it still in `STARTING`). The key replaces `activeKey` for ordering roots only (the `rootIds` sort and every consumer of root order). Nodes below a root keep today's `activeKey` (maximum) and today's order. `effective.active`, `effective.pinned`, `effective.dateKey`, `triggerIds` and every other output are unchanged. Pinned roots that are active are ordered by the same key within the pinned group. Equal keys keep the candidate order, as today.

**Single sessions.** A session that holds only its own identity (no live process below it) gets the key of flat mode: its own `started_at`, and a fresh key after each restart. One accepted difference: during an optimistic overwrite of its live process (`ApiError` retry), flat mode drops its key to 0 for a moment, while the tree root keeps its key.

## 3. State

The window start cannot be derived from the current snapshot (A starts, B starts, A stops: the smallest live `started_at` is now B's), so windows are history.

- **One tracker for the whole app**, owned by the data store and shared by every caller (sidebar instances, rail). It is derived from the live process states (their keys, `started_at`, `spawn_ancestors`) and from the tree-parent fields of the loaded rows on the ancestry chains, and it keeps its previous windows as memory: each update is a function of the previous windows and the current inputs, never a cache patched by hand from event handlers. It is kept up to date eagerly, with one update per reactive flush after any change of those inputs (not on read), whether or not anything reads it (a lazy derivation evaluated only on read would lose the history it did not see), so no hand-off between processes is missed while a view hides a root, a search is typed, tree mode is off or the rail is empty.
- Callers read the window key of their active roots from it. A tree build depends only on the keys of its own active roots: a window that opens or closes for a session outside that tree does not rerun it. A key changes only when a window opens or closes, which happens on a process start, stop or restart.
- **Limitation (rows not loaded):** the ancestry ends at a link whose row is not loaded, and only tree-mode views fetch missing ancestors. A window above such a link starts when that row loads; until then the session above it holds nothing through that link. In practice this only affects the first position of an idle leader after a reload (rows stay loaded once a tree has shown them), the same as "a reload starts empty".
- The tracker is bounded by the live processes: a window dies with the last identity its session holds.
- A page reload starts empty: the first update opens windows from the processes alive at that moment. The order is then stable from there.

## 4. What changes for the user

- In tree mode, an active root no longer jumps when a process starts or restarts anywhere below it, or when its own process starts while a descendant runs. Scope switches, the search box, the tree toggle, the archive toggle and remounts do not change it either, and the sidebar and the rail agree.
- A root moves only when: no process lives below it any more and one starts again (new window); every process below it was replaced between two updates (a restart in place, or a stop and a start in one flush, for example after a WebSocket reconnect); its live processes stop being below it (a real detach or reparent); after a reload; when a just-started process receives its real `started_at` and opens the window; or for the reasons it moves today that do not involve `activeKey` (pin, archive, scope membership).
- Flat mode, the order inside subtrees, sections, separators, unread and badges are unchanged.

## 5. Non-goals

- The order inside a subtree.
- Persisting windows across reloads.

## 6. Verification

Tests (`node --test`), through the tracker and through each caller's tree build:

1. Leader root inactive, worker A active: starting worker B (later `started_at`) does not change the root's position among active roots; restarting A does not either.
2. The root's own process starts while a worker runs: no move.
3. Window chain: A starts (window opens at A), B starts, A stops: the key stays A's start.
4. Nothing lives below the root, then a process starts: a new window opens with the new key (the root may move).
5. A session holding only its own identity: same key as flat mode (fresh key after each restart).
6. Hidden hand-off: with workers in another project of the workspace, W2 starts then W1 stops, in separate flushes, while (a) a narrower scope that does not show the leader is displayed, (b) a search query is active, (c) tree mode is off, (d) the leader is an inactive or child entry of the shown tree: back in the workspace tree, the leader's key equals the key it has when the user never left (W1's `started_at`). Nothing reads the tracker during the hand-off steps (it is read only after them), so the test proves the eager update. The rail and a sidebar showing the same root use the same key.
7. Ancestry: a worker whose own row is not loaded yet joins its leader's window through the first `spawn_ancestors` link at once (no window break during the row load); a hidden intermediate parent (never loaded) and an unloaded intermediate link end the chain, so the leader does not hold the worker, as in the tree; once the intermediate row loads, the tracker follows the tree (a `tree_detached` intermediate still ends the chain).
8. Override frame: a worker's `STARTING` frame with a monotonic `started_at` (the settings-kill override), a concurrent start of another worker, then the worker's real state: the leader's key never takes the override value.
9. Real reparent / detach: a detach (`tree_detached`) or reparent that moves the live processes out from below the root opens a new window.
10. Live retry chain: the window key comes from a process that stopped, the only remaining live process goes to `STARTING` with `started_at: null` (the `ApiError` retry) and back to its `started_at`: the key does not change.
11. Restart in place: a root's own `started_at` (or the only live descendant's) changes while its process entry stays present (the reconnect path): the root takes the new key; a stop and a start of different processes in the same flush is a gap too.
12. Optimistic start: a fresh process set to `STARTING` with `started_at: null`, then receiving the real `started_at`, orders the root by the real `started_at` (not frozen at 0).
13. The tracker triggers no rerun of a tree build when no window opens or closes (an active-session tick, a `user_turn` tick, a project activity change), nor when a window opens or closes for a session outside that tree.
14. Rail: a session holding only its own identity that stops and restarts takes its new `started_at`, as in flat mode; the rail tree rows follow the rules above.
15. Everything else equals the current output: for a fixture sequence where no session holds an identity other than its own — including a stop-and-restart and a fresh optimistic `STARTING` (not a live retry) — every tree equals today's tree exactly (root order included); for every fixture, `effective` fields, child order and `triggerIds` equal today's. Existing tests that assert the old root jump change only that assertion.
