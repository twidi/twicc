# Agent Start Failure Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Ready for user review after adversarial plan review. Implementation is not authorized by this document.

**Goal:** When a launch fails before TwiCC tries to send the message, report `agent_start_failed` with a reason, instead of `send_uncertain`, a generic error or a false delivery acknowledgement.

**Architecture:** One error type and one classifier live in `twicc.agent`. The base manager checks the launch directory and classifies every launch failure that happens before the agent marks its handoff. Each agent marks that handoff at the point that the spec defines. Automatic launches opt out of the classification. A dedicated `kill_reason` keeps the failed-send banner as the single notification, with no frontend logic change.

**Tech Stack:** Python 3.13, Django 6 ASGI, claude-agent-sdk, vendored openai_codex, Vue 3, vue-i18n, pytest, node:test.

**Spec:** `docs/plans/2026-11-08-agent-start-failure-reporting-spec.md`

## Global Constraints

- Failure code: `agent_start_failed`. Reasons: `working_directory_missing`, `cli_not_found`, `permission_denied`, `start_failed`.
- `working_directory_missing` takes priority over every other reason.
- Handoff: Codex message = opening the turn. Codex built-in command = its first provider call. Claude SDK = `query()`. A hybrid launch ends when its tmux session exists.
- Automatic launches (cron restarts, parked restarts outside the send) keep their user-visible behavior. An agent start failure must not escape into the code that started them.
- Ephemeral runs keep the generic presentation. Their log has no user content, no exception text and no traceback, only the exception type.
- All interface text uses i18n, in `en`, `de`, `es`, `fr`, `pt-BR`. Follow `docs/i18n-glossary.md`: "directory" is `répertoire`, `Verzeichnis`, `directorio`, `diretório`.
- `orjson`, never stdlib `json`. `NamedTuple` for immutable data.
- Do not run `git stash`, `git restore`, `git reset --hard`, `git clean`, or a branch switch. The checkout is shared.
- Do not modify any file in `docs/plans/` during implementation.
- **Commit steps run only if the owner has asked for commits.** Stage files by name, then check `git diff --cached --name-only`. Subject: Conventional Commit, lowercase. Always add a body. End with `Co-Authored-By: Claude MODEL <noreply@anthropic.com>`, with the exact name of the running model. No plugin version in a commit title.
- Python tests: `uv run pytest`. Frontend tests: `cd frontend && npm test`. Never add `TWICC_DATA_DIR=` to a pytest command.
- In tests, assert on logs by patching the module logger. Do not use `caplog`: other tests disable logging.

## Review Focus

1. **A launch directory that is a file.** The launch stops with `working_directory_missing`. Test in Task 1.
2. **A Claude SDK failure after `connect()` and before `query()`.** It is an agent start failure, not `send_uncertain`. Test in Task 3.
3. **A Claude SDK failure inside `query()`.** It stays uncertain, with the current process error. Test in Task 3.
4. **An existing specific send error raised during a launch.** It keeps its code, for example an attachment error. Tests in Tasks 1 and 2.
5. **A very long diagnostic.** The displayed text stays within the limit. Test in Task 1.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/twicc/agent/exceptions.py` | Modify | `AgentStartError` and the code constant |
| `src/twicc/agent/states.py` | Modify | The dedicated `kill_reason` |
| `src/twicc/agent/start_failure.py` | Create | Directory check, classifier, logging |
| `src/twicc/providers/claude_code/bin.py` | Modify | Typed "binary absent" error |
| `src/twicc/agent/base_agent.py` | Modify | `start_failure`, `handoff_attempted`, `report_start_failure` attributes |
| `src/twicc/agent/base_manager.py` | Modify | R2 check, classification before handoff, raise of a recorded failure |
| `src/twicc/core/services/timeline_filters.py` | Modify | The new reason is a problem reason |
| `frontend/src/utils/timeline/describeEvent.js` | Modify | Timeline row of the new reason |
| `src/twicc/providers/codex/agent/agent.py` | Modify | Codex handoff mark, failure before the turn opens |
| `src/twicc/providers/claude_code/agent/agent.py` | Modify | Claude SDK handoff mark, recorded failure |
| `src/twicc/providers/claude_code/agent/manager.py` | Modify | Who waits for the launch, automatic launches |
| `src/twicc/providers/claude_code/agent/hybrid/agent.py` | Modify | Hybrid launch end mark |
| `frontend/src/stores/data.js` | Modify | The banner stays translated after a reload |
| `android/app/src/main/assets/i18n/sources.sha256` | Regenerate | Fingerprint of the catalogs |
| `src/twicc/asgi.py` | Modify | WS frame, log |
| `src/twicc/core/services/send_message.py` | Modify | CLI and MCP send result |
| `src/twicc/core/services/session_creation.py` | Modify | CLI, MCP and WS create result |
| `src/twicc/drop_requests_watcher.py` | Modify | `reason` in rejected status files |
| `frontend/src/i18n/locales/*.json` | Modify | Four messages, five catalogs |
| `src/twicc/agent/plugin/twicc/skills/*/SKILL.md`, `.claude-plugin/plugin.json`, `SKILLS-AND-CLI.md` | Modify | Agent-facing documentation |
| `tests/test_agent_start_failure.py` | Create | Classifier, base manager, Claude SDK, hybrid and status tests |

No frontend logic changes. The existing error frame already drives the banner and enables Retry, Edit and Delete. The new `kill_reason` is absent from both lists in `frontend/src/composables/useWebSocket.js` (toast at line 902, `agent_died` at line 930).

---

### Task 1: Failure model, classifier and messages

**Files:**
- Modify: `src/twicc/agent/exceptions.py`
- Modify: `src/twicc/agent/states.py` (after `DELIBERATE_STOP_REASONS`, line 57)
- Modify: `src/twicc/providers/claude_code/bin.py:24-38`
- Create: `src/twicc/agent/start_failure.py`
- Modify: `frontend/src/i18n/locales/{en,de,es,fr,pt-BR}.json` (after `backend.agent.ephemeralStartFailed`, line 3743)
- Test: `tests/test_agent_start_failure.py`

**Interfaces:**
- Produces:
  - `twicc.agent.exceptions.AGENT_START_FAILED = "agent_start_failed"`
  - `twicc.agent.exceptions.AgentStartError(message: str, *, reason: str, diagnostic: str | None = None, ui_message: dict | None = None)`, a `SendDeliveryError` with `code=AGENT_START_FAILED`, and attributes `reason` and `diagnostic`.
  - `twicc.agent.states.AGENT_START_FAILED_KILL_REASON = "agent-start-failed"`
  - `twicc.providers.claude_code.bin.ClaudeBinaryNotFound(FileNotFoundError)`
  - In `twicc.agent.start_failure`:
    - `DIAGNOSTIC_MAX_CHARS = 500`
    - `REASON_WORKING_DIRECTORY_MISSING`, `REASON_CLI_NOT_FOUND`, `REASON_PERMISSION_DENIED`, `REASON_START_FAILED`
    - `launch_directory_failure(directory: str, *, provider: Provider, ephemeral: bool = False) -> AgentStartError | None`
    - `classify_start_failure(exc: BaseException, *, directory: str, provider: Provider, ephemeral: bool = False) -> SendDeliveryError`
    - `log_start_failure(exc: BaseException, *, session_id: str, ephemeral: bool) -> None`

- [ ] **Step 1: Write the failing tests in `tests/test_agent_start_failure.py`**

```python
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from claude_agent_sdk import CLIConnectionError, CLINotFoundError
import pytest

from twicc.agent.exceptions import AgentStartError, SendDeliveryError
from twicc.agent.start_failure import (
    DIAGNOSTIC_MAX_CHARS, classify_start_failure, launch_directory_failure,
)
from twicc.core.enums import Provider
from twicc.core.ui_messages import ui_message, ui_message_text
from twicc.providers.claude_code.bin import ClaudeBinaryNotFound
from twicc.providers.codex.bin import CodexRuntimeNotReady
from twicc.providers.codex.runtime import CodexRuntimeError


def test_directory_check_accepts_a_directory(tmp_path):
    assert launch_directory_failure(str(tmp_path), provider=Provider.CODEX) is None


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_directory_check_refuses_a_missing_directory_or_a_file(tmp_path, kind):
    path = tmp_path / "target"
    if kind == "file":
        path.write_text("x")
    failure = launch_directory_failure(str(path), provider=Provider.CODEX)
    assert isinstance(failure, AgentStartError)
    assert failure.code == "agent_start_failed"
    assert failure.reason == "working_directory_missing"
    assert str(failure) == f"Codex did not start: the working directory {path} does not exist."
    assert failure.ui_message == ui_message(
        "backend.agent.startFailed.working_directory_missing", {"provider": "Codex", "directory": str(path)},
    )


def test_missing_directory_wins_over_a_provider_error(tmp_path):
    failure = classify_start_failure(
        CLINotFoundError("absent"), directory=str(tmp_path / "gone"), provider=Provider.CLAUDE_CODE,
    )
    assert failure.reason == "working_directory_missing"


@pytest.mark.parametrize("error", [
    CLINotFoundError("absent"), ClaudeBinaryNotFound("absent"), CodexRuntimeNotReady("absent"),
    CodexRuntimeError("download failed"),
])
def test_absent_program(tmp_path, error):
    failure = classify_start_failure(error, directory=str(tmp_path), provider=Provider.CODEX)
    assert failure.reason == "cli_not_found"
    assert failure.ui_message == ui_message("backend.agent.startFailed.cli_not_found", {"provider": "Codex"})


def test_permission_error_is_found_through_the_cause_chain(tmp_path):
    wrapper = CLIConnectionError("Failed to start Claude Code: denied")
    wrapper.__cause__ = PermissionError(13, "denied")
    failure = classify_start_failure(wrapper, directory=str(tmp_path), provider=Provider.CLAUDE_CODE)
    assert failure.reason == "permission_denied"
    assert failure.ui_message == ui_message(
        "backend.agent.startFailed.permission_denied", {"provider": "Claude Code"},
    )


def test_other_causes_carry_a_bounded_diagnostic(tmp_path):
    failure = classify_start_failure(
        RuntimeError("x" * 5000), directory=str(tmp_path), provider=Provider.CODEX,
    )
    assert failure.reason == "start_failed"
    assert len(failure.diagnostic) == DIAGNOSTIC_MAX_CHARS
    assert failure.diagnostic.endswith("…")
    assert failure.ui_message == ui_message(
        "backend.agent.startFailed.start_failed", {"provider": "Codex", "diagnostic": failure.diagnostic},
    )


def test_a_known_error_stays_inside_the_did_not_start_message(tmp_path):
    error = RuntimeError(ui_message_text("Hybrid off", "backend.claude.hybrid_disabled"))
    failure = classify_start_failure(error, directory=str(tmp_path), provider=Provider.CODEX)
    assert failure.reason == "start_failed"
    assert failure.ui_message == ui_message("backend.agent.startFailed.start_failed", {
        "provider": "Codex", "diagnostic": ui_message("backend.claude.hybrid_disabled"),
    })


def test_an_existing_send_error_is_returned_unchanged(tmp_path):
    error = SendDeliveryError("bad attachment", code="attachment_missing")
    assert classify_start_failure(error, directory=str(tmp_path), provider=Provider.CODEX) is error


@pytest.mark.parametrize("error", [RuntimeError("secret prompt"), CLINotFoundError("secret")])
def test_ephemeral_failures_stay_generic(tmp_path, error):
    failure = classify_start_failure(error, directory=str(tmp_path), provider=Provider.CODEX, ephemeral=True)
    assert failure.code == "agent_start_failed"
    assert failure.diagnostic is None
    assert "secret" not in str(failure)
    assert failure.ui_message == ui_message("backend.agent.ephemeralStartFailed")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_start_failure.py -v`
Expected: FAIL with `ImportError` on `AgentStartError`.

- [ ] **Step 3: Add `AgentStartError`, the kill reason and `ClaudeBinaryNotFound`**

`ClaudeBinaryNotFound` keeps the current `ui_message_text` message of `resolve_bundled_binary`. Only the raised class changes.

- [ ] **Step 4: Implement `src/twicc/agent/start_failure.py`**

Messages, in priority order. `{provider}` is `get_provider_helpers(provider).LABEL`. Each reason uses the catalog key `backend.agent.startFailed.<reason>`.

| Reason | Detection | English text | Params |
|---|---|---|---|
| `working_directory_missing` | `not os.path.isdir(directory)` | `{provider} did not start: the working directory {directory} does not exist.` | `provider`, `directory` |
| `cli_not_found` | a `CLINotFoundError`, `ClaudeBinaryNotFound`, `CodexRuntimeNotReady` or `CodexRuntimeError` in the `__cause__` chain | `{provider} did not start: its program was not found.` | `provider` |
| `permission_denied` | a `PermissionError` in the `__cause__` chain | `{provider} did not start: the system refused access.` | `provider` |
| `start_failed` | anything else | `{provider} did not start: {diagnostic}` | `provider`, `diagnostic` |

- A `SendDeliveryError` input is returned unchanged, before any other test.
- `diagnostic` is `str(exc)`. Above `DIAGNOSTIC_MAX_CHARS`, cut it so that the result with a final `…` has exactly that length.
- If `exc` has its own UI message (`exc.ui_message`, or `exc.args[0].ui_message`), the `diagnostic` param of `start_failed` is that descriptor, so the banner still says that the provider did not start. Descriptors nest as params: `frontend/src/i18n/messageDescriptors.js:60-61`. The `diagnostic` attribute stays `str(exc)`.
- With `ephemeral=True`: the same `reason`, `diagnostic=None`, the text `The ephemeral run could not start.` and `ui_message("backend.agent.ephemeralStartFailed")`.
- Import the provider error classes inside the function that uses them, because `twicc.agent` must not import the provider packages at module load.
- `log_start_failure`: non-ephemeral, `logger.error("Agent failed to start for session %s", session_id, exc_info=exc)`. Ephemeral, `logger.error("Ephemeral agent failed to start (%s)", type(exc).__name__)`.

- [ ] **Step 5: Add the four catalog entries in each locale, in the same position**

| Key suffix | `en` | `fr` | `de` | `es` | `pt-BR` |
|---|---|---|---|---|---|
| `working_directory_missing` | `{provider} did not start: the working directory {directory} does not exist.` | `{provider} n'a pas démarré : le répertoire de travail {directory} n'existe pas.` | `{provider} wurde nicht gestartet: Das Arbeitsverzeichnis {directory} existiert nicht.` | `{provider} no se ha iniciado: el directorio de trabajo {directory} no existe.` | `{provider} não foi iniciado: o diretório de trabalho {directory} não existe.` |
| `cli_not_found` | `{provider} did not start: its program was not found.` | `{provider} n'a pas démarré : son programme est introuvable.` | `{provider} wurde nicht gestartet: Das Programm wurde nicht gefunden.` | `{provider} no se ha iniciado: no se encontró su programa.` | `{provider} não foi iniciado: o programa não foi encontrado.` |
| `permission_denied` | `{provider} did not start: the system refused access.` | `{provider} n'a pas démarré : le système a refusé l'accès.` | `{provider} wurde nicht gestartet: Das System hat den Zugriff verweigert.` | `{provider} no se ha iniciado: el sistema denegó el acceso.` | `{provider} não foi iniciado: o sistema negou o acesso.` |
| `start_failed` | `{provider} did not start: {diagnostic}` | `{provider} n'a pas démarré : {diagnostic}` | `{provider} wurde nicht gestartet: {diagnostic}` | `{provider} no se ha iniciado: {diagnostic}` | `{provider} não foi iniciado: {diagnostic}` |

In `fr`, the space before `:` is a no-break space (U+00A0), as in the rest of `fr.json`.

- [ ] **Step 6: Regenerate the Android fingerprint of the catalogs**

Run: `cd frontend && npm run i18n:android:generate`
Run: `cd frontend && npm run i18n:android:check`
Expected: PASS. `android/app/src/main/assets/i18n/sources.sha256` changed.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_start_failure.py tests/test_ui_messages.py -v`
Expected: PASS.

Run: `cd frontend && node --test src/i18n/index.test.js`
Expected: PASS.

- [ ] **Step 8: Commit**

Also stage any other file that Step 6 changed.

```bash
git add src/twicc/agent/exceptions.py src/twicc/agent/states.py src/twicc/agent/start_failure.py src/twicc/providers/claude_code/bin.py tests/test_agent_start_failure.py frontend/src/i18n/locales/en.json frontend/src/i18n/locales/de.json frontend/src/i18n/locales/es.json frontend/src/i18n/locales/fr.json frontend/src/i18n/locales/pt-BR.json android/app/src/main/assets/i18n/sources.sha256
git commit -m "feat(agent): classify agent start failures"
```

---

### Task 2: Base manager and Codex launches

**Files:**
- Modify: `src/twicc/agent/base_agent.py:111`, `:356-371`
- Modify: `src/twicc/agent/base_manager.py:477-502` and `:504-643`
- Modify: `src/twicc/providers/codex/agent/agent.py:849`, `:1509`, `:1622-1638`, `:3200-3214`
- Modify: `src/twicc/core/services/timeline_filters.py:78-81`
- Modify: `frontend/src/utils/timeline/describeEvent.js:40`
- Modify: `frontend/src/utils/timeline/describeEvent.test.js:71-72`, `:82`
- Modify: `tests/test_timeline_filters.py:36-40`, `:133-136`
- Test: `tests/test_agent_start_failure.py`, `tests/test_codex_async_question_send.py`, `frontend/src/composables/wsAgentDispatch.test.js`

**Interfaces:**
- Consumes: `launch_directory_failure`, `classify_start_failure`, `log_start_failure`, `AgentStartError`, `AGENT_START_FAILED_KILL_REASON` (Task 1).
- Produces:
  - `BaseAgent.start_failure: SendDeliveryError | None`, default `None`. An agent sets it during `start()` when it records a launch failure. `_start_agent_with_admission` raises it after `_register_and_start` returns.
  - `BaseAgent.handoff_attempted: bool`, default `False`. Each provider sets it to `True` at its handoff.
  - `BaseAgent.report_start_failure: bool`, default `False`. The base manager sets it before the agent starts.
  - `BaseAgent._completion_input_settled(token: str, status: str, *, failure: SendDeliveryError | None = None)`. With a `failure`, an open receipt ends with that exception, so `wait_for_completion_input` raises it instead of `send_uncertain`.
  - `BaseAgentManager._start_agent(..., report_start_failure: bool = True, **start_kwargs)`. `False` means that no caller waits for the launch.
  - `CodexAgent._handle_error(error_message, exc=None, *, error_ui_message=None, kill_reason: str = "error")`

- [ ] **Step 1: Write the failing base manager tests**

```python
from twicc.agent.base_manager import BaseAgentManager
from twicc.providers.helpers import AgentSettings


class Manager(BaseAgentManager):
    provider = Provider.CODEX

    def __init__(self, create):
        super().__init__()
        self._create_agent = create
        self._check_session_writable = AsyncMock()


def start(manager, directory, **kwargs):
    return asyncio.run(manager._start_agent("s", "p", directory, "hi", resume=True, settings=AgentSettings(), **kwargs))


def test_a_missing_directory_never_creates_an_agent_and_logs_one_entry(tmp_path):
    create = AsyncMock()
    gone = str(tmp_path / "gone")
    with patch("twicc.agent.base_manager.logger") as logger, pytest.raises(AgentStartError) as caught:
        start(Manager(create), gone)
    assert caught.value.reason == "working_directory_missing"
    create.assert_not_awaited()
    logger.error.assert_called_once()
    assert "s" in logger.error.call_args.args and gone in logger.error.call_args.args


def test_the_availability_check_runs_before_the_directory_check(tmp_path):
    manager = Manager(AsyncMock())
    manager._check_session_writable = AsyncMock(side_effect=SendDeliveryError("no", code="session_unavailable"))
    with pytest.raises(SendDeliveryError) as caught:
        start(manager, str(tmp_path / "gone"))
    assert caught.value.code == "session_unavailable"


def test_a_create_failure_is_classified_and_logged_once(tmp_path):
    error = FileNotFoundError(2, "No such file", "/some/file")
    with patch("twicc.agent.start_failure.logger") as logger, pytest.raises(AgentStartError) as caught:
        start(Manager(AsyncMock(side_effect=error)), str(tmp_path))
    assert caught.value.reason == "start_failed"
    assert caught.value.__cause__ is error
    logger.error.assert_called_once()
    assert logger.error.call_args.kwargs["exc_info"] is error


def test_an_existing_send_error_from_create_keeps_its_code(tmp_path):
    error = SendDeliveryError("bad", code="attachment_missing")
    with pytest.raises(SendDeliveryError) as caught:
        start(Manager(AsyncMock(side_effect=error)), str(tmp_path))
    assert caught.value is error


def test_a_recorded_failure_is_raised_after_the_agent_started(tmp_path):
    failure = AgentStartError("x", reason="start_failed", diagnostic="x")
    agent = SimpleNamespace(session_id="s", start_failure=failure, ephemeral=False, interrupt_or_kill=AsyncMock())
    manager = Manager(AsyncMock(return_value=agent))
    manager._register_and_start = AsyncMock()
    with pytest.raises(AgentStartError) as caught:
        start(manager, str(tmp_path))
    assert caught.value is failure
    agent.interrupt_or_kill.assert_not_awaited()


def started_agent(**kwargs):
    return SimpleNamespace(
        session_id="s", start_failure=None, handoff_attempted=False, ephemeral=False,
        interrupt_or_kill=AsyncMock(), _completion_input_settled=MagicMock(), **kwargs,
    )


def test_a_failure_after_create_and_before_handoff_is_classified(tmp_path):
    agent = started_agent()
    manager = Manager(AsyncMock(return_value=agent))
    manager._register_and_start = AsyncMock(side_effect=RuntimeError("db write failed"))
    with pytest.raises(AgentStartError) as caught:
        start(manager, str(tmp_path), completion_token="t")
    assert caught.value.reason == "start_failed"
    agent.interrupt_or_kill.assert_awaited_once_with(reason="agent-start-failed")
    agent._completion_input_settled.assert_called_once_with("t", "rejected")


def test_a_failure_after_handoff_keeps_its_exception(tmp_path):
    agent = started_agent()
    agent.handoff_attempted = True
    error = RuntimeError("late")
    manager = Manager(AsyncMock(return_value=agent))
    manager._register_and_start = AsyncMock(side_effect=error)
    with pytest.raises(RuntimeError) as caught:
        start(manager, str(tmp_path))
    assert caught.value is error
    agent.interrupt_or_kill.assert_awaited_once_with(reason="startup-failed")


def test_a_send_error_during_start_keeps_its_code(tmp_path):
    error = SendDeliveryError("bad", code="attachment_missing")
    agent = started_agent()
    manager = Manager(AsyncMock(return_value=agent))
    manager._register_and_start = AsyncMock(side_effect=error)
    with pytest.raises(SendDeliveryError) as caught:
        start(manager, str(tmp_path))
    assert caught.value is error
    agent.interrupt_or_kill.assert_awaited_once_with(reason="startup-failed")


def test_without_a_waiting_caller_failures_stay_raw(tmp_path):
    error = RuntimeError("raw")
    with pytest.raises(RuntimeError) as caught:
        start(Manager(AsyncMock(side_effect=error)), str(tmp_path), report_start_failure=False)
    assert caught.value is error


def test_the_directory_check_applies_without_a_waiting_caller(tmp_path):
    with pytest.raises(AgentStartError) as caught:
        start(Manager(AsyncMock()), str(tmp_path / "gone"), report_start_failure=False)
    assert caught.value.reason == "working_directory_missing"


def test_the_completion_wait_raises_the_recorded_start_failure():
    from twicc.agent.base_agent import BaseAgent

    class Agent(BaseAgent):
        provider = Provider.CODEX

        def current_status_label(self):
            return None

    async def run():
        manager = BaseAgentManager()
        agent = Agent("s", "p", "/tmp", AgentSettings())
        manager._bind_completion_callbacks(agent)
        agent._completion_input_pending("t")
        failure = AgentStartError("x", reason="start_failed", diagnostic="x")
        agent._completion_input_settled("t", "rejected", failure=failure)
        with pytest.raises(AgentStartError) as caught:
            await manager.wait_for_completion_input("t")
        assert caught.value is failure

    asyncio.run(run())


def test_an_ephemeral_directory_failure_stays_generic(tmp_path):
    with pytest.raises(AgentStartError) as caught:
        start(Manager(AsyncMock()), str(tmp_path / "gone"), ephemeral=True)
    assert caught.value.ui_message == ui_message("backend.agent.ephemeralStartFailed")


def test_an_ephemeral_create_failure_logs_only_the_exception_type(tmp_path):
    with patch("twicc.agent.start_failure.logger") as logger, pytest.raises(AgentStartError):
        start(Manager(AsyncMock(side_effect=RuntimeError("secret prompt"))), str(tmp_path), ephemeral=True)
    logger.error.assert_called_once_with("Ephemeral agent failed to start (%s)", "RuntimeError")
```

- [ ] **Step 2: Write the failing Codex tests in `tests/test_codex_async_question_send.py`**

Follow `test_definite_rejection_keeps_ready`, line 171.

```python
def test_codex_start_failure_is_rejected_and_answers_stay_ready(harness, monkeypatch):
    harness.manager._agents.clear()
    monkeypatch.setattr(harness.manager, "_create_agent", AsyncMock(side_effect=OSError("spawn failed")))
    with pytest.raises(SendDeliveryError) as caught:
        asyncio.run(harness.send("next", response()))
    assert caught.value.code == "agent_start_failed"
    assert caught.value.reason == "start_failed"
    assert snapshot(harness)["batches"][0]["status"] == "ready"
    assert (
        AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]["status"]
        == "rejected"
    )


def test_codex_command_launch_failure_is_reported(harness, monkeypatch):
    harness.manager._agents.clear()
    monkeypatch.setattr(harness.manager, "_create_agent", AsyncMock(side_effect=OSError("spawn failed")))
    with pytest.raises(SendDeliveryError) as caught:
        asyncio.run(harness.send("/compact"))
    assert caught.value.code == "agent_start_failed"


def cold_send(harness, monkeypatch, prepare):
    """Run one cold send through the real agent start; ``prepare(agent, thread)`` installs the failure."""
    from twicc.providers import db_writer

    async def run():
        db_writer.start_db_writer()
        agent, _sdk, thread, _turn = runtime_agent(harness)
        harness.manager._agents.clear()
        agent._work_dirs = []
        agent._reconcile_question_submissions = AsyncMock()
        agent._reconcile_async_question_owners = AsyncMock()
        del agent._handle_error  # runtime_agent mocks it; these tests read the kill_reason that it assigns
        agent.report_start_failure = True
        agent._codex = SimpleNamespace(close=AsyncMock())
        agent._transition_to_dead = AsyncMock()
        prepare(agent, thread)

        async def start(session_id, project_id, cwd, text, *, resume, settings, images, submission):
            harness.manager._agents[session_id] = agent
            await agent.start(text, AsyncMock(), resume=resume, images=images, submission=submission)
            return session_id

        monkeypatch.setattr(harness.manager, "_start_agent", start)
        try:
            with pytest.raises(SendDeliveryError) as caught:
                await harness.manager.send_to_session(
                    harness.session.id, harness.session.project_id, "/tmp", "next", AgentSettings(),
                    request_id="cold-1", send_origin="human", async_questions=response(),
                )
            return agent, thread, caught.value
        finally:
            if agent._turn_task:
                agent._turn_task.cancel()
                await asyncio.gather(agent._turn_task, return_exceptions=True)
            await db_writer.stop_db_writer()

    return asyncio.run(run())


def test_codex_failure_before_the_turn_opens_is_a_start_failure(harness, monkeypatch):
    def prepare(agent, _thread):
        agent._build_turn_input = AsyncMock(side_effect=RuntimeError("bad input"))

    agent, thread, error = cold_send(harness, monkeypatch, prepare)
    assert (error.code, error.reason) == ("agent_start_failed", "start_failed")
    assert agent.kill_reason == "agent-start-failed"
    thread.turn_with_policy.assert_not_awaited()


def test_codex_failure_while_the_turn_opens_stays_uncertain(harness, monkeypatch):
    def prepare(_agent, thread):
        thread.turn_with_policy = AsyncMock(side_effect=OSError("connection lost"))

    agent, _thread, error = cold_send(harness, monkeypatch, prepare)
    assert error.code == "send_uncertain"
    assert agent.kill_reason == "error"


def test_codex_new_session_failure_before_the_turn_reaches_the_wait(harness):
    from twicc.providers import db_writer

    async def run():
        db_writer.start_db_writer()
        agent, _sdk, thread, _turn = runtime_agent(harness)
        agent._work_dirs = []
        del agent._handle_error
        agent.report_start_failure = True
        agent._codex = SimpleNamespace(close=AsyncMock())
        agent._transition_to_dead = AsyncMock()
        agent._build_turn_input = AsyncMock(side_effect=RuntimeError("bad input"))
        harness.manager._bind_completion_callbacks(agent)
        try:
            await agent.start("hi", AsyncMock(), resume=False, completion_token="t")
            await agent._turn_task
            with pytest.raises(SendDeliveryError) as caught:
                await harness.manager.wait_for_completion_input("t")
            assert (caught.value.code, caught.value.reason) == ("agent_start_failed", "start_failed")
            thread.turn_with_policy.assert_not_awaited()
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(run())
```

`tests/test_codex_async_question_send.py` needs `SimpleNamespace`, `AsyncMock`, `SendDeliveryError` and `AgentSettings`. Add any that its imports lack.

- [ ] **Step 3: Write the failing frontend tests**

In `describeEvent.test.js`, add to `expected`: `'agent-start-failed': ['Process failed to start', 'danger', []]`. At lines 71-72, change the reason count from 15 to 16, in the title and in the assertion.

In `wsAgentDispatch.test.js`:

```js
test('a reported start failure raises neither a toast nor agent_died', () => {
    const code = block("if (msg.state === 'dead' && !ephemeral)", 'export function showUpdateToast')
    assert.match(code, /kill_reason === 'error'/)
    assert.match(code, /\['error', 'startup-failed', 'timeout_starting'\]/)
    assert.doesNotMatch(code, /agent-start-failed/)
})
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_start_failure.py -v -k "directory_never or create_failure or recorded or after_create or directory_check_applies or completion_wait or ephemeral_directory or ephemeral_create"`
Expected: FAIL. Three guards already pass before the change: `test_the_availability_check_runs_before_the_directory_check`, `test_an_existing_send_error_from_create_keeps_its_code` and `test_without_a_waiting_caller_failures_stay_raw`. They are not in this selection.

Run: `uv run pytest tests/test_codex_async_question_send.py -v -k "start_failure or launch_failure or before_the_turn or while_the_turn"`
Expected: FAIL.

Run: `cd frontend && node --test src/utils/timeline/describeEvent.test.js`
Expected: FAIL on `agent-start-failed`.

- [ ] **Step 5: Implement the base manager**

- `BaseAgent.__init__`: `self.start_failure: SendDeliveryError | None = None`, `self.handoff_attempted = False`, `self.report_start_failure = False`.
- `BaseAgent._completion_input_settled`: add the keyword `failure`. When `status` is `rejected` or `uncertain`, the receipt is open and `failure` is set, call `receipt.set_exception(failure)`, then `receipt.exception()` so that an unread receipt logs no warning. Without `failure`, keep `receipt.set_result(False)`.
- `_start_agent` and `_start_agent_with_admission`: add the keyword `report_start_failure: bool = True`.
- `_start_agent`: as the first statement inside the existing `try`, call `await self._check_launch_directory(session_id, cwd, ephemeral=ephemeral)`. The check must be inside the `try`, so that the `finally` settles an ephemeral admission.
- New `async def _check_launch_directory(self, session_id: str, directory: str, *, ephemeral: bool) -> None`. It runs `launch_directory_failure` through `asyncio.to_thread`. On failure it logs one entry, `logger.error("Agent launch refused for session %s: launch directory %s is unavailable", session_id, directory)`, and raises.
- New `def _classified_start_failure(self, exc: Exception, *, session_id: str, directory: str, ephemeral: bool, report: bool) -> SendDeliveryError | None`. It returns `None` when `report` is false or when `exc` is a `SendDeliveryError`. Otherwise it calls `log_start_failure` and returns `classify_start_failure(...)`.
- `_start_agent_with_admission`, around `self._create_agent(...)`: `except Exception as exc:` call the helper. If it returns a failure, `raise failure from exc`. Else `raise`.
- First statement inside the post-creation `try`: `agent.report_start_failure = report_start_failure`.
- In the cleanup `except BaseException` (line 629), bind the exception. Compute `failure = None`. If it is an `Exception` and `getattr(agent, "handoff_attempted", False)` is false, call the helper. Use `AGENT_START_FAILED_KILL_REASON` as the kill reason when `failure` is an `AgentStartError`, else `"startup-failed"`. If `failure` is not `None` and `start_kwargs.get("completion_token")` is set, call `agent._completion_input_settled(token, "rejected")` before the kill. End the block with `raise failure from exc` when `failure` is set, else `raise`.
- Replace `return agent.session_id` inside the `try` by leaving the `try`, then `failure = getattr(agent, "start_failure", None)`, `if failure is not None: raise failure`, then `return agent.session_id`. The raise is outside the `try`, so a recorded failure skips the cleanup: the agent is already `DEAD`. Use `getattr` because existing test doubles are `SimpleNamespace` objects without this attribute.

Codex agent:

- `start`: immediately before `await self.run_hardcoded_command(command)`, set `self.handoff_attempted = True`. A failure of a command keeps its current classification.
- `_open_turn`: immediately before `await self._thread.turn_with_policy(...)`, set `self.handoff_attempted = True`.
- `_run_turn`, first statement of `except Exception as e` (line 1622): `start_failure = classify_start_failure(e, directory=self.cwd, provider=self.provider, ephemeral=getattr(self, "ephemeral", False)) if self.report_start_failure and not self.handoff_attempted else None`. The `report_start_failure` test limits this branch to a launch. If the result is an `AgentStartError`, set `start_failure.__cause__ = e`, keep the original as `cause = e`, and rebind `e = start_failure`. `question_send_failure_status` then returns `rejected`, and the existing code passes the failure to the delivery future.
- `_run_turn`, the `_completion_input_settled` call of this branch: pass `failure=start_failure` when it is an `AgentStartError`. A new session has no delivery future, so the completion wait of the creation service is the only way that the failure reaches the caller.
- `_run_turn`, the `_handle_error` call of this branch: when `start_failure` is an `AgentStartError`, pass `exc=cause`, `error_ui_message=start_failure.ui_message` and `kill_reason=AGENT_START_FAILED_KILL_REASON`. `_handle_error` logs the traceback, so do not call `log_start_failure` here.
- `CodexAgent._handle_error`: add the keyword `kill_reason: str = "error"`, and assign it.

- [ ] **Step 6: Add the reason to the timeline**

- `PROBLEM_REASONS`: add `"agent-start-failed"`.
- `describeEvent.js`: add `get 'agent-start-failed'() { return [i18n.global.t('session.timeline.startFailed'), 'danger'] },` after the `'startup-failed'` line.
- `tests/test_timeline_filters.py`: add `"agent-start-failed"` to `FRONTEND_REASONS` (lines 36-40) and to the exact set of lines 133-136.

- [ ] **Step 7: Run the tests to verify they pass, and fix the fake directories**

Run: `uv run pytest tests/test_agent_start_failure.py tests/test_codex_async_question_send.py tests/test_timeline_filters.py tests/test_timeline_filters_sync.py tests/test_ephemeral_lifecycle.py tests/test_ephemeral_providers.py tests/test_mute_on_user_turn_creation.py tests/test_timeline_origin_titles_creation.py tests/test_timeline_origin_process.py tests/test_timeline_process_hooks.py tests/test_claude_attachment_delivery.py tests/test_codex_attachment_delivery.py -v`

Expected: the new tests PASS. A test that reaches the real `_start_agent` with a directory that does not exist, such as `/project`, now fails with `working_directory_missing`. For each such failure, replace the fake directory by `/tmp` or `str(tmp_path)`. Do not change any assertion on behavior.

Run: `cd frontend && node --test src/utils/timeline/describeEvent.test.js src/composables/wsAgentDispatch.test.js`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/twicc/agent/base_agent.py src/twicc/agent/base_manager.py src/twicc/providers/codex/agent/agent.py src/twicc/core/services/timeline_filters.py frontend/src/utils/timeline/describeEvent.js frontend/src/utils/timeline/describeEvent.test.js frontend/src/composables/wsAgentDispatch.test.js tests/test_agent_start_failure.py tests/test_codex_async_question_send.py tests/test_timeline_filters.py
git commit -m "feat(agent): report launch failures from the base manager"
```

Add any test file that Step 7 changed.

---

### Task 3: Claude SDK launches

**Files:**
- Modify: `src/twicc/providers/claude_code/agent/agent.py:1051-1436` and `:2824-2858`
- Modify: `src/twicc/providers/claude_code/agent/manager.py:679-684`, `:747-753`, `:824`, `:1754-1765`, `:1767-1800`, `:1948-1952`
- Test: `tests/test_agent_start_failure.py`, `tests/test_session_availability.py`

**Interfaces:**
- Consumes: `classify_start_failure`, `AgentStartError`, `AGENT_START_FAILED_KILL_REASON` (Task 1). `BaseAgent.start_failure`, `handoff_attempted`, `report_start_failure`, and the `report_start_failure` keyword of `_start_agent` (Task 2).
- Produces:
  - `ClaudeCodeAgent._handle_error(error_message, exc=None, *, presentation=None, kill_reason: str = "error")`
  - `ClaudeCodeAgentManager._broadcast_agent_state(session_id, project_id=None, override_state=None, *, error: str | None = None, error_ui_message: dict | None = None, kill_reason: str | None = None)`
  - `ClaudeCodeAgentManager._notify_automatic_start_failure(session_id: str, project_id: str, failure: SendDeliveryError) -> None`

- [ ] **Step 1: Write the failing agent tests in `tests/test_agent_start_failure.py`**

Write `make_started_claude_agent(monkeypatch, directory, connect=None, query=None, report=True)` from the setup of `tests/test_claude_attachment_delivery.py:223-235`, with `ephemeral=False`. Its fake client uses `connect or AsyncMock()` and `query or AsyncMock()`. It sets `agent.report_start_failure = report`, `agent._transition_to_dead = AsyncMock()` and `agent._shutdown_sdk_client = AsyncMock()`, and returns `agent`.

Each test needs the database: `ClaudeCodeAgent.start` reads it in a local closure. Without the mark, the read fails inside the `try` and the tests pass for the wrong reason.

```python
def run_start(agent):
    asyncio.run(agent.start("hi", AsyncMock(), resume=True, completion_token="token"))


@pytest.mark.django_db(transaction=True)
def test_connect_failure_is_recorded_when_a_caller_waits(monkeypatch, tmp_path):
    connect = AsyncMock(side_effect=CLIConnectionError("no"))
    agent = make_started_claude_agent(monkeypatch, str(tmp_path), connect=connect)
    run_start(agent)
    connect.assert_awaited_once()
    assert agent.start_failure.code == "agent_start_failed"
    assert agent.start_failure.reason == "start_failed"
    assert agent.kill_reason == "agent-start-failed"
    assert agent._completion_inputs["token"].status == "rejected"


@pytest.mark.django_db(transaction=True)
def test_connect_failure_keeps_the_process_error_without_a_waiting_caller(monkeypatch, tmp_path):
    connect = AsyncMock(side_effect=CLIConnectionError("no"))
    agent = make_started_claude_agent(monkeypatch, str(tmp_path), connect=connect, report=False)
    run_start(agent)
    connect.assert_awaited_once()
    assert agent.start_failure is None
    assert agent.kill_reason == "error"
    assert agent._completion_inputs["token"].status == "uncertain"


@pytest.mark.django_db(transaction=True)
def test_failure_between_connect_and_query_is_a_start_failure(monkeypatch, tmp_path):
    agent = make_started_claude_agent(monkeypatch, str(tmp_path))
    agent._build_query_prompt = AsyncMock(side_effect=RuntimeError("prompt build failed"))
    run_start(agent)
    agent._build_query_prompt.assert_awaited_once()
    assert agent.start_failure.reason == "start_failed"


@pytest.mark.django_db(transaction=True)
def test_query_failure_stays_uncertain(monkeypatch, tmp_path):
    query = AsyncMock(side_effect=CLIConnectionError("no"))
    agent = make_started_claude_agent(monkeypatch, str(tmp_path), query=query)
    run_start(agent)
    query.assert_awaited_once()
    assert agent.start_failure is None
    assert agent.handoff_attempted is True
    assert agent.kill_reason == "error"
    assert agent._completion_inputs["token"].status == "uncertain"


@pytest.mark.django_db(transaction=True)
def test_missing_directory_at_connect_gets_its_reason(monkeypatch, tmp_path):
    gone = str(tmp_path / "gone")
    connect = AsyncMock(side_effect=CLIConnectionError("no"))
    agent = make_started_claude_agent(monkeypatch, gone, connect=connect)
    run_start(agent)
    connect.assert_awaited_once()
    assert agent.start_failure.reason == "working_directory_missing"
```

- [ ] **Step 2: Write the failing manager tests in `tests/test_session_availability.py`**

```python
def _start_failure():
    from twicc.agent.exceptions import AgentStartError
    return AgentStartError("Claude Code did not start", reason="working_directory_missing")


def test_inline_restart_reports_and_clears_starting(sessions):
    from twicc.agent.exceptions import AgentStartError
    from twicc.agent.states import AgentState
    from twicc.providers.helpers import AgentSettings

    manager, _agent = _idle_claude_manager("open", AgentSettings())
    manager._start_agent = AsyncMock(side_effect=_start_failure())
    with pytest.raises(AgentStartError):
        asyncio.run(manager._restart_after_kill(
            "open", PROJECT_ID, "/tmp", "hi", settings=AgentSettings(), report_start_failure=True,
        ))
    manager._broadcast_agent_state.assert_awaited_once_with("open", PROJECT_ID, AgentState.DEAD)


def test_automatic_parked_restart_notifies_and_does_not_raise(sessions):
    from twicc.agent.states import AgentState
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    helpers = get_provider_helpers("claude_code")
    requested = helpers.enforce_agent_settings_consistency(
        helpers.resolve_agent_settings(AgentSettings.from_session(sessions.open)),
    )
    manager, agent = _idle_claude_manager("open", _other_effort(requested))
    manager._pending_after_restart["open"] = {"text": "parked", "images": None, "documents": None}
    failure = _start_failure()
    manager._start_agent = AsyncMock(side_effect=failure)

    asyncio.run(manager._apply_pending_settings(agent))

    manager._broadcast_agent_state.assert_any_await(
        "open", PROJECT_ID, AgentState.DEAD, error=str(failure), error_ui_message=failure.ui_message, kill_reason="error",
    )


def test_cron_send_notifies_then_raises(sessions):
    from twicc.agent.exceptions import AgentStartError
    from twicc.agent.states import AgentState
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    from twicc.providers.helpers import AgentSettings

    manager = ClaudeCodeAgentManager()
    manager._broadcast_agent_state = AsyncMock()
    failure = _start_failure()
    manager._start_agent = AsyncMock(side_effect=failure)
    with pytest.raises(AgentStartError):
        asyncio.run(manager.send_to_session(
            "open", PROJECT_ID, "/tmp", "hi", AgentSettings(), cancel_cron_restart=False,
        ))
    manager._broadcast_agent_state.assert_awaited_once_with(
        "open", PROJECT_ID, AgentState.DEAD, error=str(failure), error_ui_message=failure.ui_message, kill_reason="error",
    )


@pytest.mark.parametrize("cancel, expected", [(True, True), (False, False)])
def test_cold_send_report_flag_follows_the_cron_marker(sessions, cancel, expected):
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    from twicc.providers.helpers import AgentSettings

    manager = ClaudeCodeAgentManager()
    manager._start_agent = AsyncMock(return_value="open")
    asyncio.run(manager.send_to_session("open", PROJECT_ID, "/tmp", "hi", AgentSettings(), cancel_cron_restart=cancel))
    assert manager._start_agent.await_args.kwargs["report_start_failure"] is expected


@pytest.mark.parametrize("cancel, expected", [(True, True), (False, False)])
def test_inline_restart_forwards_the_report_flag(sessions, cancel, expected):
    from twicc.providers.helpers import AgentSettings

    requested = AgentSettings(effort="high")
    manager, _agent = _idle_claude_manager("open", _other_effort(requested))
    manager._start_agent = AsyncMock(return_value="open")
    asyncio.run(manager.send_to_session("open", PROJECT_ID, "/tmp", "hi", requested, cancel_cron_restart=cancel))
    assert manager._start_agent.await_args.kwargs["report_start_failure"] is expected


def test_restart_without_a_waiting_caller_notifies_then_raises(sessions):
    from twicc.agent.exceptions import AgentStartError
    from twicc.agent.states import AgentState
    from twicc.providers.helpers import AgentSettings

    manager, _agent = _idle_claude_manager("open", AgentSettings())
    failure = _start_failure()
    manager._start_agent = AsyncMock(side_effect=failure)
    with pytest.raises(AgentStartError):
        asyncio.run(manager._restart_after_kill(
            "open", PROJECT_ID, "/tmp", "hi", settings=AgentSettings(), report_start_failure=False,
        ))
    manager._broadcast_agent_state.assert_awaited_once_with(
        "open", PROJECT_ID, AgentState.DEAD, error=str(failure), error_ui_message=failure.ui_message, kill_reason="error",
    )


@pytest.mark.parametrize("ephemeral, expected", [(False, True), (True, False)])
def test_create_report_flag_follows_ephemeral(sessions, ephemeral, expected):
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    from twicc.providers.helpers import AgentSettings

    manager = ClaudeCodeAgentManager()
    manager._start_agent = AsyncMock(return_value="draft")
    asyncio.run(manager.create_session("draft", PROJECT_ID, "/tmp", "hi", AgentSettings(), ephemeral=ephemeral))
    assert manager._start_agent.await_args.kwargs["report_start_failure"] is expected
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_start_failure.py tests/test_session_availability.py -v -k "connect or query or inline_restart or parked_restart or cron_send or report_flag or without_a_waiting_caller_notifies"`
Expected: FAIL, except `test_connect_failure_keeps_the_process_error_without_a_waiting_caller`, which is a guard and passes before the change.

- [ ] **Step 4: Implement `ClaudeCodeAgent`**

- Immediately before `await self._client.query(query_prompt)`, set `self.handoff_attempted = True`.
- In `except Exception as e` (line 1428), as the first branch: if `self.report_start_failure and not self.handoff_attempted`, call `await self._record_start_failure(e, completion_token)` and return. The existing branch is unchanged otherwise.
- New `async def _record_start_failure(self, exc: Exception, completion_token: str) -> None`:
  1. `failure = classify_start_failure(exc, directory=self.cwd, provider=self.provider)`
  2. `self._completion_input_settled(completion_token, "rejected")`
  3. `self.start_failure = failure`
  4. `await self._handle_error(str(failure), exc=exc, presentation=failure.ui_message, kill_reason=AGENT_START_FAILED_KILL_REASON)`
- `_handle_error` logs the traceback. Do not call `log_start_failure` on this path.
- `_handle_error`: assign `self.kill_reason = kill_reason`.

- [ ] **Step 5: Implement the manager**

- Do not change the refusal at line 381 ("Prior Claude process final drain is incomplete"). It keeps its type, and the base manager reports it as `start_failed` with its own message nested. Accepted limitation: this rare refusal depends on the state of the session, but the report stays true, because the agent did not start and a new send is safe.
- `send_to_session`, cold start at line 747: pass `report_start_failure=cancel_cron_restart`. Wrap the call: `except AgentStartError as failure:` if `not cancel_cron_restart`, await `_notify_automatic_start_failure`; then `raise`.
- Inline restart at line 679: pass `report_start_failure=cancel_cron_restart`.
- `create_session` at line 824: pass `report_start_failure=not ephemeral`.
- `_restart_after_kill`: add `except AgentStartError as failure:`. If `start.get("report_start_failure", True)`, await `self._broadcast_agent_state(session_id, project_id, AgentState.DEAD)`. Else await `_notify_automatic_start_failure(session_id, project_id, failure)`. Then `raise`.
- `_broadcast_agent_state`: add the three keywords and pass them to the synthetic `AgentInfo`, whose fields `error`, `error_ui_message` and `kill_reason` already exist.
- `_notify_automatic_start_failure`: broadcast `DEAD` with `error=str(failure)`, `error_ui_message=failure.ui_message`, `kill_reason="error"`.
- Automatic restart at line 1948: pass `report_start_failure=False`. Wrap the `_restart_after_kill` call in `except AgentStartError: return`. `_restart_after_kill` has already notified. The parked refs go to the reaper, as the `SessionUnavailableError` branch at line 1932 does.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_start_failure.py tests/test_session_availability.py tests/test_claude_ui_messages.py tests/test_claude_completion.py tests/test_claude_attachment_delivery.py tests/test_attachment_only_messages.py tests/test_composer_attachment_send.py tests/test_timeline_origin_markers.py -v`

Expected: PASS. A fake `_start_agent` with a fixed signature that now fails on `report_start_failure` needs `**kwargs`. A test that asserts the exact kwargs of `_start_agent` needs the new kwarg in its expected value.

- [ ] **Step 7: Commit**

```bash
git add src/twicc/providers/claude_code/agent/agent.py src/twicc/providers/claude_code/agent/manager.py tests/test_agent_start_failure.py tests/test_session_availability.py
git commit -m "feat(claude): report sdk launch failures to the waiting caller"
```

Add any test file that Step 6 changed.

---

### Task 4: Hybrid launches

**Files:**
- Modify: `src/twicc/providers/claude_code/agent/hybrid/agent.py:328`
- Test: `tests/test_agent_start_failure.py`

**Interfaces:**
- Consumes: `BaseAgent.handoff_attempted` and the classification of the base manager (Task 2).
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

Write `make_hybrid_agent(monkeypatch, directory, create=None, argv=None)` from `tests/test_claude_ui_messages.py:379` and the patches of `tests/test_hybrid_launch_chrome_onboarding.py`. It patches `hybrid_tmux.session_exists` to `False`, `hybrid_tmux.pane_status` to `MagicMock(return_value=(1, False))`, `hybrid_tmux.create_session` to `create or MagicMock()`, `build_argv` to `argv or MagicMock(return_value=["claude"])`, and the addendum writer and the work directory calls. It sets `agent._first_paste = AsyncMock()`, because `start` passes its result to `create_task`, and `agent._start_liveness_monitor = MagicMock()`.

```python
@pytest.mark.django_db(transaction=True)
def test_the_launch_ends_when_the_tmux_session_exists(monkeypatch, tmp_path):
    create = MagicMock()
    agent = make_hybrid_agent(monkeypatch, str(tmp_path), create=create)
    assert agent.handoff_attempted is False
    asyncio.run(agent.start("hi", AsyncMock(), resume=True, completion_token="token"))
    create.assert_called_once()
    assert agent.handoff_attempted is True


@pytest.mark.django_db(transaction=True)
def test_a_tmux_failure_leaves_the_launch_before_handoff(monkeypatch, tmp_path):
    create = MagicMock(side_effect=RuntimeError("tmux down"))
    agent = make_hybrid_agent(monkeypatch, str(tmp_path), create=create)
    with pytest.raises(RuntimeError, match="tmux down"):
        asyncio.run(agent.start("hi", AsyncMock(), resume=True, completion_token="token"))
    create.assert_called_once()
    assert agent.handoff_attempted is False


@pytest.mark.django_db(transaction=True)
def test_a_hybrid_tmux_failure_is_classified_by_the_manager(monkeypatch, tmp_path):
    agent = make_hybrid_agent(monkeypatch, str(tmp_path), create=MagicMock(side_effect=RuntimeError("tmux down")))
    agent.interrupt_or_kill = AsyncMock()
    manager = Manager(AsyncMock(return_value=agent))

    async def register(agent, text, resume, **kwargs):
        await agent.start(text, AsyncMock(), resume=resume, **kwargs)

    manager._register_and_start = register
    with pytest.raises(AgentStartError) as caught:
        start(manager, str(tmp_path), completion_token="token")
    assert caught.value.reason == "start_failed"
    agent.interrupt_or_kill.assert_awaited_once_with(reason="agent-start-failed")
    assert agent._completion_inputs["token"].status == "rejected"


@pytest.mark.django_db(transaction=True)
def test_an_absent_hybrid_binary_gets_its_reason(monkeypatch, tmp_path):
    agent = make_hybrid_agent(monkeypatch, str(tmp_path), argv=MagicMock(side_effect=ClaudeBinaryNotFound("absent")))
    agent.interrupt_or_kill = AsyncMock()
    manager = Manager(AsyncMock(return_value=agent))

    async def register(agent, text, resume, **kwargs):
        await agent.start(text, AsyncMock(), resume=resume, **kwargs)

    manager._register_and_start = register
    with pytest.raises(AgentStartError) as caught:
        start(manager, str(tmp_path))
    assert caught.value.reason == "cli_not_found"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_start_failure.py -v -k "tmux_session_exists"`
Expected: FAIL: `handoff_attempted` stays `False`.

- [ ] **Step 3: Implement**

Immediately after `self.agent_pid, _ = await asyncio.to_thread(_create)`, set `self.handoff_attempted = True`. A hybrid launch ends there. Do not change the adoption branch, the title callback, the first paste or the liveness monitor.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_start_failure.py tests/test_hybrid_attachment_delivery.py tests/test_hybrid_completion.py tests/test_hybrid_launch_chrome_onboarding.py tests/test_hybrid_title_confirmation.py tests/test_claude_ui_messages.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/providers/claude_code/agent/hybrid/agent.py tests/test_agent_start_failure.py
git commit -m "feat(hybrid): report launch failures before the tmux session exists"
```

---

### Task 5: Caller results and agent-facing documentation

**Files:**
- Modify: `src/twicc/asgi.py:1476`, `:1484-1492`
- Modify: `src/twicc/core/services/send_message.py:39-43`, `:58-59`, `:288-292`
- Modify: `src/twicc/core/services/session_creation.py:50-53`, `:524-537`
- Modify: `src/twicc/drop_requests_watcher.py:289`
- Modify: `frontend/src/stores/data.js:4238`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-send-message/SKILL.md:93-103`, `src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md:76-80`, `src/twicc/agent/plugin/twicc/skills/twicc-send-messages/SKILL.md:84`, `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`, `SKILLS-AND-CLI.md`
- Test: `tests/test_agent_start_failure.py`, `tests/test_session_availability.py`

**Interfaces:**
- Consumes: `AgentStartError` (Task 1).
- Produces:
  - Optional `reason: str | None = None` as the last field of `SendMessageError` and `SessionCreationError`.
  - `twicc.drop_requests_watcher._error_payload(error: NamedTuple) -> dict`, which omits `reason` when it is `None`.
  - A WS error frame carries `reason` only for an `AgentStartError`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_agent_start_failure.py`:

```python
def test_status_file_has_reason_only_when_set():
    from twicc.core.services.send_message import SendMessageError
    from twicc.drop_requests_watcher import _error_payload

    assert _error_payload(SendMessageError("session", "agent_start_failed", "m", "start_failed")) == {
        "field": "session", "code": "agent_start_failed", "message": "m", "reason": "start_failed",
    }
    assert _error_payload(SendMessageError("session", "other", "m")) == {
        "field": "session", "code": "other", "message": "m",
    }
```

In `tests/test_session_availability.py`:

```python
def _registry_with(monkeypatch, manager):
    monkeypatch.setattr("twicc.agent.registry.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda p: manager))
    monkeypatch.setattr("twicc.core.services.send_message.ensure_provider_running", lambda provider: None)
    monkeypatch.setattr("twicc.core.services.session_creation.ensure_provider_running", lambda provider: None)


def test_the_send_service_returns_the_code_and_the_reason(sessions, monkeypatch):
    from twicc.agent.base_manager import BaseAgentManager
    from twicc.core.services.send_message import send_message_to_session_from_payload

    failure = _start_failure()
    manager = MagicMock(spec=BaseAgentManager)
    manager.send_to_session = AsyncMock(side_effect=failure)
    manager.get_live_agent = lambda sid: None
    _registry_with(monkeypatch, manager)

    result = asyncio.run(send_message_to_session_from_payload({"session_id": "open", "text": "hi"}))

    assert result.success is False
    assert (result.errors[0].code, result.errors[0].reason) == ("agent_start_failed", "working_directory_missing")
    assert result.errors[0].message == str(failure)


def test_the_create_service_returns_the_code_and_the_reason(sessions, monkeypatch):
    from twicc.agent.base_manager import BaseAgentManager
    from twicc.core.services.session_creation import create_session_from_payload

    manager = MagicMock(spec=BaseAgentManager)
    manager.create_session = AsyncMock(side_effect=_start_failure())
    _registry_with(monkeypatch, manager)

    result = asyncio.run(create_session_from_payload({
        "session_id": "new-session", "project_id": PROJECT_ID, "provider": "claude_code", "text": "hi",
    }))

    assert result.success is False
    assert (result.errors[0].code, result.errors[0].reason) == ("agent_start_failed", "working_directory_missing")


@pytest.fixture
def db_write_passthrough(monkeypatch):
    async def passthrough(operation, **kwargs):
        return await operation()

    monkeypatch.setattr("twicc.asgi.run_under_db_write_lock", passthrough)


def test_the_websocket_new_session_frame_has_the_reason(sessions, consumer, monkeypatch, db_write_passthrough):
    from twicc.core.services.session_creation import SessionCreationError, SessionCreationResult

    error = SessionCreationError("session", "agent_start_failed", "did not start", "working_directory_missing")
    monkeypatch.setattr("twicc.asgi.create_session_from_payload", AsyncMock(
        return_value=SessionCreationResult(False, None, None, None, [error]),
    ))

    async def go():
        await consumer._handle_send_message({
            "type": "send_message", "session_id": "new-draft", "project_id": PROJECT_ID,
            "provider": "claude_code", "text": "hi", "request_id": "req-1",
        })
        await _drain()

    asyncio.run(go())

    [frame] = _frames(consumer)
    assert (frame["code"], frame["reason"]) == ("agent_start_failed", "working_directory_missing")


def test_the_websocket_frame_has_the_reason_and_no_ack(sessions, consumer, db_write_passthrough):
    failure = _start_failure()
    consumer.manager.send_to_session = AsyncMock(side_effect=failure)

    async def go():
        await consumer._handle_send_message({
            "type": "send_message", "session_id": "open", "project_id": PROJECT_ID,
            "text": "hi", "request_id": "req-1", "selected_model": "opus",
        })
        await _drain()

    with patch("twicc.asgi.logger") as logger:
        asyncio.run(go())

    [frame] = _frames(consumer)
    assert (frame["type"], frame["code"], frame["reason"], frame["request_id"]) == (
        "error", "agent_start_failed", "working_directory_missing", "req-1",
    )
    consumer.manager.send_to_session.assert_awaited_once()
    logger.warning.assert_not_called()


def test_the_websocket_frame_of_another_error_has_no_reason(sessions, consumer, db_write_passthrough):
    consumer.manager.send_to_session = AsyncMock(side_effect=SessionUnavailableError(
        "gone", session_id="open", reason="some_reason",
    ))

    async def go():
        await consumer._handle_send_message({
            "type": "send_message", "session_id": "open", "project_id": PROJECT_ID,
            "text": "hi", "request_id": "req-1", "selected_model": "opus",
        })
        await _drain()

    asyncio.run(go())

    [frame] = _frames(consumer)
    consumer.manager.send_to_session.assert_awaited_once()
    assert frame["code"] == SESSION_UNAVAILABLE
    assert "reason" not in frame
```

In `frontend/src/composables/wsAgentDispatch.test.js`:

```js
test('a failed send keeps its translated message across a reload', () => {
    const store = readFileSync(new URL('../stores/data.js', import.meta.url), 'utf8')
    assert.match(store, /failed: \{ code, message, messageKey, messageParams, ui_message: failedSend\.ui_message, failedAt \}/)
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_start_failure.py tests/test_session_availability.py -v -k "status_file or the_code_and or websocket_frame or websocket_new_session"`
Expected: FAIL, except `test_the_websocket_frame_of_another_error_has_no_reason`, which is a guard and passes before the change.

Run: `cd frontend && node --test src/composables/wsAgentDispatch.test.js`
Expected: FAIL on the reload test.

- [ ] **Step 3: Implement**

- `SendMessageError`, `SessionCreationError`: add `reason: str | None = None` as the last field. `_rejected(field, code, message, reason=None)`.
- `send_message.py`, `except RuntimeError` at line 288 and at line 308 (the completion wait): pass `reason=e.reason` when `isinstance(e, AgentStartError)`.
- `session_creation.py`, `except RuntimeError` at line 524 and at line 545 (the completion wait): same for the `SessionCreationError`.
- `drop_requests_watcher.py`: add `_error_payload` and use it at line 289.
- `asgi.py`, new session branch at line 1476: add `reason` to `extra` when `first.reason` is set.
- `asgi.py`, `except RuntimeError` at line 1484: skip `logger.warning` for an `AgentStartError`, because the failure is already logged. Add `reason=e.reason` to `send_error` for an `AgentStartError`. Do not read `.reason` on other errors: `SessionUnavailableError` also has that attribute.
- `frontend/src/stores/data.js:4238`: add `ui_message: failedSend.ui_message` to the persisted `failed` object, between `messageParams` and `failedAt`. `_applySendFailure` already reads `info.ui_message`, so the banner stays translated after a reload.

- [ ] **Step 4: Update the documentation**

In the `twicc-send-message` server code list and the `twicc-create-session` server code list, add:

```
- `agent_start_failed` — the agent did not start, and the message was not delivered: a new send is safe. `reason` is `working_directory_missing`, `cli_not_found`, `permission_denied` or `start_failed`.
```

In `twicc-send-messages/SKILL.md:84`, name `agent_start_failed` among the per-session outcomes. In `SKILLS-AND-CLI.md`, add one line to the `send-message`, `send-messages` and `create-session` entries. Increase the patch number of `version` in `plugin.json`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_start_failure.py tests/test_attachment_drop_requests.py tests/test_composer_attachment_send.py tests/test_core_ui_messages.py tests/test_session_availability.py -v`
Expected: PASS.

Run: `cd frontend && node --test src/composables/wsAgentDispatch.test.js`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/stores/data.js frontend/src/composables/wsAgentDispatch.test.js src/twicc/asgi.py src/twicc/core/services/send_message.py src/twicc/core/services/session_creation.py src/twicc/drop_requests_watcher.py src/twicc/agent/plugin/twicc/skills/twicc-send-message/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-send-messages/SKILL.md src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json SKILLS-AND-CLI.md tests/test_agent_start_failure.py tests/test_session_availability.py
git commit -m "feat(agent): expose agent start failures to callers"
```

---

### Task 6: Full verification

**Files:** none, except fixes that this task finds.

- [ ] **Step 1: Run the complete suites**

Run: `uv run pytest`
Expected: PASS.

Run: `cd frontend && npm test`
Expected: PASS.

Run: `cd frontend && npm run lint:i18n`
Expected: PASS.

Run: `uvx ruff check src/twicc/agent src/twicc/providers/claude_code/agent src/twicc/core/services tests/test_agent_start_failure.py`
Expected: no new finding.

- [ ] **Step 2: Check each acceptance criterion against a test or a run**

| Criteria | Evidence |
|---|---|
| A1, A4, A10, A11 | Task 2 directory tests, Task 5 service and frame tests. For A11, `create-session --project <removed path>` stops earlier at the CLI as `invalid_project`, exit 1 (`src/twicc/cli/create_session/command.py:470`). Only the project id form reaches `agent_start_failed`. |
| A2, A8, A9, A13, A15 | Task 3 tests, Task 2 frontend guard test, Task 5 frame test |
| A3 | Task 2 directory test: the check runs before `_create_agent`, so no tmux session exists |
| A5, A6, A7, A25 | Task 1 tests, `src/i18n/index.test.js` parity |
| A12, A20 | Task 2 Codex tests |
| A14 | Indirect: an agent that started reaches `DEAD` in Tasks 2 and 3, and a failure inside `_create_agent` broadcasts no `starting`. The real run in Step 3 confirms it. |
| A16 | Indirect: the send record is `rejected` in Task 2, and Retry uses a new request id. The real run in Step 3 confirms it. |
| A17, A18 | Task 3 tests |
| A19 | Task 2 `test_codex_failure_while_the_turn_opens_stays_uncertain`, Task 3 `test_query_failure_stays_uncertain` |
| A21 | Adoption calls `_register_and_start` directly, never `_start_agent`: `src/twicc/providers/claude_code/agent/manager.py:1001` |
| A22, A23 | Task 2 log tests |
| A24 | Task 5 Step 4 |
| A26 | Task 2 availability test |

- [ ] **Step 3: Real run, only if the owner asks for it**

Start a worktree server with `uv run ./devctl.py start`. Remove the directory of a test project. Send a message from two browser tabs, for one Codex session and one Claude session. Check that the banner names the provider and the directory, that Retry, Edit and Delete are active, and that no tab stays in `starting`. Restore the directory, then Retry, and check that the message is delivered once. Without this run, tell the owner that A14 and A16 have only indirect evidence.

- [ ] **Step 4: Remind the owner of the reserved operations**

A backend restart through `devctl.py` is needed after the backend changes. No migration is created.
