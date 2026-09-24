# Composing an orchestration

Patterns are not a fixed menu — they are points in a small space you compose
along five axes. Read this once; the named patterns in this folder are just
common, useful combinations to start from. When none fits, compose your own.

## The five axes

- **Topology** — the shape of the tree: star (you → N children), chain (each step
  feeds the next), deep tree (children that recurse into their own subtrees), or
  panel (N voices + one judge). The tree is the **control** shape (who spawns, who
  waits, who aggregates) — children may still hold **lateral links** to each other
  on top of it (a mesh), see Channel.
- **Channel** — how information moves: push (`send-message parent`), pull (you read
  a child anytime with `session <id> messages`), files (bulky exchange through the
  shared `scratch_dir`), steering (you message a child mid-`assistant_turn` to
  redirect it), and **sideways** (siblings talk to each other directly with
  `send-message <sibling_id>` / `send-messages --siblings self`, or pull a peer) —
  no rule forces every exchange through the parent. See `patterns/peer-coordination.md`.
- **Synchronization** — when you move on: barrier (wait for all,
  `sessions wait-reply <CHILD_ID>...`), phase gate (wait on one
  batch, named by its ids only, validate, then advance), first-wins
  (`--wait-first`, then stop the losers by explicit id or scoped `sessions stop`),
  or continuous (no barrier — you watch and steer as they run).
- **Aggregation** — how N results become one: concat/merge/dedup, vote (majority),
  select (best, or first that works), synthesize (a new artifact from the inputs),
  single-writer integration, or chain (no merge — one output is the next input).
- **Voice diversity** — how alike the children are: homogeneous (same
  model/preset/brief — throughput, redundancy) or heterogeneous (different model,
  provider, preset, angle, or prompt — diversity of judgment, e.g. Claude vs Codex).

## Two invariants, whatever you compose

- **You wait on, and aggregate from, your direct children only.** A manager's
  subtree is its own business; you see its single deliverable, not its internals.
  This is about **control** (synchronization and the final merge), not about who may
  talk to whom: children are free to coordinate sideways with their siblings
  (`patterns/peer-coordination.md`) — you still only wait on and aggregate your own.
- **Read-only children still push and spawn via `mcp__twicc__*`.** A `strict`/`dontAsk`
  child can't write files, edit code, or run shell — but the MCP tools let it spawn,
  send, and retag (see twicc-orchestration › Permission modes). It's a pull-only leaf
  only when MCP is disabled — deferred tools still count as present.

## Reading the named patterns

Each file states its axes (Shape), who does what (leader vs manager), the protocol
in real commands, when to use it, and the pitfalls. Load the one that matches your
situation — you do not need to read them all.
