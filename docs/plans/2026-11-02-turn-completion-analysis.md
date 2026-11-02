# Session completion analysis and proposed algorithm

## Recommendation

Confirm a logical turn after **one second without a new root turn or an existing assistant hold**.
Start this interval when the provider turn ends, or when the last assistant hold ends.
Cancel the interval immediately when the SDK announces another root request.

Use this confirmation for public state, timeline, unread notifications, and default `wait-reply`.
Keep the existing distinction between the public state and the actual SDK turn.
Keep background shells outside the assistant hold condition.

This is an analysis and a tested reference model. Production behavior remains unchanged.
The one-second value is an engineering choice supported by the cases below.
It is not a statistical guarantee against every future autonomous request.

## Three separate causes

| Cause | Current behavior | Required change |
| --- | --- | --- |
| The next root request starts before its first stream event | Claude ignores `status=requesting`. Public state changes twice. | Recognize the early request signal and confirm completion after a short interval. |
| A transcript message has `is_final=True` during ongoing work | Default `wait-reply` returns immediately, independently of the public state. | Wait for the logical completion and select its associated final message. |
| Background metadata changes during `user_turn` | Timeline emits another `turn.ended`. | Update background metadata without creating another turn boundary. |

Delaying a WebSocket broadcast addresses none of the other consumers.
The manager already persists the state and records timeline events before broadcasting.

## Existing behavior to preserve

- Native subagents, Monitors, scheduled wakeups, and existing goal logic can hold `assistant_turn`.
- A background shell alone permits `user_turn`, with the existing terminal indicator.
- Codex uses its actual active turn to choose between `steer` and a fresh SDK turn.
- A permission request or question exposes the existing pending-request state immediately.
- Interruptions, provider errors, and process death keep their distinct outcomes.
- A later independent message can start another logical turn.

Do not hold every TwiCC child session merely because it exists.
An independent session can send another message much later.
That message is a new input, even when no human types it.

## Historical evidence

The initial timeline snapshot covers October 26 through November 2, 2026.
It contains 1,220 Claude `turn.ended` events and 576 Codex `turn.ended` events.
Live tests and concurrent sessions add events after that snapshot.
Repeated background snapshots account for some end events. They are not separate completed turns.

Within one process lifetime, the sample contains 877 Claude and 388 Codex end-to-next-start pairs.
These pairs span 56 Claude sessions and 11 Codex sessions.
Message matching includes a delivery window after `turn.started`, because transcript writes can arrive later.

The Codex sample contains no confirmed autonomous resumption without a recorded input.
It contains 262 messages from other agents and 126 human or command inputs.
One orchestration session contributes 260 of those 262 agent messages.
This concentration prevents a useful general Codex delay estimate.

The Claude SDK sample contains 57 logs, including the live diagnostic session.
The extracted snapshot contains 2,731 root results.
`queued_turn_count` exists in 2,559 results, and every observed value is zero.
It also equals zero in the three confirmed cases below.
Therefore, zero is not a completion certificate.

### Confirmed interruptions without a background shell

These cases have no new sent prompt between the two provider turns.
The public completion snapshot reports no remaining background work.
Native child completion has already reached the SDK before the parent result.

All times in this table are UTC.
The early signal delay starts at the SDK result receipt.
The public interruption starts at the recorded timeline end.

| Session | Timeline end | Public interruption | Next `requesting` | First root `message_start` after result |
| --- | --- | ---: | ---: | ---: |
| `db30b6a3-84d5-4fd0-bc83-2f7c56011227` | October 26, 16:54:20.953440 | 3.190 s | 25.183 ms | 3.190 s |
| `78554f24-3ee1-4b45-8be2-6a2cfcc44b2a` | October 30, 10:59:31.890014 | 1.881 s | 20.158 ms | 1.878 s |
| `d2aa617e-0cca-4f03-9424-897df130cc44` | November 1, 10:46:12.053439 | 0.678 s | 20.960 ms | 0.674 s |

In the first case, the child completion reaches the SDK about 7.77 seconds before the parent result.
The live-child set can therefore be empty while the SDK still has an automatic continuation to consume.
Tracking only running children cannot cover this sequence.

All three results also report `terminal_reason="completed"` and `stop_reason="end_turn"`.
Their subagent statistics report every spawned child as completed.
These fields describe the physical SDK turn correctly. They do not establish logical completion in TwiCC.

### Why the short interval uses an early signal

| Algorithm | Confirmed cases bridged | Added completion latency |
| --- | ---: | --- |
| Immediate completion | 0 of 3 | None |
| One second, first stream event only | 1 of 3 | One second |
| Three seconds, first stream event only | 2 of 3 | Three seconds |
| Five seconds, first stream event only | 3 of 3 | Five seconds |
| One second plus `requesting` | 3 of 3 | One second |

Once `requesting` arrives, the root turn stays active until its terminal signal.
Slow first tokens therefore do not require a longer completion interval.

Across the extracted logs, 1,422 result-to-`requesting` sequences have no intervening sent input or result.
Many represent expected shell notifications, scheduled work, or other legitimate later activity.
Their global median is about 130 seconds. It must not set the completion interval.

The immediate subset contains 42 requests within one second.
Its median is 26.96 ms. Thirty-nine arrive within 500 ms.
This subset describes immediate SDK chaining. It is not a false-completion rate.

A further raw-log scan finds 10,920 `requesting` statuses without child scope fields.
The live child tests show this signal when the root consumes a child completion.
Keep an explicit whitelist for this status. Other system events remain metadata.

### Why an exact notification ledger is insufficient

The extracted logs contain 1,191 native child completion notifications.
Only 1,181 have a later result with the same `origin.runId`.
The ten unmatched notifications all report `completed`.

These mismatches do not establish the reason for each missing match.
They do invalidate an unconditional rule that requires one exact result per notification.
Such a rule needs cancellation and merge semantics before it can safely become a completion requirement.

## Live tests

The tests use `haiku` and `gpt-luna`, with low effort and fast mode disabled.
They use the normal TwiCC MCP interfaces and existing provider binaries.
The inspected checkout pins `claude-agent-sdk==0.2.175`.
The diagnostic prompts forbid repository changes.
Both parent processes are stopped after the tests.

| Provider | Session | Recorded cost including native children |
| --- | --- | ---: |
| Claude | `cff50e81-f4fb-47c3-a900-5f27ce45efae` | $0.030156 |
| Codex | `01a19dda-557b-72d0-8d93-5734ff957ddd` | $0.004333 |
| Total | | **$0.034489** |

### Claude native child

The parent writes `PARENT_WAITING`, while a native background child continues working.
After the child ends, the root automatically writes `PARENT_DONE`.
The existing public hold correctly produces one start and one end.

Default `session_wait_reply(from_line=0)` returns **`PARENT_WAITING` at line 45**.
It returns that message even when called after `PARENT_DONE` exists.
This directly reproduces the wrong reply selection.

Two additional trials vary the child duration and parent activity.
They do not reproduce the exact historical public-state interruption.
One trial consumes the child notification before its only root result.
The historical replay tests cover the precise interrupted sequences instead.

### Codex native child

The parent writes `PARENT_WAITING` and completes its SDK turn while its native child still works.
Default `wait-reply` returns that final while the public state remains `assistant_turn`.
The background snapshot reports one active subagent at that time.

After the child stops, this parent does not produce another root answer.
Therefore, completion must not require another final message after every child stop.
After the short interval, the earlier final can be the valid reply for this logical turn.

The same inexpensive session also critiques the proposed algorithm without tools or repository access.
Its useful objections concern overlapping inputs, timer races, transcript lag, and missing follow-up answers.
The proposal below addresses these conditions. This is a conceptual critique, not a code review.

## Proposed state algorithm

### Runtime facts

Keep these facts separate:

1. Actual root SDK activity.
2. Existing assistant holds, excluding shells.
3. A pending request requiring user input.
4. A cancellable completion candidate.
5. The confirmed completion of a logical turn.

The public `assistant_turn` can remain active while no actual SDK turn exists.
This already occurs with native child holds.
The completion interval extends that existing distinction.

### Events and decisions

| Event | Decision |
| --- | --- |
| Root result or completed SDK turn | Mark actual root activity inactive. Consider a completion candidate. |
| Last assistant hold ends | Consider a completion candidate if root activity is inactive. |
| Root `requesting`, Codex root `turn/started`, or actual root conversation traffic | Cancel the candidate. Mark root activity active. |
| Accepted input during the interval | Cancel the candidate. Dispatch using actual SDK activity. |
| Native child or another existing assistant hold starts | Cancel the candidate. Keep the public assistant state. |
| Background shell starts or stops | Refresh shell metadata. Do not create an assistant hold. |
| Settings acknowledgment, hook event, command catalog change, or unrelated metadata | Do not reopen a root turn or extend the interval. |
| Permission request or question | Cancel the candidate. Expose the pending request immediately. |
| Interrupt, provider error, process death, or shutdown | Cancel the candidate. Apply the explicit terminal outcome. |
| One-second interval expires | Recheck the same process and logical turn. Confirm once if still eligible. |

Scope conversation traffic to the root where the SDK exposes child identifiers.
Child tokens alone must not reopen the root turn.
The early Claude status is specifically `SystemMessage(subtype="status", data={"status": "requesting", ...})`.

Use a monotonic clock for the interval.
Process events and confirmation through one serialized decision path.
Each candidate carries the process identity and a generation token.
An old timer cannot close a later turn.
Recheck activity received before the deadline before publishing completion.

Known root activity has no completion timeout.
Existing SDK error handling and process lifecycle rules release failed activity.
A silent but active request must not become a successful reply merely because time passes.

### One confirmation for every consumer

Create an immutable completion record for each logical turn.
Associate the record with its process, accepted inputs, final message identity, and transcript boundary.
Do not infer an old completion from the current state of a later turn.

At confirmation, make the same decision available to:

- persisted public process state;
- WebSocket state updates;
- timeline turn boundaries;
- unread state and completion notifications;
- CLI and MCP `wait-reply`;
- the MCP event monitor, which shares `_SessionWait`.

The transcript watcher runs independently of the SDK.
An indexed earlier final can exist while the actual concluding final is still missing.
`wait-reply` must wait for the final identity associated with the confirmed completion.
Use an indexing barrier, not another fixed sleep.

Return the **last relevant final within that logical turn**.
Do not select the first final after the cursor.
Do not select the latest final across unrelated later turns.
Keep previous completion records available when a later input starts another logical turn.

If no later root answer exists after a child stops, the earlier final remains eligible.
If the logical turn contains no answer, return the existing non-reply outcome.
A provisional final does not override a current permission request or question.

Keep the explicit `--wait-background` shell-waiting contract separate.
Enabling it by default would contradict the required shell behavior.
Changing default completion semantics also requires CLI help, MCP descriptions, and skill documentation updates.

### Timeline rules

Emit one `turn.started` and one `turn.ended` per logical assistant interval.
Update background metadata without re-emitting `turn.ended` during an existing `user_turn`.
If background history needs an event, give that event its own meaning.
Keep raw provider events available for diagnostics without treating them as public turn boundaries.

## Source locations

| File | Relevant behavior |
| --- | --- |
| `src/twicc/providers/claude_code/agent/agent.py:1616` | `_is_turn_activity` excludes all system statuses, including `requesting`. |
| `src/twicc/providers/claude_code/agent/agent.py:1788` | `_update_live_tasks` removes terminal native tasks. |
| `src/twicc/providers/claude_code/agent/agent.py:2558` | Result handling holds known work or immediately publishes `USER_TURN`. |
| `src/twicc/providers/codex/agent/agent.py:830` | Actual turn and synthetic hold determine fresh send versus steer. |
| `src/twicc/providers/codex/agent/agent.py:1559` | Root turn completion and follow-up handling. |
| `src/twicc/providers/codex/agent/agent.py:2100` | Last native child stop can immediately release a hold. |
| `src/twicc/agent/base_manager.py:725` | State persistence precedes broadcasts. |
| `src/twicc/agent/base_manager.py:1157` | Process transitions feed timeline persistence. |
| `src/twicc/core/timeline.py:181` | `process_transition_events` repeats ends for idle background changes. |
| `src/twicc/cli/_wait_reply.py:496` | Final transcript messages bypass ordinary working-state checks. |
| `src/twicc/cli/_wait_reply.py:507` | The first final after the cursor wins. |
| `frontend/src/utils/sessions.js:73` | A live process becomes unread when its state is `user_turn`. |
| `src/twicc/asgi.py:250` | A former broadcast-delay comment remains; the sleep is disabled. |

## Tests and reproduction

The existing tests confirm the current contract, including behavior that must change.
They do not demonstrate that the proposed production fix exists.

```bash
uv run pytest -q tests/test_wait_reply.py tests/test_wait_background.py tests/test_codex_subagent_hold.py tests/test_background_work.py tests/test_timeline_process_hooks.py
```

Result: **197 passed**.

An additional run covers Claude activity, Monitor liveness, and four scratch reproductions.
Result: **15 passed**, including 11 existing tests and four scratch tests.
The scratch tests reproduce the ignored status and replay the three historical SDK sequences.

The reference model has **22 passing tests**.
Its generated lifecycle test covers **40,000 events across 1,000 deterministic sequences**.
It checks duplicate completion, slow root output, pending requests, interruptions, shell metadata, child holds, input routing, and generation isolation.
The three historical replays pass again after the final model changes.

The model represents indexing with a boolean barrier.
It does not test actual database atomicity, scheduler races, or the production watcher.
Those require integration tests during implementation.

Scratch directory:

```text
/home/twidi/.twicc/scratch/01a19d6a-ce71-7902-9b91-0cc76c4ae41e/
```

Important files:

- `completion_spike.py`: isolated reference model and 22 tests.
- `test_completion_replay.py`: current predicate reproduction and three historical replays.
- `analyze.py`, `pairs.json`: timeline pairing and input matching.
- `enrich.py`, `enriched.json`: candidate trigger classification. Categories require manual validation.
- `sdk_events.json`, `sdk_matches.json`: extracted SDK events and timeline correlations.
- `signal_eval.py`, `signal_eval.json`: request timing and notification correlations.
- `verify_signals.py`: raw status scope checks and the last Claude live trace.

Run from the checkout:

```bash
uv run python /home/twidi/.twicc/scratch/01a19d6a-ce71-7902-9b91-0cc76c4ae41e/completion_spike.py
uv run pytest -c pyproject.toml -q /home/twidi/.twicc/scratch/01a19d6a-ce71-7902-9b91-0cc76c4ae41e/test_completion_replay.py tests/test_claude_turn_activity.py tests/test_claude_monitor_liveness.py
```

Historical analysis opens the production SQLite database in read-only mode.
Pytest uses `twicc.settings_test` and isolated data.

## Remaining limits and implementation checks

No finite interval can predict an unrelated future message.
A new request after confirmed completion starts another logical turn unless existing held work already explains it.
The one-second interval targets immediate chaining, rather than every later autonomous event.

During implementation, add integration checks for:

- a result, child stop, early root request, and delayed first token;
- a child stop without a later root final;
- fresh Codex sends during the interval, and steer during an actual turn;
- a result arriving concurrently with a timer or accepted input;
- delayed transcript indexing of the exact concluding message;
- multiple accepted inputs and later waits for older completed turns;
- pending questions, interruptions, errors, and shutdown during the interval;
- shell metadata updates during `user_turn`, without additional timeline ends;
- reconnects and manager restarts, with no replay of stale candidates.

Record the candidate duration, cancellation reason, and delay before any later automatic restart.
Separate external inputs, shell notifications, and already-held work in those measurements.
Use those categories to validate the one-second value after deployment.
