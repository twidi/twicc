# Background compute recovery

## Requirements and constraints

An internal writer failure or compute-process failure must not require a backend restart.
Keep processing other sessions. Retry unfinished work automatically while the provider remains enabled.
Only explicit provider shutdown, task cancellation, or backend shutdown stops recovery.
Completed sessions stay current. Partial commits remain safe inputs for another compute.
All database writes continue through the existing writer coordinator.
Foreground demand takes precedence over compute and recovery. Retry waits hold no writer or admission lease.
Do not change schemas, cold rules, content payloads, scratch cleanup, or normal compute concurrency.
Do not start servers, mutate the worktree's copied database, install packages, or touch main.
Keep the already reviewed SQLite write-first initialization and per-run IPC transport corrections.

Use stop-aware async retry waits. Repeated failures back off, with a maximum delay of 60 seconds.
Avoid repeated attempts on a faulty session while another session is eligible.
Transport failures retire only the old run and process, not the provider.
Fence incoming results, await writer-side abandonment, settle/retrieve retained completion futures,
and close old process/stream resources before creating a new run.
Cancellation must drain owned cleanup and must never spawn a replacement process.
Events from an old Codex run must not change the new run or release its migration leases.
Preserve migration reconstruction intent and release each old lease exactly once.
Do not replay or advertise a migrated history as ready before successful final application.
Existing unavailable-source diagnostics remain explicit; eligible failed sessions get periodic retries.
Never advertise successful completion of pending failed work.

## Task 1: Shared recovery and Claude continuation

Work in `src/twicc/providers/background_compute_task.py` and, if useful, a small
`src/twicc/providers/compute_recovery.py` module. Add focused tests in
`tests/test_background_compute_retries.py` and `tests/test_background_compute_recovery.py`.

Use TDD. Demonstrate the existing failure policy with a behavioral failing test before product edits.
Separate intentional provider stop from worker/run cleanup. Internal recovery must not set `ctx.stop_event`.
Provide a narrowly scoped cleanup/backoff interface for Task 2 and record its exact API in the report.
Cleanup must fence the old run, finalize it through the writer, settle/retrieve its retained future,
then stop the worker and close transport resources. Retain ownership through cancellation.
After failure, query actual compute versions and retry only outdated sessions.
Continue feeding healthy sessions after one initialization job fails.
Continue after per-session compute/apply failures. Restart a dead process with a fresh run ID and pipe.
Rotate or cool down candidates after run failure so a deterministic faulty newest session cannot starve older sessions.
Preserve existing pipelined computation; do not change to one full-session apply at a time.
Keep active/changing sessions conservative under the existing offset-stability policy.
Do not report failed outdated sessions as successful completed work.

Cover: one-off initialization failure, apply failure, transport/process failure, successful sessions excluded from retry,
faulty newest session with healthy older session, bounded repeated retries, explicit stop during wait,
cancellation during cleanup, and fresh-run rejection of late results.
Prefer real writer/SQLite/process integration for the critical recovery path.
Mocks may inject failures or remove external provider dependencies; assert real state and progress.
Run targeted tests and scoped ruff. Report RED/GREEN evidence and remaining risks.
Do not run the whole suite; the controller runs it once after integration.

## Task 2: Codex recovery without losing coordinator state

Read Task 1's exact shared recovery interfaces before editing.
Work in `src/twicc/providers/codex/background_compute.py` and its scheduler/recovery tests.
Modify `src/twicc/providers/db_writer.py` only if required to fence acknowledgment events by run ID.
Do not replace the whole Codex coordinator with a generic supervisor.

Use TDD. A writer initialization failure or a process/transport failure must not terminate the coordinator.
Fence and drain the old run before releasing its migration leases and starting a replacement.
Settle/retrieve the old completion future. Retire old status pumps and transient state.
Keep forced rebuild intentions until effective successful application.
Replace permanent `failed_this_run` exclusion for eligible failures with bounded retry eligibility.
Use per-session cooldowns and process-level backoff. Ready healthy sessions must run before a cooling failed session.
Keep external-writer/active-session deferral rules. Re-read current source and DB state on retry.
Fence worker status, apply acknowledgments, and completion callbacks by generation/run ID.
No delayed old event may mutate a new run, clear its state, or release its leases.
Do not signal successful completion while eligible failed work remains.
Preserve watcher startup/replay semantics and existing unavailable-source explanations.
Keep startup classification separate from successful repair completion for both providers.
The Claude orchestrator must receive initial classification even while its recovery task remains alive.

Cover: failed initialization after migration preparation, failed final apply, unexpected process death,
late old apply/status/future callback after restart, failed newest session fairness,
forced reconstruction preserved, lease/replay ordering, stop/cancellation during recovery, and no busy loop.
Include at least one real writer/SQLite or real-process recovery test without backend restart.
Run targeted Codex, shared recovery, and IPC tests plus scoped ruff.
Do not run the whole suite; the controller runs it once after integration.

## Task 3: Independent integrated validation

Review both providers and their shared cleanup as one lifecycle.
Verify foreground priority and healthy-provider progress during failure of the other provider.
Check that old runs cannot publish after replacement, completion futures settle, and process/receiver resources drain.
Validate cancellation and intentional shutdown: no retry or process spawn follows them.
Run the full Python suite from this worktree with `TWICC_DATA_DIR=$PWD`.
Run scoped ruff and `git diff --check`.
Keep devctl stopped. Live startup on the copied user database requires the user's start instruction.
