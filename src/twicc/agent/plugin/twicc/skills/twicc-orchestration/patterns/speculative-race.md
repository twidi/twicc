# Speculative race (first-wins)

Run several attempts at the same task in parallel; take the first acceptable result
and stop the rest.

Shape: star · pull + push · first-wins · select · heterogeneous (varied approaches).

## Who does what
- **Leader or manager** — spawn K children on the same goal, each with a different
  approach (model, preset, or strategy). Take the first that succeeds, `stop` the
  others, use its result.
- **Workers** — each attempts the whole task independently; report success with the
  result, or failure.

## Protocol
1. Spawn K attempts, ideally diverse (`--model`/`--provider`/`--preset` or a
   different brief), `--annotation attempt=<k>` and optionally `--annotation status=racing`.
2. First-wins: `sessions wait-reply <ATTEMPT_ID>... --wait-first --wait-timeout 300`.
   A winner needs `outcome == "replied"`: `--wait-first` also stops on
   `awaiting_user_input` — answer that attempt, or wait again without it.
3. Inspect the finisher: if acceptable, stop the rest by explicit ids, or mark the
   losers and run `sessions stop --spawned-by self --annotation status=loser --timeout <N>`.
   If not acceptable, wait again, naming only the other ids. Re-running resumes,
   except for a session whose compute is not current; `--since <the instant the
   batch started>` resumes in every case.
4. Use the winning result.

## Use it when
A task is flaky or latency-sensitive and attempts are independent — diversity raises
the odds one path works.
Not when you need agreement (→ quorum) or every result matters (→ scatter-gather).

## Pitfalls
- "First done" ≠ "correct" — validate the finisher before stopping the others.
- Stop the losers, or they burn cost to no end.
- Identical attempts waste the race — vary something meaningful.

Examples: `examples/hard-bug-race.md`.
