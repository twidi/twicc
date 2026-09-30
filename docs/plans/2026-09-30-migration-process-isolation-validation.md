# Migration Process Isolation Validation

Status: implemented and independently reviewed in the isolated worktree.

Normal runtime uses Django SQLite. Only an awaited migration child selects the custom backend. No live instance is restarted or migrated. Product commits: 45290450, cf562647, 52df4e31.



## Evidence: task-1-report.md

# Task 1 Report

## Implementation

- Runtime and ordinary pytest settings use `django.db.backends.sqlite3`.
- `twicc.settings_migration` copies database dictionaries and changes only the default engine.
- Exact `TWICC_SQLITE_STANDARD_MIGRATIONS=1` selects the standard migration fallback.
- `run_migrations()` executes `sys.executable -m django migrate --settings=twicc.settings_migration --verbosity=0`.
- The launcher inherits environment and cwd. It uses no shell or replacement environment map.
- Startup waits at the original migration call position, inside the parent instance lock.
- Child Django logging writes normal migration diagnostics directly to the same `backend.log`.
- Parent logging preserves failed child stderr tracebacks. It does not replay normal child log records.
- Child launch failures log their exception before propagation.
- SIGINT raises KeyboardInterrupt. SIGTERM raises SystemExit(143) through a temporary handler.
- Both paths terminate and reap a live child before propagation and lock release.
- Cleanup ignores repeated SIGINT/SIGTERM, waits five seconds, then kills and reaps a resistant child.
- SIGKILL cannot execute parent cleanup. This non-recoverable case can leave the migration child running.
- `close_fds=True` prevents inheritance of the instance lock, even if its descriptor is explicitly inheritable.
- Direct optimized migration documentation now selects `--settings=twicc.settings_migration`.

## TDD Evidence

### RED

Command:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_migration_process.py -q
```

Output: **8 failed, 1 passed in 3.97s**.

Expected failures: runtime still selected the custom backend, migration settings did not exist, and launcher did not exist.
The passing case covered the existing exact standard fallback.
Full output: `.superpowers/sdd/2026-09-30-migration-process-isolation/red.log`.

### GREEN

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_migration_process.py tests/test_sqlite_migration_integration.py tests/test_sqlite_migration_schema.py -q
```

Output: **205 passed in 44.91s**. Log: `green.log`.

After adding resistant-child, environment inheritance, and inheritable-descriptor checks:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_migration_process.py -q
```

Output: **10 passed in 22.23s**. Log: `green-launcher.log`.

Final backend and peer regressions:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_migration_process.py tests/test_sqlite_migration_integration.py tests/test_sqlite_migration_schema.py tests/test_sqlite_migration_effects.py tests/test_sqlite_migration_metadata.py tests/test_peer_threading_migration.py tests/test_peer_revocation_migration.py -q
```

Output: **330 passed in 57.36s**. Log: `final-focused.log`.

Additional real child-launch failure regression:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_migration_process.py::test_child_launch_failure_is_logged -q
```

Output: **1 passed in 0.73s**. Log: `launch-failure.log`.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && uvx ruff check src/twicc/db/migration_process.py src/twicc/settings_migration.py tests/test_migration_process.py
```

Output: **All checks passed!**

`git diff --check`: passes.
Tests print the expected uv inherited-environment warning. No test warnings or failures occur.
The known-failing full suite is not rerun, per the controller override.

## Files Changed

- `src/twicc/settings.py`
- `src/twicc/settings_test.py`
- `src/twicc/settings_migration.py`
- `src/twicc/db/migration_process.py`
- `src/twicc/cli/run.py`
- `tests/test_migration_process.py`
- `tests/test_sqlite_migration_integration.py`
- `docs/plans/2026-09-30-migration-process-isolation.md`
- `docs/plans/2026-09-30-sqlite-migration-checks-design.md`
- `docs/plans/2026-09-30-sqlite-migration-checks-implementation.md`

The existing review and validation documents remain untouched and excluded from the commit.

## Self-Review

- Read the product diff and launcher source.
- Confirmed normal startup selects standard settings before provider imports.
- Confirmed startup retains its lock and backfill ordering.
- Confirmed child settings do not mutate the runtime engine dictionary.
- Confirmed tests use disposable databases and real subprocesses.
- Confirmed failed child stderr reaches backend.log once through parent failure logging.
- Confirmed no dependencies, migrations, compute versions, restarts, or main checkout changes occur.

## Concerns

- SIGKILL cannot invoke cleanup. No stronger process supervision is added.
- Launcher signal handlers require the main thread, as current synchronous startup does.
- The descriptor regression uses Linux `/proc/self/fd`, matching this task environment.

## Commit

Pending task commit; SHA is appended after creation.

Created: `45290450 fix(sqlite): isolate migration backend in a startup child`.
Trailer: `Co-Authored-By: Codex GPT-6.1 Sol <codex@openai.com>`.
The controller identifies the worker model from its explicit dispatch.

## Review Fix: Test Backend Documentation

- Read `task-1-review.md` and verified both obsolete test-engine statements against `settings_test.py` and integration fixtures.
- Corrected design and implementation guidance: ordinary tests use standard settings; optimized regression fixtures explicitly instantiate the custom backend.
- Product files remain unchanged.
- Verification: `cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks && git diff --check` passes with no output.
- No tests rerun for these documentation-only corrections, per controller instruction.
- Commit: `docs(sqlite): correct ordinary test backend guidance`; SHA follows below.

Review-fix commit: `cf562647 docs(sqlite): correct ordinary test backend guidance`.


## Evidence: task-1-review.md

### Spec Compliance

- ❌ Minor documentation issues found. Runtime isolation and launcher requirements are satisfied.
- `docs/plans/2026-09-30-sqlite-migration-checks-design.md:338` still says test settings select the optimized backend.
- `docs/plans/2026-09-30-sqlite-migration-checks-implementation.md:117` repeats that obsolete selection.
- `src/twicc/settings_test.py:39` selects the standard backend. Update both documentation statements to describe explicit optimized fixtures.
- No missing product behavior or unrelated implementation appears in the supplied diff.
- ⚠️ The diff cannot verify preservation of pre-existing uncommitted documents or the exact worker model. The controller must verify those claims.

### Strengths

- `src/twicc/settings.py:331` selects Django SQLite unconditionally for runtime.
- `src/twicc/settings_migration.py:8` copies database configurations before selecting the migration engine.
- `src/twicc/settings_migration.py:12` accepts only the exact standard fallback value `1`.
- `src/twicc/db/migration_process.py:13` uses the specified interpreter, Django command, settings, and verbosity.
- `src/twicc/db/migration_process.py:29` inherits environment and cwd without a shell. It closes inherited descriptors.
- `src/twicc/db/migration_process.py:40` suppresses repeated interruption during child cleanup.
- `src/twicc/db/migration_process.py:44` terminates, waits, escalates to kill, and reaps before propagating interruption.
- `src/twicc/cli/run.py:565` preserves blocking migration placement before backfill.
- `tests/test_migration_process.py:75` exercises real processes, a real lock, both signals, and a resistant child.
- `tests/test_migration_process.py:18` checks runtime imports after a real database connection.
- `tests/test_migration_process.py:46` verifies real disposable migrations and one normal migration log record.
- `tests/test_migration_process.py:62` verifies failed child tracebacks reach backend.log.

### Issues

#### Critical (Must Fix)

- None.

#### Important (Should Fix)

- None.

#### Minor (Nice to Have)

- `docs/plans/2026-09-30-sqlite-migration-checks-design.md:338` and `docs/plans/2026-09-30-sqlite-migration-checks-implementation.md:117`: obsolete test-engine statements contradict this task. Describe standard ordinary settings and explicit optimized fixtures.
- `.superpowers/sdd/2026-09-30-migration-process-isolation/final-focused.log:1`: uv prints the inherited-environment warning. This is expected environment noise, not a test failure. Future validation can omit inherited VIRTUAL_ENV without using --active.

### Integration Checks

- Child interruption and lock ownership: checked `src/twicc/cli/run.py:510` through migration placement and `src/twicc/cli/run.py:608` release. Cleanup finishes inside the parent lock scope.
- Descriptor isolation: checked `src/twicc/instance_lock.py:126`. The lock uses an OS descriptor. Launcher close_fds prevents inheritance, including the test's explicitly inheritable descriptor.
- Environment, database, and log identity: checked `src/twicc/paths.py:75` and `src/twicc/settings.py:355`. Child reloads the same data-dir configuration and inherits the same database options and file logging configuration.
- Normal runtime driver isolation: reviewed unconditional engine selection and the supplied real-connection regression. No changed runtime import loads the custom backend.
- Read the supplied diff in bounded output passes. No changed product file required a separate full-file read.
- Read final-focused.log and launch-failure.log. They record 330 passing checks and one additional passing launch-failure check.
- No tests rerun. No concrete unanswered behavior doubt requires another run.
- No product edits, index changes, HEAD changes, server operations, or live migrations performed.

### Assessment

**Task quality:** Approved

**Reasoning:** The implementation isolates migration driver state and preserves startup ordering, failure diagnostics, and child cleanup. Two documentation statements need a minor correction.


## Evidence: task-1-rereview.md

### Finding Verdicts

- **Obsolete optimized ordinary-test guidance in design** — ADDRESSED. `docs/plans/2026-09-30-sqlite-migration-checks-design.md:338` states ordinary settings use the standard backend. It distinguishes explicit optimized fixtures.
- **Obsolete optimized ordinary-test guidance in implementation** — ADDRESSED. `docs/plans/2026-09-30-sqlite-migration-checks-implementation.md:117` states the same settings and fixture distinction.

### New Breakage in the Fix Diff

- None. The diff changes only these two documentation statements. Both agree with the previously reviewed implementation.

### Out-of-Scope Observations

- None.

### Checks

- Read `review-45290450..cf562647.diff` once and the appended fix report.
- The fix report records a passing `git diff --check` with no output.
- No tests rerun. This fix changes documentation only, as the controller requires.
- No git commands, product edits, index changes, HEAD changes, or subagents.
- The controller confirms preserved pre-existing documents and worker-model identity.

### Verdict

**Fix round:** All findings addressed, no new Critical/Important breakage.


## Evidence: final-review.md

# Final integration review

**Verdict: With fixes.** One Important interruption defect remains.

Range: `87ffc25f..cf562647`.
Requirements: `docs/plans/2026-09-30-sqlite-migration-checks-design.md` and `docs/plans/2026-09-30-migration-process-isolation.md`.

## Strengths

- Runtime selects standard Django SQLite. Only migration settings select the custom backend.
- The migration logging command imports no custom driver. Normal startup imports only the subprocess launcher.
- The isolated settings copy each database configuration before changing its engine.
- Parent and child use the same inherited data directory, database options, and backend log configuration.
- Explicit `--settings=twicc.settings_migration` selects child settings independently of inherited Django settings.
- Startup retains the instance lock through migration completion, before backfill, server, Watcher, and compute startup.
- Child logging writes normal migration records directly. The parent records failed stderr without replaying successful records.
- Established-child interruption terminates and reaps the child. Resistant-child cleanup escalates after five seconds.
- The child does not inherit the instance lock descriptor.
- Selective validation remains inside the migration transaction. Unknown effects and unproved rebuilds retain global checks.
- Optimized tests explicitly construct the custom backend. Ordinary test settings use the standard backend.
- Manual optimized migration documentation selects the dedicated settings module.

## Issues

### Critical

None found.

### Important

**I1: Interruption during child construction can release the instance lock while the child survives.**

Location: `src/twicc/db/migration_process.py:29`, with cleanup at lines 37–51.

`child` receives the process handle only after `subprocess.Popen()` returns.
A signal can arrive after OS process creation, before that assignment.
The temporary SIGTERM handler raises immediately. Cleanup then sees `child is None` and propagates the exception.
The caller releases its instance lock while the migration child can continue.
Another startup can acquire the lock and start a second migration process.

A focused probe reproduces this boundary with a real sleeping child.
Its Popen wrapper starts that child, sends SIGTERM to the parent, then would return the handle.
The actual launcher catches the resulting exception before receiving that handle.

Observed output:

```text
Migration process did not complete
SystemExit: 143
parent interruption: 143
child alive after launcher propagates interruption: True
```

The probe injects timing deterministically. It does not claim a spontaneous OS scheduling reproduction.
It kills and reaps its child afterward. It runs no migrations and opens no project database.

Existing signal regressions wait for child readiness and therefore cover only established-child cleanup.

**Correction:** Defer SIGINT and SIGTERM exceptions while creating and assigning the process handle.
Record pending interruption, then perform cleanup after ownership is established.
Preserve signal semantics and restoration, including child launch failures.
Avoid inheriting blocked signals into the child if signal masking is used.
Add a regression that injects interruption after process creation but before launcher assignment.
It must prove child termination and reaping before lock release.

### Minor

No new Minor finding.
The inherited `VIRTUAL_ENV` warning remains an accepted project isolation signal.

## Evidence and review coverage

- Read the requirements, task report, initial review, scoped rereview, and progress ledger.
- Read all changed production backend modules and the subprocess/settings integration.
- Read startup lock placement, environment loading, database settings, and logging configuration.
- Read integration fixtures and regression coverage for migration graphs, transaction boundaries, binding guards, and subprocess lifecycle.
- Reviewed the branch diff inventory, changelog entry, and database-directory ignore correction.
- Used the archived whole-branch review and scoped corrections through `b9e81207` as prior integrity evidence.
- Confirmed `final-focused.log` reports **330 passed**, and `launch-failure.log` reports **1 passed**.
- Read the archived broad-failure disposition and standard-backend scheduler reproduction.
- No reported suite reran. Only the concrete launch-window doubt received a focused process probe.
- No product, index, HEAD, dependency, server, or live-migration changes occurred.
- This report is the only review artifact written.

The archived broad suite remains failed.
The unfiltered run reports 32 failures and 11 errors, including the Watcher cleanup cascade.
The controlled run excludes two Watcher watchdog cases and reports 7 failures, 6319 passes, and 21 skips.
Those failures include documented standard-backend failures and a reproduced baseline scheduler race.
These results do not establish a passing full application suite at this head.

## Declined to judge

- Stage-2 table-rebuild optimization: the approved milestone explicitly retains global checks pending preservation proof.
- SIGKILL and interpreter crashes: parent cleanup cannot run; the plan explicitly records this supervision limit.
- Deliberate raw-driver guard bypass or replacement: the approved migration contract excludes these customizations.
- Full rollback for `atomic=False`: Django permits partial committed effects; the design explicitly preserves that limitation.
- Production restart timing and live-data safety: live migrations and server operations are outside this review's authorization.
- Other Python, Django, or SQLite versions: current evidence covers the installed versions, not a compatibility matrix.
- Fixing Watcher, scheduler, provider-configuration, or fixed-date test failures: archived standard-backend evidence places these outside this change.
- Main-branch integration and deployment: the request authorizes isolated review only.

## Final integration judgment

**Ready to merge: With fixes.**

The process boundary removes the custom driver from ordinary runtime and preserves migration behavior.
Fix I1 before integration: catchable interruption must not leave a migration child beyond parent lock release.
The baseline test failures remain explicit validation limits, not new branch findings or passing-suite evidence.

## Exact launch-window probe

The review executes this process-only probe with `.venv/bin/python -` from the worktree.
It imports the launcher but performs no Django setup or database operation.

```python
import os
import signal
import subprocess
import sys
from twicc.db import migration_process

original = subprocess.Popen
spawned = []
def interrupt_before_return(command, **kwargs):
    process = original([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
    spawned.append(process)
    os.kill(os.getpid(), signal.SIGTERM)
    return process
migration_process.subprocess.Popen = interrupt_before_return
try:
    try:
        migration_process.run_migrations()
    except SystemExit as error:
        print('parent interruption:', error.code)
    print('child alive after launcher propagates interruption:', spawned[0].poll() is None)
finally:
    for process in spawned:
        if process.poll() is None:
            process.kill()
        process.communicate()
```

The probe exits zero after its own cleanup.
The launcher traceback points to line 29 for Popen and line 20 for the SIGTERM handler.


## Evidence: final-fix-report.md

# Final review I1 fix

## Change

- Temporary Python handlers defer SIGINT and SIGTERM during Popen construction and child-handle assignment.
- The launcher restores ordinary SIGINT handling after assignment and replays the pending signal.
- SIGINT preserves KeyboardInterrupt; SIGTERM preserves SystemExit(143).
- Existing cleanup terminates and reaps the child before interruption propagation.
- Original handlers are restored on success, interruption, and launch failure.
- Python signal handlers avoid inherited blocked signals. No process signal mask changes occur.
- Changes affect only the product launcher and its regression tests.

## TDD and verification

All commands start with:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-sqlite-migration-checks &&
```

RED command:

```bash
TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_migration_process.py -k 'construction or restores' -q
```

Result: **2 failed, 1 passed, 11 deselected in 3.10s**.
Both construction regressions fail because the real child remains unreaped before lock release.
Full output: `final-fix-red.log`.
The launch-failure restoration regression already passes before the fix.

An initial boundary GREEN attempt finds an incorrect test assumption.
`InstanceLock.acquire()` raises `InstanceAlreadyRunning`; it does not return false.
The contender now catches that documented exception.
The corrected boundary run reports **3 passed, 11 deselected in 3.87s**.

The tests then add child signal-mask checks and repeated signals during cleanup.
The first covering run reports **2 failed, 208 passed in 104.33s**.
The repeated-signal callback remains attached to `child.communicate` and fires during test cleanup after handler restoration.
The callback now restores the original method before signal injection.
Full intermediate output: `final-fix-intermediate.log`.
The corrected boundary command reports **3 passed, 11 deselected in 4.23s**.
Full output: `final-fix-green-boundary.log`.

Final covering command:

```bash
TWICC_DATA_DIR=$PWD uv run --extra test python -m pytest tests/test_migration_process.py tests/test_sqlite_migration_integration.py tests/test_sqlite_migration_schema.py -q
```

Final result follows below. Full output: `final-fix-green.log`.

Static checks:

```bash
uvx ruff check src/twicc/db/migration_process.py tests/test_migration_process.py
```

Output: **All checks passed!**

```bash
git diff --check
```

Output: empty; exit status zero.

The expected uv warning states that the inherited main-repository VIRTUAL_ENV is ignored.
No full application suite runs, per the controller's explicit instruction.

## Self-review

- Reviewed the complete launcher and test diff.
- Real children start before injected signals; no mocked process handles substitute for lifecycle evidence.
- `waitpid` confirms reaping before the lock context exits.
- A separate real contender confirms the parent still owns the instance lock after reaping.
- Child readiness confirms SIGINT and SIGTERM are not blocked in the child.
- Repeated real SIGINT and SIGTERM during cleanup do not interrupt reaping.
- Original handlers survive launch failure and construction interruption.
- Established-child interruption and resistant-child escalation retain their covering regressions.
- No settings, dependencies, schema migrations, live databases, server operations, or main-checkout files change.
- Root-owned pending review and validation documents remain untouched and unstaged.
- SIGKILL and interpreter crashes retain the existing supervision limit.

## Commit

Commit and final verification follow below.

Final covering result: **210 passed in 103.17s (0:01:43)**; exit status zero.
The final output contains no test failures or warnings beyond the accepted uv environment warning.

Commit command:

```bash
git add src/twicc/db/migration_process.py tests/test_migration_process.py && git commit -m 'fix(sqlite): defer interruptions until migration child ownership' -m 'Defer SIGINT and SIGTERM while constructing the migration subprocess. Replay pending interruption after assigning the child handle, so cleanup terminates and reaps the child before the parent releases its instance lock. Restore original handlers on every exit without blocking signals inherited by the child.' -m 'Add real process regressions for both construction-boundary signals, lock retention, child signal masks, repeated cleanup signals, and launch-failure handler restoration.' -m 'Co-Authored-By: Codex GPT-6.1 Sol <codex@openai.com>'
```

Output:

```text
[bugfix/sqlite-migration-checks 52df4e31] fix(sqlite): defer interruptions until migration child ownership
 2 files changed, 113 insertions(+), 2 deletions(-)
```

Only the two task-owned product/test files enter the commit.
The report and logs stay under the ignored `.superpowers/sdd/2026-09-30-migration-process-isolation/` evidence directory.


## Evidence: final-rereview.md

## Finding Verdicts

- **I1: Construction interruption leaves a migration child beyond parent lock release — ADDRESSED.**
- `src/twicc/db/migration_process.py:21`: temporary handlers record interruption without raising during construction.
- `src/twicc/db/migration_process.py:41`: assignment establishes child ownership before pending interruption propagates.
- `src/twicc/db/migration_process.py:44`: pending SIGINT invokes the previous Python handler, preserving normal `KeyboardInterrupt` behavior.
- `src/twicc/db/migration_process.py:23`: an ignored SIGINT remains ignored during construction.
- `src/twicc/db/migration_process.py:49`: pending SIGTERM raises `SystemExit(143)` through the existing interruption handler.
- `src/twicc/db/migration_process.py:57`: interruption then reaches child termination and reaping before propagation.
- `src/twicc/db/migration_process.py:74`: both original handlers are restored, including after child launch failure.
- `src/twicc/db/migration_process.py:37`: Python handlers defer exceptions without changing the signal mask inherited by the child.

## New Breakage in the Fix Diff

- **None.** No new Critical, Important, or Minor finding.

## Checks

- Read the scoped rereview instructions, task brief, fix report, and supplied `cf562647..52df4e31` diff.
- Compared the fix against I1 and its exact deterministic launch-window reproduction in the original review.
- Checked the added real-process regression for both SIGINT and SIGTERM.
- The regression uses `waitpid` to prove reaping while the instance lock context remains active.
- A separate contender verifies lock ownership after reaping.
- The child checks that neither signal is blocked.
- Repeated cleanup signals occur while the launcher ignores interruption; the test restores its communicate wrapper before final cleanup.
- The launch-failure regression checks restoration of both original handlers.
- Confirmed `final-fix-green.log` reports **210 passed in 103.17s**.
- The report identifies launcher, migration integration, and schema tests as the covering suite.
- The report records intermediate test-harness failures and their corrections without presenting them as successful runs.
- No tests rerun. The supplied evidence answers the scoped behavior questions.
- No git commands, product edits, index changes, HEAD changes, server operations, live migrations, or subagents.

## Out-of-Scope Observations

- None new. The original report's declined judgments and broad-suite limits remain unchanged.

## Verdict

**Fix round: All findings addressed, no new Critical/Important breakage.**


## Evidence: progress.md

# SDD ledger — plan: docs/plans/2026-09-30-migration-process-isolation.md

Base: b9e812070033ce1252f1645198b13dc2e4c78d43

The user explicitly authorizes implementation of the previously discussed design. Existing worktree isolation and SDD execution remain authorized.

| Preflight interface | Producer / consumer | Result |
| --- | --- | --- |
| Task 1 settings / launcher | Migration settings select engine; launcher explicitly selects settings | Consistent |
| Task 1 startup / launcher | Startup owns instance lock; launcher waits and reaps | Consistent |
| Task 1 tests / implementation | Real disposable migration proves isolation and shared paths; mocks cover launch failures | Consistent |

Ruling: Use one integration task for settings, launcher, startup, and tests — these form one blocking-startup contract — if wrong, review or rework becomes larger.
Ruling: Reuse focused backend and startup checks rather than rerun the known-failing full suite — prior failures have standard-backend reproductions and this change removes the custom driver from runtime — if wrong, a wider regression may require additional validation.

Task 1: dispatched; no completed tasks in this plan.

Task 1: implementation 45290450; 330 covering tests pass plus one additional launch-failure regression. Task review approves product quality; two obsolete test-engine documentation statements receive spec failure. Fix round 1 dispatched for documentation only.
Controller verification: pre-existing review/validation edits remain uncommitted and preserved. Worker model explicitly selected gpt-6.1-sol; trailer matches.
Task 1: minor (deferred): expected uv inherited VIRTUAL_ENV warning. Project instructions explicitly classify this warning as correct isolation; no environment override introduced.

Task 1: fix round 1/5 (2 addressed, 0 open; documentation-only commit cf562647).
Task 1: complete (commits b9e81207..cf562647, review clean).
Final whole-branch integration review pending. Prior selective-backend scoped review is clean at b9e81207; prior broad failures remain documented baseline limits.

Final review cf562647: one Important I1, SIGINT/SIGTERM between child creation and Popen return loses handle. Single final fix dispatched to fresh worker migration_isolation_final_fix. Scoped re-review follows.
Final review declined judgments accepted as scope limits: Stage-2 rebuilds, SIGKILL/crashes, deliberate raw-driver bypass, atomic=False total rollback, live timing/data operations, other runtime versions, baseline failure corrections, main deployment. None is silently claimed verified.

Final fix 52df4e31: covering launcher/integration/schema suite 210 passed; Ruff and diff check pass. Final scoped re-review dispatched. Intermediate regression-test cleanup errors documented and corrected; no additional product finding.

Final scoped review: I1 ADDRESSED; no new Critical/Important/Minor. Product HEAD52df4e31. Implementation and reviews complete. Keep isolated worktree, no merge/push/restart/live migrate. Archive reports and ledger into isolation-validation.md.


## Validation limits

Final covering tests report 210 passed after the launcher fix. The earlier broader focused run reports 330 passed, plus one later launch-failure regression. The previous full application suite remains failed with documented standard-engine reproductions; it is not rerun for this bounded isolation change. No all-green full-suite claim is made. Stage-2 table-rebuild optimizations and live database-copy timing remain outside this task. SIGKILL cannot execute Python cleanup; the launcher runs in the startup main thread. No main integration occurs.
