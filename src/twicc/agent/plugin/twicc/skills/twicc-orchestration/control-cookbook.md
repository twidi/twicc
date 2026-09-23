# Process Control Cookbook

Concrete process-control recipes for leaders and managers. Use these when you
need exact `sessions wait-reply` / `sessions stop` commands; otherwise the main
orchestration skill is enough.

## Direct-child barrier

Wait for the children you spawned, not grandchildren. Name their ids (from each
`create-session` result): a child spawned seconds ago matches no filter yet.

```bash
$TWICC sessions wait-reply <CHILD_ID>... --since 2000-01-01 --wait-timeout 300
```

`--since 2000-01-01` fits children never messaged since their spawn; for a later
round, pass the instant captured before that send. Never today's date: a bare
date is midnight UTC, and a future instant misses an answer already given.

Exit 0 whatever the outcomes: read each `results[id].outcome`. Repeat the same
call, same `--since`, naming only the ids still on `timeout` (or `wait_failed`);
`ended`, `provider_error` and `unknown_session` are final. Never loop on
`summary.concluded == summary.total`: it counts `replied` and
`awaiting_user_input` only.

Use `--wait-all` (the default). Use `--wait-first` only for races or queue loops
where one child is enough to move forward.

Adding `--spawned-by self` is safe only when every child is in the barrier:
named ids are unioned with the filters, never narrowed by them.

## Scoped barrier by annotation

To wait on one phase, wave, role, or attempt set, name **only that subset's
ids** — the ones you spawned for it:

```bash
$TWICC sessions wait-reply <AUDIT_ID>... --since 2000-01-01 --wait-timeout 300
```

An `--annotation phase=audit` filter adds nothing here: it misses a child not
indexed yet, and the ids already cover the subset. Never name ids outside the
subset: the filters do not narrow them.

## First-wins race

Wait for one child to answer, validate it, then stop the losers:

```bash
$TWICC sessions wait-reply <ATTEMPT_ID>... --since 2000-01-01 --wait-first --wait-timeout 300
$TWICC sessions stop <LOSER_ID>... --timeout 30
```

`--wait-first` also stops on `awaiting_user_input`: declare a winner only on
`outcome == "replied"`. When the first conclusion is `awaiting_user_input`,
answer that session and wait again, or wait again naming only the other ids —
the same call with the same `--since` returns the blocked session at once. A
winner that finished before the call is seen only with `--since`.

If you tagged losers after validation:

```bash
$TWICC sessions stop --spawned-by self --annotation status=loser --timeout 30
```

Never stop before validating the first finisher; "first done" is not "correct".

## Stop selected children

For one child, use the exact id:

```bash
$TWICC session <SESSION_ID> stop --timeout 30
```

For a batch you own:

```bash
$TWICC sessions stop <SESSION_ID>... --timeout 30
$TWICC sessions stop --spawned-by self --annotation status=cancelled --timeout 30
```

`sessions stop` has no guardrail: never call it bare (it stops every running
session, you included). `parent`, `--spawn-tree` and `--siblings` reach beyond
your children; use them only when that is the intent. Pair `--annotation` with
a filiation scope.

## Abort a subtree

Only do this when you intentionally cancel a manager and everything below it.
`--descendants` excludes the target, so pass the manager id explicitly too:

```bash
$TWICC sessions stop <MANAGER_ID> --descendants <MANAGER_ID> --timeout 30
```

Explicit ids and filtered ids are unioned: the explicit `<MANAGER_ID>` stops the
manager, and `--descendants <MANAGER_ID>` adds every proper descendant below it.

This is exceptional cleanup, not normal synchronization.

## Inspect before acting

Use direct-child listing for ordinary control:

```bash
$TWICC sessions --spawned-by self --active --slim
$TWICC sessions --spawned-by self --annotation status=blocked --slim
$TWICC sessions get <CHILD_ID>...
```

Drop `--active` to see finished children too (`process.state: "dead"`). A child
spawned seconds ago is not listed yet: `sessions get` returns it by id.

Use `topology self` for structure and context. Use `sessions --spawn-tree self`
only for an explicit whole-tree inventory, not for routine manager control.

## Annotation keys for control

- `status` — `working`, `done`, `failed`, `blocked`, `cancelled`, `loser`, `runaway`.
- `phase` — named phase such as `audit`, `implement`, `verify`.
- `wave` — bounded-concurrency wave number.
- `attempt` — speculative attempt id.
- `job` / `role` — functional role when a barrier targets only one role.
