"""
Manual diagnostic of what a ``claude-agent-sdk`` / bundled Claude Code CLI bump can change for TwiCC.

Run it AFTER restarting the backend on the new SDK:

    uv run python scripts/diagnose_claude_sdk.py [--live] [--yes] [--json] [--only O1,B,...]

Without ``--live`` only the offline checks run (no model call, no session):

  O1  the installed SDK is the one pyproject.toml pins
  O2  the bundled CLI runs, and which version it is
  O3  the bundled CLI still reads the ``idleCompaction`` setting TwiCC forces off
  O4  that setting reaches both launch paths (SDK agent, hybrid CLI)
  O5  the backend was started after the last change of pyproject.toml / uv.lock (a proxy, so a WARN at most)
  O6  AskUserQuestion's input schema still declares ``answers``: the hybrid mode answers a question through a
      PermissionRequest hook's ``updatedInput.answers``, and Claude Code 2.1.300 drops the unknown keys of that
      input, so a schema that lost the key would silently lose every answer

With ``--live`` it also spends tokens, through the running backend and the ``twicc`` CLI:

  B1  a hidden read-only Sonnet session reads its own identity through the TwiCC MCP tool: MCP loads, the
      ``mcp__twicc`` allow-list holds, the call runs
  B2  the launched ``claude`` process carries the forced settings and the environment TwiCC sets
      (``idleCompaction`` off, the long Bash ceiling, the todo tools)
  B3  the session is stopped then resumed and still knows what it was told, on a process launched the same way
  B4  the throwaway session is archived and hidden (it is left behind, never deleted)

NOT covered, to check by hand:

  - a real hybrid session (only the UI can create one): create it in TwiCC, ask for a multiple-choice question,
    answer it in the UI, and check the agent carries on; ``ps`` on its ``claude`` process must show
    ``"idleCompaction":false`` in ``--settings``. The hook channel cannot be exercised headlessly: AskUserQuestion
    is only offered to a CLI given ``--permission-prompt-tool stdio``, and that mode asks through stdio, not hooks;
  - answering an AskUserQuestion on an SDK session (the ``can_use_tool`` path);
  - the effect of ``idleCompaction`` itself (a 200K+ token session left idle for over an hour);
  - a live model switch (``set_model``) on a running session.

Exit codes: 0 all good, 1 at least one FAIL or INCONCLUSIVE, 2 the diagnostic could not run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnose_hermetic_llm import FAIL, INCONCLUSIVE, PASS, WARN, Report  # noqa: E402

IDLE_KEY = b"idleCompaction"
# Read from the backend's .env only: an inherited value is dropped with a warning that pollutes a command's output.
PROVIDER_HOME_KEYS = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CODEX_HOME")
CLI_TIMEOUT_SECONDS = 180
BACKEND_REPLY_TIMEOUT_SECONDS = 240


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """``os.environ`` without the variables a Claude Code session exports (they make a nested CLI act as a child) nor the provider homes."""
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("CLAUDE_CODE", "CLAUDECODE")) and k not in PROVIDER_HOME_KEYS
    }
    env.update(extra or {})
    return env


def run(argv: list[str], *, timeout: float = 60, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env or clean_env())


def bundled_cli() -> Path:
    import claude_agent_sdk

    return Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"


def installed_sdk_version() -> str:
    from importlib.metadata import version

    return version("claude-agent-sdk")


def pinned_sdk_version() -> str | None:
    match = re.search(r'"claude-agent-sdk==([^"]+)"', (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return match.group(1) if match else None


def parse_result(text: str):
    """The JSON a ``twicc`` command printed, unwrapped from ``{"exit_code", "result"}`` when it carries that."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, dict) and "exit_code" in data and "result" in data:
        return data["result"]
    return data


# --- offline checks ------------------------------------------------------------------------------------------


def check_o1(report: Report) -> None:
    pin, installed = pinned_sdk_version(), installed_sdk_version()
    if pin is None:
        report.add("O1", FAIL, "pyproject.toml carries no claude-agent-sdk==X pin")
    elif pin != installed:
        report.add("O1", FAIL, f"pyproject.toml pins {pin} but the environment has {installed}: sync it first")
    else:
        report.add("O1", PASS, f"claude-agent-sdk {installed} is installed and matches the pin")


def check_o2(report: Report) -> None:
    cli = bundled_cli()
    try:
        result = run([str(cli), "--version"], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        report.add("O2", FAIL, f"the bundled CLI does not run: {error}")
        return
    if result.returncode != 0:
        report.add("O2", FAIL, f"`claude --version` exited {result.returncode}: {result.stderr.strip()[:120]}")
    else:
        report.add("O2", PASS, f"bundled CLI {result.stdout.strip()}")


def check_o3(report: Report) -> None:
    count = bundled_cli().read_bytes().count(IDLE_KEY)
    if count:
        report.add("O3", PASS, f"the bundled CLI reads idleCompaction ({count} mentions)")
    else:
        report.add("O3", FAIL, "the bundled CLI never mentions idleCompaction: the forced setting would be a dead key")


O4_PROBE = """
import json, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings_test")
import django
django.setup()
from twicc.providers.claude_code.agent.agent import build_sdk_flag_settings
from twicc.providers.claude_code.agent.hybrid.launch import build_hooks_settings
from twicc.providers.claude_code.constants import FORCED_CLI_SETTINGS
print(json.dumps({
    "forced": FORCED_CLI_SETTINGS,
    "sdk": json.loads(build_sdk_flag_settings(False)),
    "hybrid": json.loads(build_hooks_settings("diag", False)),
}))
"""


def check_o4(report: Report) -> None:
    # A child process: the test settings of django.setup() export CLAUDE_CONFIG_DIR & co. and create a temp
    # directory, which this process (and the twicc commands the live checks run) must not inherit.
    result = run([sys.executable, "-c", O4_PROBE], timeout=120)
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        report.add("O4", INCONCLUSIVE, f"the probe printed no JSON (exit {result.returncode}): {result.stderr.strip()[-160:]}")
        return
    problems = []
    for label in ("sdk", "hybrid"):
        for key, value in data["forced"].items():
            if data[label].get(key) is not value:
                problems.append(f"{label}: {key}={data[label].get(key)!r}, expected {value!r}")
    if problems:
        report.add("O4", FAIL, "; ".join(problems))
    else:
        report.add("O4", PASS, f"{sorted(data['forced'])} reach the SDK and hybrid launch settings")


def check_o5(report: Report) -> None:
    result = run([sys.executable, str(REPO_ROOT / "run.py"), "status"], timeout=60)
    data = parse_result(result.stdout)
    if not isinstance(data, dict) or data.get("status") != "running":
        report.add("O5", WARN, f"the backend does not report `running` ({result.stdout.strip()[:100] or 'no output'})")
        return
    started = datetime.fromisoformat(data["started_at"]).timestamp()
    newest = max((REPO_ROOT / name).stat().st_mtime for name in ("pyproject.toml", "uv.lock"))
    if started < newest:
        report.add(
            "O5", WARN,
            "the backend started before pyproject.toml / uv.lock last changed: restart it so it loads the new SDK",
        )
    else:
        report.add("O5", PASS, "the backend started after the last change of pyproject.toml / uv.lock")


def check_o6(report: Report) -> None:
    needle = b"User answers collected by the permission component"
    if needle in bundled_cli().read_bytes():
        report.add("O6", PASS, "AskUserQuestion's input schema still declares `answers`, which the hybrid protocol sends")
    else:
        report.add(
            "O6", FAIL,
            "AskUserQuestion's input schema no longer declares `answers`: a hook's updatedInput would lose the "
            "answers the hybrid mode sends (Claude Code 2.1.300 drops unknown keys)",
        )


# --- B1-B4 : the running backend, through the twicc CLI --------------------------------------------------------


def twicc(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return run([sys.executable, str(REPO_ROOT / "run.py"), *args], timeout=timeout)


def session_process(session_id: str) -> tuple[int, list[str]] | None:
    """(pid, argv) of the live ``claude`` process behind a session, if any."""
    found = run(["pgrep", "-f", session_id], timeout=10)
    for pid in (int(p) for p in found.stdout.split()):
        try:
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
        except OSError:
            continue
        if argv and Path(argv[0]).name == "claude" and "--settings" in argv:
            return pid, argv
    return None


def check_process(report: Report, check_id: str, session_id: str, label: str) -> None:
    from_process = session_process(session_id)
    if from_process is None:
        report.add(check_id, INCONCLUSIVE, f"no live `claude` process found for {session_id} ({label})")
        return
    pid, argv = from_process
    try:
        settings = json.loads(argv[argv.index("--settings") + 1])
    except (ValueError, IndexError):
        report.add(check_id, INCONCLUSIVE, f"the --settings of pid {pid} is not inline JSON ({label})")
        return
    env = dict(
        item.split("=", 1)
        for item in Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace").split("\0")
        if "=" in item
    )
    problems = []
    if settings.get("idleCompaction") is not False:
        problems.append(f"idleCompaction={settings.get('idleCompaction')!r} in --settings")
    if env.get("BASH_MAX_TIMEOUT_MS") != "2147483647":
        problems.append(f"BASH_MAX_TIMEOUT_MS={env.get('BASH_MAX_TIMEOUT_MS')!r}")
    if env.get("CLAUDE_CODE_ENABLE_TODO_TOOLS") != "1":
        problems.append(f"CLAUDE_CODE_ENABLE_TODO_TOOLS={env.get('CLAUDE_CODE_ENABLE_TODO_TOOLS')!r}")
    if problems:
        report.add(check_id, FAIL, f"pid {pid} ({label}): " + "; ".join(problems))
    else:
        report.add(check_id, PASS, f"pid {pid} ({label}): idleCompaction off, long Bash ceiling and todo tools on")


def reply_of(result: subprocess.CompletedProcess) -> tuple[str | None, str | None, str | None]:
    """(session_id, outcome, text) of a command run with ``--wait-reply``."""
    data = parse_result(result.stdout)
    if not isinstance(data, dict):
        return None, None, None
    reply = data.get("reply") or {}
    return data.get("session_id"), reply.get("outcome"), reply.get("text")


def run_backend_checks(report: Report) -> None:
    status = twicc("status", timeout=60)
    if status.returncode != 0:
        report.add("B0", INCONCLUSIVE, "the TwiCC backend is not running: nothing to drive")
        return
    codeword = f"PELICAN-{uuid4().hex[:8]}"
    prompt = (
        "Reply with exactly two lines and nothing else. "
        "Line 1: call the twicc `whoami` MCP tool (a deferred tool: load it with ToolSearch first if needed) and "
        "write the `id` it returns. "
        f"Line 2: write the codeword {codeword}."
    )
    session_id = None
    try:
        created = twicc(
            "create-session", prompt, "--project", str(REPO_ROOT), "--hidden", "--model", "sonnet", "--effort", "low",
            "--permission-mode", "dontAsk", "--no-question-widget", "--wait-reply",
            "--wait-timeout", str(BACKEND_REPLY_TIMEOUT_SECONDS), timeout=BACKEND_REPLY_TIMEOUT_SECONDS + 60,
        )
        session_id, outcome, text = reply_of(created)
        if session_id is None:
            report.add("B1", INCONCLUSIVE, f"create-session printed no session: {created.stdout.strip()[:140] or created.stderr.strip()[:140]}")
            return
        if outcome != "replied":
            report.add("B1", INCONCLUSIVE, f"the session {session_id} did not reply (outcome {outcome!r})")
        elif session_id in (text or "") and codeword in text:
            report.add("B1", PASS, f"session {session_id}: the whoami MCP tool ran and the reply carries its id")
        elif codeword in (text or ""):
            report.add(
                "B1", FAIL,
                f"session {session_id} replied but not with the id the whoami MCP tool returns: MCP did not load, "
                f"was refused or failed; reply {text[:100]!r}",
            )
        else:
            report.add("B1", INCONCLUSIVE, f"session {session_id}: unexpected reply {(text or '')[:100]!r}")
        check_process(report, "B2", session_id, "first launch")

        twicc("session", session_id, "stop", timeout=60)
        for _ in range(30):
            if session_process(session_id) is None:
                break
            time.sleep(1)
        resumed = twicc(
            "send-message", session_id, "What was the codeword I asked you to write? Reply with the codeword only.",
            "--wait-reply", "--wait-timeout", str(BACKEND_REPLY_TIMEOUT_SECONDS),
            timeout=BACKEND_REPLY_TIMEOUT_SECONDS + 60,
        )
        _, outcome, text = reply_of(resumed)
        if outcome != "replied":
            report.add("B3", INCONCLUSIVE, f"the resumed session did not reply (outcome {outcome!r})")
        elif codeword in (text or ""):
            report.add("B3", PASS, "stopped then resumed: the conversation came back intact")
        else:
            report.add("B3", FAIL, f"resumed without its conversation: asked for {codeword}, got {(text or '')[:100]!r}")
        check_process(report, "B3b", session_id, "resumed launch")
    except subprocess.TimeoutExpired as error:
        report.add("B1", INCONCLUSIVE, f"a twicc command timed out: {error.cmd[3:6]}")
    finally:
        if session_id is not None:
            archived = twicc("update-session", session_id, "archive", timeout=60)
            hidden = twicc("update-session", session_id, "hide", timeout=60)
            ok = archived.returncode == 0 and hidden.returncode == 0
            report.add(
                "B4", PASS if ok else WARN,
                f"throwaway session {session_id} {'archived and hidden' if ok else 'left as is (archive/hide failed)'}",
            )


# --- driver ----------------------------------------------------------------------------------------------------

OFFLINE = [
    ("O1", check_o1), ("O2", check_o2), ("O3", check_o3), ("O4", check_o4), ("O5", check_o5), ("O6", check_o6),
]
LIVE = [("B", run_backend_checks)]


def wanted(check_id: str, only: set[str] | None) -> bool:
    return only is None or any(o == check_id or o.startswith(check_id) for o in only)


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-bump diagnostic of the Claude Agent SDK and its bundled CLI.")
    parser.add_argument("--live", action="store_true", help="also run the checks that spend tokens (B1-B4)")
    parser.add_argument("--yes", action="store_true", help="do not ask before spending tokens")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--only", help="comma-separated check ids (O1, B...), default all")
    args = parser.parse_args()
    only = {part.strip() for part in args.only.split(",") if part.strip()} if args.only else None

    report = Report()
    for check_id, check in OFFLINE:
        if wanted(check_id, only):
            try:
                check(report)
            except Exception as error:  # a broken check must not hide the others
                report.add(check_id, INCONCLUSIVE, f"the check itself failed: {type(error).__name__}: {error}")

    live_ids = [check_id for check_id, _ in LIVE if wanted(check_id, only)]
    if args.live and live_ids:
        if not args.yes:
            if not sys.stdin.isatty():
                report.abort("live checks spend tokens: pass --yes when stdin is not a terminal")
                print(report.to_json() if args.json else report.render())
                return report.exit_code()
            answer = input(
                "Live checks spend tokens (B1-B3 run two Sonnet turns at low effort) and leave one hidden, "
                "archived session. Proceed? [y/N] "
            )
            if answer.strip().lower() != "y":
                report.abort("declined")
                print(report.to_json() if args.json else report.render())
                return report.exit_code()
        for check_id, check in LIVE:
            if check_id in live_ids:
                try:
                    check(report)
                except Exception as error:
                    report.add(check_id, INCONCLUSIVE, f"the check itself failed: {type(error).__name__}: {error}")

    print(report.to_json() if args.json else report.render())
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
