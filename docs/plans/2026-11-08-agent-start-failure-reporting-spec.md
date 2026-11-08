# Agent start failure reporting specification

Status: Ready for user review after adversarial specification review. Implementation is not authorized by this document.

## Intent

When TwiCC cannot launch an agent, the user must know two things:

- The agent did not start.
- Why it did not start, when TwiCC can tell.

Today, the same failure produces a misleading error, or no error at all.
This document specifies the behavior and the acceptance criteria.
It does not prescribe functions, classes or call sites.

## Scope

This change covers every launch of a Claude Code (SDK and hybrid) or Codex agent that a caller requests with a message or a built-in command, and that runs as part of that request:

- A send from the web UI to an existing session with no live agent.
- A new session created from the web UI.
- `send-message`, `send-messages` and `create-session`, through the CLI and MCP.
- A restart performed inside the send that requested it, after a startup settings change.

The agent-facing documentation of these commands is in scope: the skills and `SKILLS-AND-CLI.md`.

### Non-goals

- Resuming a session whose working directory no longer exists. This change only reports the failure. A later step defines any recovery, for example choosing another directory.
- The wording, buttons and recovery of a real `send_uncertain` state. A failure at or after handoff keeps its current behavior.
- Failures after the agent has started, for example a process crash during a turn.
- Failures of a hybrid session after its tmux session exists. This includes a process exit before the first paste, and the first paste itself.
- Changes to the protocol of a provider, or to the vendored Codex SDK.

## Verified current behavior

These facts were checked against the code on 2026-11-08.

| Launch | Failure point | Result today |
|---|---|---|
| Codex resume | `FileNotFoundError` from `subprocess.Popen` when the `cwd` is absent, during the thread resume | The classifier returns `uncertain`, because it only knows three rejection cases. The user sees "Message delivery remains unresolved; do not retry", with disabled buttons. |
| Codex new session | Same exception, during the thread start | The exception is not a `RuntimeError`. It reaches the generic handler: "Failed to send message: [Errno 2]...". |
| Claude SDK, send to an existing session | `CLIConnectionError: Working directory does not exist: <path>`, during `connect()` | `start()` catches the error and the agent becomes `DEAD`. The send still returns success and the backend sends `send_ack`. The frontend then removes the message. |
| Claude SDK, new session or CLI/MCP send | Same | The input receipt settles as `uncertain`. The caller gets `send_uncertain`. |
| Claude hybrid | No failure | `tmux new-session -c <absent directory>` succeeds. The pane starts in `$HOME`. |

Other facts:

- On a Codex resume, the original exception does not reach the log. The WebSocket layer logs only the wrapper message.
- In Codex, a message leaves TwiCC when the turn opens. This happens after the agent is created and the thread is attached.
- A Codex built-in command can launch an agent. It runs after the thread is attached, and opens no turn before its first provider call.
- In the Claude SDK path, the message leaves TwiCC in `query()`, after `connect()`.
- An error frame with a code other than `send_uncertain` enables Retry, Edit and Delete and removes the optimistic message, if its in-flight send still exists.
- A `DEAD` state with `kill_reason` `error` or `startup-failed` can reach the browser first. It then consumes the in-flight send, as `agent_died`, and the later error frame finds nothing.
- An error frame reaches only the browser tab that sent the message.
- A send retry uses a new request identifier.
- A cron restart already stops when the session directory does not exist.

## Definitions

**Handoff** is the moment TwiCC gives the message or command to the provider process.

| Launch | Handoff |
|---|---|
| Codex message | Opening the turn |
| Codex built-in command | The first provider call of the command |
| Claude SDK | `query()` |

A hybrid launch ends when its tmux session exists. Nothing after that point is an agent start failure.

**Launch directory** is the directory that the launch will use as its working directory.

**Agent start failure** is any failure of a launch before handoff, with two exceptions:

- An error that already has a specific send code, for example `session_unavailable` or an attachment error.
- A refusal based on the state of the session or the content of a command, for example an agent that is already active, or a usage error of a built-in command.

TwiCC had not yet tried to send the message. TwiCC therefore knows that the message was not sent.

A failure at or after handoff is not an agent start failure. It keeps its current classification.

## Requirements

### R1. A distinct outcome

An agent start failure is reported with the code `agent_start_failed` and a machine-readable reason.
It is never reported as `send_uncertain`, and never acknowledged as delivered.

R1 and R3 to R8 apply to the launches listed in Scope. R2 applies to every launch, with the exception that it states. R10 and R11 define the other cases.

### R2. Launch directory check

Before a launch, TwiCC checks that the launch directory exists and is a directory.
If not, the launch does not happen and the failure has the reason `working_directory_missing`.

This check applies to all providers. It is the only way to detect this case for hybrid sessions.
It runs after the existing session availability check.

It does not apply when TwiCC adopts a hybrid process that is already running.

### R3. Reasons

| Reason | Meaning |
|---|---|
| `working_directory_missing` | The launch directory does not exist, or is not a directory, at the time of the failure. |
| `cli_not_found` | The provider program is absent. |
| `permission_denied` | The operating system refused access. |
| `start_failed` | Any other cause. It carries a diagnostic. |

`working_directory_missing` takes priority, because a directory test does not depend on provider error text.
Authentication failures keep their existing handling.

### R4. User message

The web UI shows the failure in the existing failed-send banner. The message states:

- that the named provider did not start;
- the reason in plain words;
- the launch directory path, for `working_directory_missing`;
- the diagnostic, for `start_failed`.

The text uses i18n in all supported languages.
The diagnostic is provider text. It is displayed as received, with a length limit.

The banner shows this message even if a state change reaches the browser before the failure. `agent_start_failed` takes priority over `agent_died`.

### R5. User actions

After an agent start failure, the user can Retry, Edit and Delete the message.
The message text and attachments are preserved under the existing failed-send rules.

### R6. Session state

No session remains in `starting` after an agent start failure, in any browser tab.
The failure does not produce a second notification in the browser tab that shows the banner.

### R7. Durable send state

An agent start failure leaves no pending or uncertain delivery record.
Codex question answers attached to the failed send become available again.
Input completion receipts settle as rejected.

### R8. CLI and MCP

`send-message`, `send-messages` and `create-session` return the rejection with the code `agent_start_failed`, the reason, and the same explanation.
The result means that the message was not delivered and that a new send is safe.
The agent-facing documentation describes this code and this meaning.

### R9. Diagnostics in logs

The backend logs an agent start failure once, with the original exception and its traceback.
A failure of the R2 check has no original exception. Its log entry states the session and the launch directory.
Ephemeral runs keep their rule: the log contains no user content and no exception text or traceback, only the exception type. The session and the launch directory are not user content.

### R10. Automatic launches

These launches do not run as part of the request that caused them:

- Cron restarts.
- Restarts of parked messages performed outside the send that requested them.

R1 and R3 to R8 do not apply to them. They keep their current user-visible behavior, including retries and notifications.
A failure of the R2 check on these launches produces the same notification as any other failed start, and the session does not remain in `starting`.
An agent start failure must not escape into the code that started them.

### R11. Ephemeral runs

An ephemeral run keeps its existing generic start failure presentation, whatever the reason.
R1 and R4 do not change what it shows.

## Accepted limitations

- When a CLI or MCP caller causes the failure, a person who watches the same session in the web UI gets no banner. A second browser tab gets no banner either. The party that sent the message receives the error.
- A CLI or MCP send that is parked behind a cron restart keeps its current outcome, under R10. It can end as `send_uncertain` or as a timeout of the caller.
- If the launch directory is removed between the check and the launch, the launch fails and the failure is classified at that time.
- If the launch directory disappears from a live agent that is then restarted for a settings change, TwiCC stops the agent before it finds the failure. An inline restart reports the failure. Other restarts follow R10.
- The Claude CLI writes its startup errors to stderr. Without an added capture, the `start_failed` diagnostic can be generic on that path.

## Acceptance criteria

| # | Requirement | Scenario | Expected result |
|---|---|---|---|
| A1 | R1, R3, R4, R5 | Web UI, Codex session, launch directory removed, send a message | Banner states that Codex did not start and names the directory. Retry, Edit and Delete are active. No `send_uncertain`. |
| A2 | R1, R3, R4, R5 | Same with Claude SDK, message with attachments | Same. No `send_ack`. The message and its attachments are not lost. |
| A3 | R2 | Same with a hybrid session | Same. No tmux session is created. |
| A4 | R1, R4 | Web UI, new session or open draft, project directory removed and project not yet marked stale, each provider | Same banner. |
| A5 | R3 | Launch directory replaced by a file | Reason `working_directory_missing`. |
| A6 | R3 | Provider program absent | Reason `cli_not_found`. |
| A7 | R3 | The operating system refuses the launch | Reason `permission_denied`. |
| A8 | R3, R4 | Launch fails for another cause before handoff, including after the Claude SDK agent is registered | Reason `start_failed`, with the diagnostic, in the banner, within the length limit. No `agent_died` banner. |
| A9 | Scope | Inline restart after a startup settings change, launch fails | Same banner. No session remains in `starting`. |
| A10 | R8 | CLI or MCP `send-message` or `send-messages` to a session whose launch directory was removed | Rejection with `agent_start_failed` and the reason. |
| A11 | R8 | CLI or MCP `create-session` in a project whose directory was removed | Same rejection. |
| A12 | R7 | Codex send with question answers fails to start | The answers can be sent again. No uncertain delivery record remains. |
| A13 | R7 | CLI send with a completion wait fails to start | The wait ends with the rejection. |
| A14 | R6 | Two browser tabs show the session | No tab remains in `starting`. |
| A15 | R6 | The tab that shows the banner | No "terminated" toast for the same failure. |
| A16 | R5 | Directory restored, then Retry | The message is delivered once. |
| A17 | R10 | Automatic restart of a parked message fails to start | Same user-visible behavior as today. |
| A18 | R10 | Cron restart fails to start | Same behavior as today. |
| A19 | Definitions | Failure at or after handoff | Same behavior as today. |
| A20 | Definitions | Codex built-in command launches an agent, process cannot start | Reported as `agent_start_failed`. |
| A21 | R2 | Hybrid adoption of a running process | Not refused by the directory check. |
| A22 | R9 | Backend log after A6 or A8, and after A1 | After A6 or A8: the original exception and traceback, once. After A1: one entry that states the session and the launch directory. |
| A23 | R9, R11 | Ephemeral run fails to start | Generic presentation. The log contains no user content, no exception text and no traceback. |
| A24 | R8 | Agent-facing documentation | Describes `agent_start_failed`. |
| A25 | R4 | A1 and A8 in each supported language | The banner is translated. The path and the diagnostic stay as received. |
| A26 | R2 | Session history unavailable and launch directory removed | The existing `session_unavailable` error. |
