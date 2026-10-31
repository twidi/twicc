"""Tests for the script runner of the text-to-speech script configs (twicc.tts.script).

Every command runs through ``/bin/sh -c`` (``shell_argv`` is replaced: never the user's login
profile), and every helper program runs with ``sys.executable``. Every await is bounded.

Every test runs its whole scenario inside one ``asyncio.run(...)``: streams and their futures
belong to the loop that created them.
"""

import asyncio
import logging
import os
import shlex
import shutil
import signal
import sys
import time

import pytest

from tests.test_tts_part_audio import _frame, _id3
from tests.test_tts_script_audio import _wav
from twicc import terminal
from twicc.tts import script, streams
from twicc.tts.openrouter import SpeechError
from twicc.tts.parts import split_text
from twicc.tts.script import (
    COULD_NOT_START,
    FILE_UNREADABLE,
    NOT_A_PATH,
    PATH_REUSED,
    STALLED,
    TOO_LARGE,
    ScriptSpeechRequest,
    pump_script_speech,
    script_parts,
)
from twicc.tts.script_audio import NO_AUDIO, NOT_MP3
from twicc.tts.streams import PART_TYPE_CHANGED, AudioStream

STREAMS_LOGGER_NAME = "twicc.tts.streams"
LABEL = {"kind": "script", "output": "stream", "format": "pcm", "chars": 5}
PCM_8000 = "audio/pcm;rate=8000;channels=1"
# Raw PCM that does not start like a known container.
PCM = bytes(range(1, 101)) * 10
MP3 = _id3(20) + _frame(b"Info") + _frame() + _frame()
MP3_FRAMES = _frame() + _frame()
ORIGINAL_SHELL_ARGV = script.shell_argv


@pytest.fixture(autouse=True)
def _plain_shell(monkeypatch):
    monkeypatch.setattr(script, "shell_argv", lambda command: ["/bin/sh", "-c", command])


@pytest.fixture(autouse=True)
def _reset_streams():
    streams._reset_for_tests()
    yield
    streams._reset_for_tests()


@pytest.fixture
def streams_log(caplog, monkeypatch):
    """Route the streams logger straight to caplog, at INFO (settings_test can disable it)."""
    logger = logging.getLogger(STREAMS_LOGGER_NAME)
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    caplog.set_level(logging.INFO, logger=STREAMS_LOGGER_NAME)
    return caplog


@pytest.fixture
def groups(tmp_path):
    """The process group files of the commands that write ``$$`` (their shell leads the group).

    At teardown, every group still alive gets SIGKILL: no ``sleep`` outlives its test.
    """
    files = []

    def group_file(name: str = "pgid"):
        path = tmp_path / name
        files.append(path)
        return path

    yield group_file
    for path in files:
        try:
            os.killpg(int(path.read_text()), signal.SIGKILL)
        except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
            pass


def _request(command, *, text="Hello", output="stream", audio_format="pcm", pcm_rate=8000, max_chars=None):
    return ScriptSpeechRequest(
        text=text,
        command=command,
        output=output,
        audio_format=audio_format,
        pcm_rate=pcm_rate if audio_format == "pcm" else None,
        pcm_channels=1,
        max_chars=max_chars,
    )


async def _within(awaitable, seconds: float = 5.0):
    """Await *awaitable*, failing the test instead of hanging."""
    async with asyncio.timeout(seconds):
        return await awaitable


async def _until(predicate, seconds: float = 5.0) -> None:
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.01)


async def _pump(request, parts=None, *, seconds: float = 5.0, paced=False, delete_file=True) -> AudioStream:
    stream = AudioStream(LABEL)
    await _within(pump_script_speech(request, stream, parts, paced=paced, delete_file=delete_file), seconds)
    return stream


async def _failed_open(stream: AudioStream) -> str:
    """The message of a speech that failed its open step (before any headers)."""
    with pytest.raises(SpeechError) as exc_info:
        await _within(stream.ready, 0.1)
    assert stream.state == "failed"
    assert stream.content_type is None
    return exc_info.value.message


def _file(tmp_path, name: str, content: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(content)
    return shlex.quote(str(path))


def _helper(tmp_path, name: str, source: str) -> str:
    """The command that runs the Python *source* with this interpreter."""
    path = tmp_path / name
    path.write_text(source)
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"


def _group_gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    return False


async def _read_pgid(path) -> int:
    await _until(lambda: path.exists() and path.read_text().strip())
    return int(path.read_text())


def _speech_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == STREAMS_LOGGER_NAME and r.levelno == logging.INFO]


def test_script_parts_follow_the_output_kind():
    text = " ".join(["word"] * 1000)
    assert script_parts(_request("x", text=text)) == [text]

    stream_parts = script_parts(_request("x", text=text, output="stream", max_chars=100))
    assert stream_parts == split_text(text, 100, 100)
    assert all(95 <= len(part) <= 100 for part in stream_parts[:-1])

    for output in ("whole", "path"):
        parts = script_parts(_request("x", text=text, output=output, max_chars=1000))
        assert parts == split_text(text, 1000, 250)
        assert [len(part) // 10 for part in parts[:3]] == [24, 49, 99]  # 249, 499, 999


def test_shell_argv(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    assert ORIGINAL_SHELL_ARGV("say hi") == ["/bin/zsh", "-l", "-c", "say hi"]
    monkeypatch.delenv("SHELL")
    assert ORIGINAL_SHELL_ARGV("say hi") == ["/bin/sh", "-l", "-c", "say hi"]
    monkeypatch.setenv("SHELL", "")
    assert ORIGINAL_SHELL_ARGV("say hi") == ["/bin/sh", "-l", "-c", "say hi"]


def test_terminal_environment_is_a_cleaned_copy(monkeypatch):
    # Keys that the purge and the sanitizing remove: they survive only when the overlay comes last.
    overlay = {"CODEX_HOME": "/instance/codex", "CLAUDE_CODE_OVERLAID": "kept", "GIT_PAGER": "kept"}
    monkeypatch.setattr(terminal, "provider_env_overlay", lambda: overlay)
    base = {"PATH": "/usr/bin", "GIT_EDITOR": "true", "CLAUDECODE": "1", "CODEX_HOME": "/other"}
    env = terminal.terminal_environment(base)
    assert env["PATH"] == "/usr/bin"
    assert "GIT_EDITOR" not in env  # the terminal sanitizing
    assert "CLAUDECODE" not in env  # the provider purge
    assert {key: env[key] for key in overlay} == overlay  # the provider homes, applied last
    assert base["GIT_EDITOR"] == "true"  # the base is not changed


def test_the_text_reaches_stdin_unchanged(tmp_path):
    text = f"$(touch {tmp_path}/injected) `touch {tmp_path}/injected2`; echo x — é ü 日本"

    async def scenario():
        stream = await _pump(_request("cat", text=text))
        assert (stream.state, stream.content_type) == ("complete", PCM_8000)
        assert bytes(stream.data) == text.encode()

    asyncio.run(scenario())
    assert not (tmp_path / "injected").exists()
    assert not (tmp_path / "injected2").exists()


_PIECES = """
import sys, time
sys.stdin.buffer.read()
data = open(sys.argv[1], "rb").read()
count = int(sys.argv[2])
size = -(-len(data) // count)
for start in range(0, len(data), size):
    sys.stdout.buffer.write(data[start : start + size])
    sys.stdout.buffer.flush()
    time.sleep(0.05)
"""


def test_a_streamed_wav_plays_as_pcm(tmp_path):
    samples = bytes(range(1, 201)) * 3
    wav = _file(tmp_path, "speech.wav", _wav(samples))  # unknown sizes: a WAV written as it goes

    async def scenario():
        stream = await _pump(_request(f"{_helper(tmp_path, 'pieces.py', _PIECES)} {wav} 3", audio_format="wav"))
        assert (stream.state, stream.content_type) == ("complete", PCM_8000)
        assert bytes(stream.data) == samples

    asyncio.run(scenario())


_WRITE_THEN_WAIT = """
import os, sys, time
sys.stdin.buffer.read()
sys.stdout.buffer.write(open(sys.argv[1], "rb").read())
sys.stdout.buffer.flush()
deadline = time.monotonic() + 30
while not os.path.exists(sys.argv[2]) and time.monotonic() < deadline:
    time.sleep(0.01)
"""


def test_audio_streams_before_the_command_exits(tmp_path):
    go = tmp_path / "go"
    command = f"{_helper(tmp_path, 'wait.py', _WRITE_THEN_WAIT)} {_file(tmp_path, 'a.pcm', PCM)} {shlex.quote(str(go))}"

    async def scenario():
        stream = AudioStream(LABEL)
        pump = asyncio.create_task(pump_script_speech(_request(command), stream))
        await _within(stream.ready)
        await _until(lambda: len(stream.data) == len(PCM))
        assert stream.state == "running"  # the command waits for the go file: it still runs
        go.touch()
        await _within(pump)
        assert stream.state == "complete"
        assert bytes(stream.data) == PCM

    asyncio.run(scenario())


def test_a_banner_before_the_audio_fails_with_the_format_message(tmp_path):
    async def scenario():
        stream = await _pump(_request(f"echo hi; cat {_file(tmp_path, 'a.mp3', MP3)}", audio_format="mp3"))
        assert await _failed_open(stream) == NOT_MP3

    asyncio.run(scenario())


def test_a_failing_command_shows_its_last_stderr_line(streams_log):
    async def scenario():
        stream = await _pump(_request("echo one >&2; echo 'sh: piper: not found' >&2; echo >&2; exit 127"))
        assert await _failed_open(stream) == "sh: piper: not found"
        assert stream.error == "sh: piper: not found"

    asyncio.run(scenario())
    [line] = _speech_lines(streams_log)
    assert "error='script exit status 127'" in line
    assert "piper" not in line


def test_a_long_stderr_line_is_cut():
    async def scenario():
        stream = await _pump(_request("printf '%0500d' 0 >&2; exit 1"))
        message = await _failed_open(stream)
        assert message == "0" * 299 + "…"

    asyncio.run(scenario())


def test_a_failing_command_without_stderr_shows_its_status():
    async def scenario():
        stream = await _pump(_request("exit 3"))
        assert await _failed_open(stream) == "The script failed (exit status 3)"

    asyncio.run(scenario())


def test_a_killed_command_shows_its_signal():
    async def scenario():
        stream = await _pump(_request("kill -9 $$"))
        assert await _failed_open(stream) == "The script was stopped by signal 9"

    asyncio.run(scenario())


def test_a_command_that_cannot_start(monkeypatch):
    monkeypatch.setattr(script, "shell_argv", lambda command: ["/no/such/shell", "-c", command])

    async def scenario():
        stream = await _pump(_request("cat"))
        assert await _failed_open(stream) == COULD_NOT_START

    asyncio.run(scenario())


def test_a_command_with_a_nul_byte_cannot_start():
    async def scenario():
        stream = await _pump(_request("echo a\x00b"))
        assert await _failed_open(stream) == COULD_NOT_START

    asyncio.run(scenario())


class _HeldStart:
    """Holds the start of the command after its spawn, until :meth:`release` (the shell already
    runs, inside the awaited start). Create it inside the scenario's loop."""

    def __init__(self, monkeypatch):
        real_start = asyncio.create_subprocess_exec
        self.procs = []
        self._released = asyncio.Event()

        async def held_start(*args, **kwargs):
            proc = await real_start(*args, **kwargs)
            self.procs.append(proc)
            await self._released.wait()
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", held_start)

    def release(self) -> None:
        self._released.set()


@pytest.mark.parametrize("cancels", [1, 2])  # 2: a release, then a shutdown cancel
def test_a_release_while_the_command_starts_stops_its_group(groups, monkeypatch, cancels):
    pgid_file = groups()

    async def scenario():
        held = _HeldStart(monkeypatch)
        stream = AudioStream(LABEL)
        pump = asyncio.create_task(pump_script_speech(_request(f"echo $$ > {pgid_file}; sleep 30 | sleep 30"), stream))
        pgid = await _read_pgid(pgid_file)
        assert not _group_gone(pgid)
        for _ in range(cancels):
            pump.cancel()
            await asyncio.sleep(0.05)
        held.release()
        with pytest.raises(asyncio.CancelledError):
            await _within(pump, 3)
        await _until(lambda: _group_gone(pgid), 3)

    asyncio.run(scenario())


def test_a_command_that_exits_while_it_starts_is_not_signalled(groups, monkeypatch):
    pgid_file = groups()

    async def scenario():
        held = _HeldStart(monkeypatch)
        stream = AudioStream(LABEL)
        command = f"echo $$ > {pgid_file}; sleep 30 </dev/null >/dev/null 2>&1 &"
        pump = asyncio.create_task(pump_script_speech(_request(command), stream))
        pgid = await _read_pgid(pgid_file)
        await _until(lambda: held.procs and held.procs[0].returncode is not None)  # the shell exited
        pump.cancel()
        await asyncio.sleep(0.05)
        held.release()
        with pytest.raises(asyncio.CancelledError):
            await _within(pump, 3)
        await asyncio.sleep(0.2)
        assert not _group_gone(pgid)  # its leftover still runs: never signalled

    asyncio.run(scenario())


def test_a_command_that_exits_in_the_pass_of_a_stop_reason_is_not_signalled(groups, monkeypatch):
    # The read gives its chunk only once the command exited: the cap is found in the same pass.
    monkeypatch.setattr(script, "SCRIPT_MAX_BYTES", 1000)
    real_read = script._ScriptRun._read_stdout

    async def read_after_the_exit(self, timeout):
        chunk = await real_read(self, timeout)
        if chunk:
            await _until(lambda: self._proc.returncode is not None)
        return chunk

    monkeypatch.setattr(script._ScriptRun, "_read_stdout", read_after_the_exit)
    pgid_file = groups()

    async def scenario():
        command = f"echo $$ > {pgid_file}; sleep 30 </dev/null >/dev/null 2>&1 & head -c 5000 /dev/zero"
        stream = await _pump(_request(command))
        assert (stream.state, stream.error) == ("failed", TOO_LARGE)
        await asyncio.sleep(0.2)
        assert not _group_gone(int(pgid_file.read_text()))  # its leftover still runs: never signalled

    asyncio.run(scenario())


def test_a_mismatch_of_a_failing_command_shows_stderr():
    async def scenario():
        stream = await _pump(_request("echo some text; echo 'engine crashed' >&2; exit 2", audio_format="mp3"))
        assert await _failed_open(stream) == "engine crashed"

    asyncio.run(scenario())


def test_stderr_of_a_successful_command_is_ignored(tmp_path):
    async def scenario():
        stream = await _pump(_request(f"echo 'progress: 100%' >&2; cat {_file(tmp_path, 'a.pcm', PCM)}"))
        assert (stream.state, stream.error) == ("complete", None)
        assert bytes(stream.data) == PCM

    asyncio.run(scenario())


def test_an_empty_output_is_no_audio():
    async def scenario():
        stream = await _pump(_request("cat > /dev/null"))
        assert await _failed_open(stream) == NO_AUDIO

    asyncio.run(scenario())


_WRITE_THEN_READ = """
import sys
sys.stdout.buffer.write(open(sys.argv[1], "rb").read())
sys.stdout.buffer.flush()
assert len(sys.stdin.buffer.read()) == int(sys.argv[2])
"""


def test_a_long_text_while_the_command_writes_first(tmp_path):
    audio = PCM * 1000  # 1 MB
    text = "a" * (2 * 1024 * 1024)
    command = f"{_helper(tmp_path, 'first.py', _WRITE_THEN_READ)} {_file(tmp_path, 'a.pcm', audio)} {len(text)}"

    async def scenario():
        stream = await _pump(_request(command, text=text), seconds=10)
        assert stream.state == "complete"
        assert bytes(stream.data) == audio

    asyncio.run(scenario())


def test_a_background_child_keeping_stdout_open_does_not_hold_the_run(tmp_path, groups):
    pgid_file = groups()
    command = f"echo $$ > {pgid_file}; sleep 30 & cat {_file(tmp_path, 'a.pcm', PCM)}"

    async def scenario():
        before = len(os.listdir("/dev/fd"))
        started = time.monotonic()
        stream = await _pump(_request(command), seconds=2)
        assert time.monotonic() - started < 2
        assert stream.state == "complete"
        assert bytes(stream.data) == PCM
        assert len(os.listdir("/dev/fd")) <= before
        assert not _group_gone(int(pgid_file.read_text()))  # an exited command is never signalled

    asyncio.run(scenario())


def test_a_failing_command_with_a_background_child_fails_at_once(groups):
    async def scenario():
        stream = await _pump(_request(f"echo $$ > {groups()}; sleep 30 & echo err >&2; exit 3"), seconds=2)
        assert await _failed_open(stream) == "err"

    asyncio.run(scenario())


def test_a_release_stops_the_whole_pipeline(groups):
    pgid_file = groups()

    async def scenario():
        stream = AudioStream(LABEL)
        pump = asyncio.create_task(pump_script_speech(_request(f"echo $$ > {pgid_file}; sleep 30 | sleep 30"), stream))
        pgid = await _read_pgid(pgid_file)
        assert not _group_gone(pgid)  # the shell leads its own group
        pump.cancel()
        with pytest.raises(asyncio.CancelledError):
            await _within(pump, 3)
        await _until(lambda: _group_gone(pgid), 3)

    asyncio.run(scenario())


def test_a_release_forces_a_child_that_ignores_sigterm(groups, monkeypatch):
    monkeypatch.setattr(script, "STOP_GRACE_SECONDS", 0.2)
    pgid_file = groups()

    async def scenario():
        stream = AudioStream(LABEL)
        command = f"echo $$ > {pgid_file}; ( trap '' TERM; sleep 30 ) | cat"
        pump = asyncio.create_task(pump_script_speech(_request(command), stream))
        pgid = await _read_pgid(pgid_file)
        assert not _group_gone(pgid)  # the shell leads its own group
        await asyncio.sleep(0.1)  # the subshell sets its trap
        started = time.monotonic()
        pump.cancel()
        with pytest.raises(asyncio.CancelledError):
            await _within(pump, 1)
        await _until(lambda: _group_gone(pgid), 1 - (time.monotonic() - started))

    asyncio.run(scenario())


def test_a_forced_shell_is_reaped_by_asyncio(groups, monkeypatch, caplog):
    """The shell itself ignores SIGTERM: after its SIGKILL, asyncio's child watcher reads its
    exit status (no "exit status already read" warning)."""
    monkeypatch.setattr(script, "STOP_GRACE_SECONDS", 0.2)
    logger = logging.getLogger("asyncio")
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    caplog.set_level(logging.WARNING, logger="asyncio")
    stop_process_group = script._stop_process_group

    def wait_for_zombie(pid):
        """Block (the loop runs nothing) until *pid* exited, without reaping it."""
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                    return
            except ChildProcessError:
                return  # a threaded child watcher (macOS, Linux without pidfd) already reaped it
            time.sleep(0.01)

    async def stop_then_die(pgid):
        await stop_process_group(pgid)
        # The killed shell turns zombie before ``_close()`` runs, as on a busy machine.
        wait_for_zombie(pgid)

    monkeypatch.setattr(script, "_stop_process_group", stop_then_die)
    pgid_file = groups()

    async def scenario():
        stream = AudioStream(LABEL)
        command = f"echo $$ > {pgid_file}; trap '' TERM; sleep 30"
        pump = asyncio.create_task(pump_script_speech(_request(command), stream))
        pgid = await _read_pgid(pgid_file)
        await asyncio.sleep(0.1)  # the shell sets its trap
        pump.cancel()
        with pytest.raises(asyncio.CancelledError):
            await _within(pump, 1)
        await _until(lambda: _group_gone(pgid), 1)
        await asyncio.sleep(0.1)  # the child watcher reports the exit

    asyncio.run(scenario())
    assert [r.getMessage() for r in caplog.records if r.name == "asyncio" and r.levelno >= logging.WARNING] == []


def test_a_header_only_wav_fails_the_open(tmp_path):
    async def scenario():
        stream = await _pump(_request(f"cat {_file(tmp_path, 'empty.wav', _wav(b''))}", audio_format="wav"))
        assert await _failed_open(stream) == NO_AUDIO

    asyncio.run(scenario())


def test_the_size_cap_stops_the_command(monkeypatch, groups):
    monkeypatch.setattr(script, "SCRIPT_MAX_BYTES", 1000)
    pgid_file = groups()

    async def scenario():
        stream = await _pump(_request(f"echo $$ > {pgid_file}; yes"))
        assert (stream.state, stream.error) == ("failed", TOO_LARGE)
        assert len(stream.data) == 1000  # the output up to the cap plays
        assert _group_gone(int(pgid_file.read_text()))

    asyncio.run(scenario())


def test_the_cap_counts_dropped_output(monkeypatch, groups):
    monkeypatch.setattr(script, "SCRIPT_MAX_BYTES", 1000)
    pgid_file = groups()

    async def scenario():
        stream = await _pump(_request(f"echo $$ > {pgid_file}; yes", audio_format="mp3"))
        assert await _failed_open(stream) == NOT_MP3
        assert _group_gone(int(pgid_file.read_text()))

    asyncio.run(scenario())


def test_a_silent_audio_stream_is_stopped(tmp_path, monkeypatch, groups):
    monkeypatch.setattr(script, "STALL_SECONDS", 0.2)
    pgid_file = groups()

    async def scenario():
        command = f"echo $$ > {pgid_file}; cat {_file(tmp_path, 'a.pcm', PCM[:100])}; sleep 30"
        stream = await _pump(_request(command), seconds=3)
        assert (stream.state, stream.error) == ("failed", STALLED)
        assert bytes(stream.data) == PCM[:100]
        assert _group_gone(int(pgid_file.read_text()))

    asyncio.run(scenario())


def test_a_slow_start_is_not_a_stall(tmp_path, monkeypatch):
    monkeypatch.setattr(script, "STALL_SECONDS", 0.2)

    async def scenario():
        stream = await _pump(_request(f"sleep 0.5; cat {_file(tmp_path, 'a.pcm', PCM)}"))
        assert stream.state == "complete"
        assert bytes(stream.data) == PCM

    asyncio.run(scenario())


def test_a_whole_audio_written_at_the_end_is_not_a_stall(tmp_path, monkeypatch):
    monkeypatch.setattr(script, "STALL_SECONDS", 0.2)

    async def scenario():
        command = f"cat {_file(tmp_path, 'a.pcm', PCM[:100])}; sleep 0.5; cat {_file(tmp_path, 'b.pcm', PCM)}"
        stream = await _pump(_request(command, output="whole"))
        assert stream.state == "complete"
        assert bytes(stream.data) == PCM[:100] + PCM

    asyncio.run(scenario())


def test_a_file_path_is_read_after_the_exit(tmp_path):
    path = shlex.quote(str(tmp_path / "speech.mp3"))
    command = f"cp {_file(tmp_path, 'source.mp3', MP3)} {path}; echo {path}"

    async def scenario():
        stream = await _pump(_request(command, output="path", audio_format="mp3"))
        assert (stream.state, stream.content_type) == ("complete", "audio/mpeg")
        assert bytes(stream.data) == MP3  # a single run: no filter

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "stdout, message",
    [
        ("relative.mp3\n", NOT_A_PATH),
        ("/a\n/b\n", NOT_A_PATH),
        ("\n", NOT_A_PATH),
        ("/a\0b\n", NOT_A_PATH),
        ("/no/such/file\n", FILE_UNREADABLE),
        ("/\n", FILE_UNREADABLE),  # a directory
        ("/dev/null\n", FILE_UNREADABLE),  # not a regular file
    ],
)
def test_a_bad_path_fails(tmp_path, stdout, message):
    output = _file(tmp_path, "stdout", stdout.encode())

    async def scenario():
        stream = await _pump(_request(f"cat {output}", output="path", audio_format="mp3"))
        assert await _failed_open(stream) == message

    asyncio.run(scenario())


def test_a_path_that_is_not_utf8_fails(tmp_path):
    output = _file(tmp_path, "stdout", b"/a\xff\n")

    async def scenario():
        stream = await _pump(_request(f"cat {output}", output="path", audio_format="mp3"))
        assert await _failed_open(stream) == NOT_A_PATH

    asyncio.run(scenario())


def test_a_path_returned_twice_fails(tmp_path):
    path = _file(tmp_path, "a.mp3", MP3)

    async def scenario():
        stream = await _pump(_request(f"echo {path}", output="path", audio_format="mp3"), ["one", "two"])
        assert (stream.state, stream.error) == ("failed", PATH_REUSED)
        assert bytes(stream.data) == _id3(20) + MP3_FRAMES  # the first part plays

    asyncio.run(scenario())


def test_an_empty_returned_file_is_no_audio(tmp_path):
    path = _file(tmp_path, "empty.mp3", b"")

    async def scenario():
        stream = await _pump(_request(f"echo {path}", output="path", audio_format="mp3"))
        assert await _failed_open(stream) == NO_AUDIO

    asyncio.run(scenario())


def test_a_returned_file_above_the_cap_is_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(script, "SCRIPT_MAX_BYTES", 1000)
    path = _file(tmp_path, "big.pcm", PCM * 2)

    async def scenario():
        stream = await _pump(_request(f"echo {path}", output="path"))
        assert await _failed_open(stream) == TOO_LARGE

    asyncio.run(scenario())


def test_the_cap_is_per_speech_across_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(script, "SCRIPT_MAX_BYTES", 1500)
    path = _file(tmp_path, "a.pcm", PCM)

    async def scenario():
        stream = await _pump(_request(f"cat {path}"), ["one", "two"])
        assert (stream.state, stream.error) == ("failed", TOO_LARGE)
        assert bytes(stream.data) == PCM + PCM[:500]  # the second part up to the cap

    asyncio.run(scenario())


def test_an_endless_path_output_is_stopped(monkeypatch, groups):
    monkeypatch.setattr(script, "PATH_OUTPUT_MAX_BYTES", 100)
    pgid_file = groups()

    async def scenario():
        stream = await _pump(_request(f"echo $$ > {pgid_file}; yes /a", output="path", audio_format="mp3"))
        assert await _failed_open(stream) == NOT_A_PATH
        assert _group_gone(int(pgid_file.read_text()))

    asyncio.run(scenario())


def test_a_returned_file_is_deleted_after_a_successful_read(tmp_path):
    path = tmp_path / "speech.mp3"
    path.write_bytes(MP3)

    async def scenario():
        stream = await _pump(_request(f"echo {shlex.quote(str(path))}", output="path", audio_format="mp3"))
        assert (stream.state, bytes(stream.data)) == ("complete", MP3)

    asyncio.run(scenario())
    assert not path.exists()


def test_a_returned_file_is_kept_when_the_option_is_off(tmp_path):
    path = tmp_path / "speech.mp3"
    path.write_bytes(MP3)
    request = _request(f"echo {shlex.quote(str(path))}", output="path", audio_format="mp3")

    async def scenario():
        stream = await _pump(request, delete_file=False)
        assert (stream.state, bytes(stream.data)) == ("complete", MP3)

    asyncio.run(scenario())
    assert path.read_bytes() == MP3


def test_a_failed_run_keeps_its_file(tmp_path):
    path = tmp_path / "speech.mp3"
    path.write_bytes(b"not an mp3 file at all")

    async def scenario():
        stream = await _pump(_request(f"echo {shlex.quote(str(path))}", output="path", audio_format="mp3"))
        assert await _failed_open(stream) == NOT_MP3

    asyncio.run(scenario())
    assert path.read_bytes() == b"not an mp3 file at all"


def test_a_symbolic_link_is_never_deleted(tmp_path):
    target = tmp_path / "target.mp3"
    target.write_bytes(MP3)
    link = tmp_path / "link.mp3"
    link.symlink_to(target)

    async def scenario():
        stream = await _pump(_request(f"echo {shlex.quote(str(link))}", output="path", audio_format="mp3"))
        assert (stream.state, bytes(stream.data)) == ("complete", MP3)

    asyncio.run(scenario())
    assert link.is_symlink()
    assert target.read_bytes() == MP3


@pytest.mark.parametrize("error", [PermissionError, FileNotFoundError])
def test_a_failed_deletion_is_not_an_error(tmp_path, monkeypatch, error):
    path = tmp_path / "speech.mp3"
    path.write_bytes(MP3)
    unlinked = []

    def failing_unlink(target):
        unlinked.append(target)
        raise error(target)

    monkeypatch.setattr(script.os, "unlink", failing_unlink)

    async def scenario():
        stream = await _pump(_request(f"echo {shlex.quote(str(path))}", output="path", audio_format="mp3"))
        assert (stream.state, bytes(stream.data)) == ("complete", MP3)

    asyncio.run(scenario())
    assert unlinked == [str(path)]


def test_the_parts_run_one_after_the_other_into_one_stream(tmp_path):
    texts = tmp_path / "texts"
    command = f"cat >> {texts}; echo >> {texts}; cat {_file(tmp_path, 'a.mp3', MP3)}"
    parts = ["Part one.", "Part two.", "Part three."]

    async def scenario():
        stream = AudioStream(LABEL)
        waits = []

        async def wait_for_lookahead():
            waits.append(texts.read_text().count("\n"))  # the runs done when the wait starts

        stream.wait_for_lookahead = wait_for_lookahead
        await _within(pump_script_speech(_request(command, audio_format="mp3"), stream, parts, paced=True))
        assert (stream.state, stream.content_type, stream.parts_sent) == ("complete", "audio/mpeg", 3)
        assert stream.paced
        assert waits == [1, 2]
        # The first part keeps its ID3 tag; every part loses its info frame, the later ones their tag.
        assert bytes(stream.data) == _id3(20) + MP3_FRAMES * 3

    asyncio.run(scenario())
    assert texts.read_text() == "Part one.\nPart two.\nPart three.\n"


def test_a_split_pcm_speech_drops_partial_frames(tmp_path):
    path = _file(tmp_path, "odd.pcm", PCM[:101])

    async def scenario():
        stream = await _pump(_request(f"cat {path}"), ["one", "two"])
        assert stream.state == "complete"
        assert bytes(stream.data) == PCM[:100] * 2

    asyncio.run(scenario())


def test_a_later_part_of_another_type_fails(tmp_path):
    _file(tmp_path, "one.wav", _wav(PCM, rate=8000))
    _file(tmp_path, "two.wav", _wav(PCM, rate=16000))
    command = f'read -r name; cat {shlex.quote(str(tmp_path))}/"$name".wav'

    async def scenario():
        stream = await _pump(_request(command, audio_format="wav"), ["one", "two"])
        assert (stream.state, stream.error) == ("failed", PART_TYPE_CHANGED)
        assert (stream.content_type, bytes(stream.data)) == (PCM_8000, PCM)

    asyncio.run(scenario())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_ffmpeg_streamed_wav_is_parsed():
    # -nostdin: ffmpeg would read the speech text as keyboard input.
    command = "ffmpeg -nostdin -loglevel error -f lavfi -i sine=frequency=440:duration=1 -ac 1 -ar 8000 -f wav -"

    async def scenario():
        stream = await _pump(_request(command, audio_format="wav"), seconds=10)
        assert (stream.state, stream.content_type) == ("complete", PCM_8000)
        assert len(stream.data) == 16000

    asyncio.run(scenario())
