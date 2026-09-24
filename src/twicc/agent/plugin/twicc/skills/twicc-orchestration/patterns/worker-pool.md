# Worker pool (bounded concurrency)

Process many tasks through a bounded set of workers, instead of spawning one child
per task all at once.

Shape: star · push or pull · waves · merge · homogeneous.

## Who does what
- **Leader or manager** — hold a queue of tasks and a concurrency cap K. Keep K
  workers busy: as one finishes, hand it the next task (reuse it) or spawn a
  replacement. Collect results as they complete.
- **Workers** — take a task, do it, report back, and (if reused) wait for the next
  via `send-message` — which resurrects them if they went idle/dead.

## Protocol
1. Set K from the work size and cost/quota headroom (check `usage`).
2. Launch the first K tasks, optionally tagged with `--annotation wave=<n>`.
3. Wait for the active wave by its ids only:
   `sessions wait-reply <WAVE_ID>... --wait-timeout 300`,
   collect each result, then either `send-message` a worker the next task (reuse)
   or spawn a fresh worker for it.
4. Fresh workers need no `--since` (except while a session's compute is not
   current — e.g. right after a TwiCC restart: then pass `--since` an instant
   before the spawn or the send). For reused workers, capture the instant before
   sending the next task (`date -u +%Y-%m-%dT%H:%M:%SZ`) and pass it as
   `--since`, or send it with `send-message --wait-reply`. Update their `wave` annotation too, so the
   map shows which wave each worker is on.
5. Continue until the queue drains; aggregate.

## Use it when
There are far more tasks than you should run at once (cost, rate limits, or just
dozens of files).
Not for a handful of pieces (just scatter-gather them).

## Pitfalls
- Reuse via `send-message` keeps a worker warm but also accumulates its context —
  start fresh workers when context bloats.
- Mind `usage`/quota: a wide pool can exhaust a window fast.
- Still wait on direct children only.

Examples: `examples/codebase-audit.md`, `examples/large-migration.md`.
