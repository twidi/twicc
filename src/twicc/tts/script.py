"""The runner of the text-to-speech script configs: the user's command as the speech pump.

A script config stores a shell command. Each part of the text runs it once, in the user's shell
(a non-interactive login shell, in the home directory, with the environment of TwiCC's
terminals): the text goes to its standard input, and its audio comes on its standard output,
as a stream, as a whole, or as the path of a file. :func:`pump_script_speech` writes that audio
into an :class:`~twicc.tts.streams.AudioStream`, as :func:`~twicc.tts.openrouter.pump_speech`
does for OpenRouter.

The run ends at the command's exit, never at the end of its output: a process it leaves in the
background can hold the output open for its whole life. A stop (a release, a shutdown, a reason
TwiCC finds itself) ends the whole process group of the command.

The command line and the stderr text never reach a log line (either can hold a key): a failure
logs the exit status, or TwiCC's own message.
"""

import asyncio
import logging
import os
import signal
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from twicc.terminal import terminal_environment
from twicc.tts.openrouter import PCM_SAMPLE_WIDTH, SpeechError, parse_pcm_format
from twicc.tts.part_audio import Mp3HeadFilter, PcmFrameTrimmer
from twicc.tts.parts import FIRST_PART_CHARS, split_text
from twicc.tts.script_audio import OutputCheck

if TYPE_CHECKING:
    from twicc.tts.streams import AudioStream

logger = logging.getLogger(__name__)

# The audio cap of one speech, across its parts, for the standard output and a returned file.
SCRIPT_MAX_BYTES = 512 * 1024 * 1024
# An audio stream run that gives no new byte for this long, after its first audio byte, is stopped.
STALL_SECONDS = 60.0
# A stop: SIGTERM to the process group, then SIGKILL when it still exists after this delay.
STOP_GRACE_SECONDS = 2.0
# After the exit, the output is still read until its end, or this long at most.
EXIT_DRAIN_SECONDS = 0.5
# How often the exit of the command (and the end of a stopped group) is checked.
EXIT_POLL_SECONDS = 0.05
STDERR_TAIL_BYTES = 8192
ERROR_LINE_CHARS = 300
PATH_OUTPUT_MAX_BYTES = 64 * 1024
READ_CHUNK = 64 * 1024

COULD_NOT_START = "The script could not start"
SCRIPT_FAILED = "The script failed (exit status {status})"
SCRIPT_KILLED = "The script was stopped by signal {signal}"
STALLED = "The script stopped sending audio"
TOO_LARGE = "The script audio is too large"
NOT_A_PATH = "The script output is not one absolute path"
PATH_REUSED = "The script returned the same path twice"
FILE_UNREADABLE = "The script file cannot be read"


class ScriptSpeechRequest(NamedTuple):
    """The speech fields of a script config, with the text (hashable: a cache key)."""

    text: str
    command: str
    output: str  # "stream", "whole" or "path"
    audio_format: str  # "mp3", "wav" or "pcm"
    pcm_rate: int | None
    pcm_channels: int
    max_chars: int | None


def script_parts(request: ScriptSpeechRequest) -> list[str]:
    """The texts of the runs: the whole text without a max characters per run, else its parts
    (full parts for an audio stream, growing parts for the other kinds, so the audio starts fast)."""
    if request.max_chars is None:
        return [request.text]
    first = request.max_chars if request.output == "stream" else min(request.max_chars, FIRST_PART_CHARS)
    return split_text(request.text, request.max_chars, first)


def shell_argv(command: str) -> list[str]:
    """The user's shell, as a non-interactive login shell, running *command*."""
    shell = os.environ.get("SHELL") or "/bin/sh"
    return [shell, "-l", "-c", command]


async def pump_script_speech(
    request: ScriptSpeechRequest,
    stream: "AudioStream",
    parts: Sequence[str] | None = None,
    *,
    paced: bool = False,
) -> None:
    """Run the command of *request* once per text part, and stream its audio into *stream*.

    *parts* are the texts of the runs, in order, into the same stream (None: the request's text,
    in one run). A run starts once the previous one ended and the stream's lookahead allows it.
    With *paced*, the stream delivers its audio at the playback pace.

    A failed run fails the stream (before its headers on the first part) and starts no next run.
    A later part of another type fails it too. A cancellation (a release, a shutdown) stops the
    running command, then propagates.
    """
    texts = [request.text] if parts is None else list(parts)
    split = len(texts) > 1
    paths: set[str] = set()  # the file paths returned in this speech
    for index, text in enumerate(texts):
        if index:
            await stream.wait_for_lookahead()
        run = _ScriptRun(request, stream, index, split=split, paced=paced, paths=paths)
        if not await run.run(text):
            return
    stream.finish()


def _signal_group(pgid: int, signal_number: int) -> bool:
    """Send *signal_number* (0: a probe) to the process group. False when the group is gone."""
    try:
        os.killpg(pgid, signal_number)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Nothing left in the group that TwiCC may signal (macOS answers so for zombies).
        return False
    return True


async def _stop_process_group(pgid: int) -> None:
    """SIGTERM to the group, then SIGKILL when it still exists after ``STOP_GRACE_SECONDS``.

    Awaited, never scheduled with ``call_later``: at shutdown the loop closes, and a scheduled
    signal would never fire. A cancellation during the grace wait sends SIGKILL at once.
    """
    if not _signal_group(pgid, signal.SIGTERM):
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + STOP_GRACE_SECONDS
    try:
        while loop.time() < deadline:
            await asyncio.sleep(EXIT_POLL_SECONDS)
            if not _signal_group(pgid, 0):
                return
    except asyncio.CancelledError:
        _signal_group(pgid, signal.SIGKILL)
        raise
    # The shell can be gone while a child ignores SIGTERM: the group still exists.
    _signal_group(pgid, signal.SIGKILL)


def _read_returned_file(path: str, limit: int) -> bytes:
    """The bytes of the regular file at *path*, at most *limit* (runs in a thread).

    Opened non-blocking, then checked on its descriptor: a FIFO never blocks the read.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        raise SpeechError(FILE_UNREADABLE) from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SpeechError(FILE_UNREADABLE)
        if info.st_size > limit:
            raise SpeechError(TOO_LARGE)
        with os.fdopen(descriptor, "rb", closefd=False) as file:
            data = file.read(limit + 1)
    except OSError:
        raise SpeechError(FILE_UNREADABLE) from None
    finally:
        os.close(descriptor)
    if len(data) > limit:  # the file grew after its size was read
        raise SpeechError(TOO_LARGE)
    return data


def _last_error_line(tail: bytes) -> str | None:
    """The last non-empty line of the stderr tail, stripped and cut, or None."""
    for line in reversed(tail.decode("utf-8", errors="replace").splitlines()):
        line = line.strip()
        if line:
            if len(line) > ERROR_LINE_CHARS:
                line = line[: ERROR_LINE_CHARS - 1] + "…"
            return line
    return None


class _ScriptRun:
    """One run of the command, for one part of the text.

    The run reads the standard output while it polls the command's exit (never ``wait()``: it
    returns only once every pipe is closed). After the exit, it reads what is left for
    ``EXIT_DRAIN_SECONDS`` at most, then closes the pipes: a process left in the background never
    extends the run.
    """

    def __init__(
        self,
        request: ScriptSpeechRequest,
        stream: "AudioStream",
        index: int,
        *,
        split: bool,
        paced: bool,
        paths: set[str],
    ):
        self._request = request
        self._stream = stream
        self._index = index
        self._split = split
        self._paced = paced
        self._paths = paths
        self._check = OutputCheck(request.audio_format, request.pcm_rate, request.pcm_channels)
        self._kept_before = len(stream.data)  # the bytes of the previous parts, for the cap
        self._read = 0  # the stdout bytes of this run: appended, held or dropped
        self._path_output = bytearray() if request.output == "path" else None
        # A format mismatch: the output is drained and dropped until the exit decides.
        self._format_error: SpeechError | None = None
        # A reason TwiCC found itself to end the run (the cap, a stall, an endless path output).
        self._stop_error: SpeechError | None = None
        self._stream_failed = False  # the stream failed itself: a later part of another type
        self._headers_sent = False
        self._head_filter: Mp3HeadFilter | None = None
        self._trimmer: PcmFrameTrimmer | None = None
        self._gave_audio = False
        self._last_read_at = 0.0
        self._stderr_tail = bytearray()
        self._proc: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task | None = None
        self._side_tasks: list[asyncio.Task] = []  # the stdin writer and the stderr reader
        self._stopped = False
        self._closed = False

    async def run(self, text: str) -> bool:
        """Run the command on *text*. False when the run failed the stream."""
        self._stream.begin_part()
        start = asyncio.ensure_future(
            asyncio.create_subprocess_exec(
                *shell_argv(self._request.command),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=Path.home(),
                env=terminal_environment(os.environ),
                start_new_session=True,  # its own process group: a stop reaches every process
            )
        )
        try:
            # Shielded: a cancelled start lets asyncio kill the shell only, not its children.
            self._proc = await asyncio.shield(start)
        except asyncio.CancelledError:
            await self._stop_cancelled_start(start)
            raise
        except (OSError, ValueError):  # ValueError: a NUL byte in the command
            self._stream.fail(SpeechError(COULD_NOT_START))
            return False
        try:
            return await self._follow(text)
        finally:
            try:
                # A process that exited is never signalled: what it left running stays.
                if self._proc.returncode is None and not self._stopped:
                    await self._stop()
            finally:
                await self._close()

    async def _stop_cancelled_start(self, start: asyncio.Future) -> None:
        """A cancellation came while the command started: once it runs, stop its whole group.

        The start stays shielded until it ends: a further cancellation (a release, then a
        shutdown) would let asyncio kill the shell alone. The caller re-raises the cancellation.
        """
        while not start.done():
            try:
                await asyncio.shield(start)
            except (asyncio.CancelledError, Exception):
                pass  # a further cancellation, or a failed start: read below
        if start.cancelled() or start.exception() is not None:
            return  # it never started
        self._proc = start.result()
        try:
            # A command that exited while it started is never signalled, as in ``run()``.
            if self._proc.returncode is None:
                await self._stop()
        finally:
            await self._close()

    async def _follow(self, text: str) -> bool:
        proc = self._proc
        loop = asyncio.get_running_loop()
        self._side_tasks = [
            asyncio.create_task(self._write_stdin(text.encode())),
            asyncio.create_task(self._read_stderr()),
        ]
        self._last_read_at = loop.time()
        stdout_open = True
        while proc.returncode is None:
            if stdout_open:
                chunk = await self._read_stdout(EXIT_POLL_SECONDS)
                if chunk == b"":
                    stdout_open = False  # the command closed its output, but still runs
                elif chunk is not None:
                    self._take(chunk)
            else:
                await asyncio.sleep(EXIT_POLL_SECONDS)
            # The command can exit in this same pass: an exited command is never signalled.
            running = proc.returncode is None
            if running and self._stop_error is None and self._stalled(loop.time()):
                self._stop_error = SpeechError(STALLED)
            if self._stream_failed:
                if running:
                    await self._stop()
                return False
            if self._stop_error is not None:
                if not running:
                    break  # the exit decides, then this message
                await self._stop()
                self._stream.fail(self._stop_error)
                return False

        # The command exited: what is left in the pipes, one deadline for both.
        deadline = loop.time() + EXIT_DRAIN_SECONDS
        while stdout_open and self._stop_error is None and not self._stream_failed:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            chunk = await self._read_stdout(remaining)
            if chunk is None:
                break
            if chunk:
                self._take(chunk)
            else:
                stdout_open = False
        if self._stream_failed:
            return False
        stderr_task = self._side_tasks[1]
        remaining = deadline - loop.time()
        if not stderr_task.done() and remaining > 0:
            await asyncio.wait({stderr_task}, timeout=remaining)
        await self._close()
        return await self._conclude()

    async def _conclude(self) -> bool:
        """After the exit: the exit status decides first, then the output checks."""
        status = self._proc.returncode
        if status != 0:
            if status < 0:
                fallback = SCRIPT_KILLED.format(signal=-status)
            else:
                fallback = SCRIPT_FAILED.format(status=status)
            message = _last_error_line(bytes(self._stderr_tail)) or fallback
            self._stream.fail(SpeechError(message, log_message=f"script exit status {status}"))
            return False
        error = self._stop_error or self._format_error
        if error is not None:
            self._stream.fail(error)
            return False
        try:
            if self._path_output is not None:
                data = await self._returned_file()
                for start in range(0, len(data), READ_CHUNK):
                    if not self._give(self._check.feed(data[start : start + READ_CHUNK])):
                        return False
            if not self._give(self._check.end()):
                return False
        except SpeechError as error:
            self._stream.fail(error)
            return False
        if self._head_filter is not None:
            self._stream.append(self._head_filter.flush())
        elif self._trimmer is not None and (dropped := self._trimmer.end()):
            logger.debug("TTS script part %d: %d bytes of a partial PCM frame dropped", self._index + 1, dropped)
        self._stream.end_part()
        return True

    async def _returned_file(self) -> bytes:
        """The bytes of the file whose path the command printed. Raises :class:`SpeechError`."""
        try:
            path = bytes(self._path_output).decode("utf-8")
        except UnicodeDecodeError:
            raise SpeechError(NOT_A_PATH) from None
        path = path.removesuffix("\n")
        if not path or "\n" in path or "\0" in path or not os.path.isabs(path):
            raise SpeechError(NOT_A_PATH)
        if path in self._paths:
            raise SpeechError(PATH_REUSED)
        self._paths.add(path)
        # Read off the event loop: a file can be large, or on a slow disk.
        return await asyncio.to_thread(_read_returned_file, path, SCRIPT_MAX_BYTES - self._kept_before)

    def _take(self, chunk: bytes) -> None:
        """One stdout chunk: counted, then checked, filtered and appended (or dropped). The bytes
        past the cap are never used: they stop the run."""
        room = SCRIPT_MAX_BYTES - self._kept_before - self._read
        self._read += len(chunk)
        self._last_read_at = asyncio.get_running_loop().time()
        over_cap = len(chunk) > room
        if over_cap:
            chunk = chunk[: max(room, 0)]
        self._use(chunk)
        if over_cap and self._stop_error is None and not self._stream_failed:
            # A pending format message is the real cause: it wins.
            self._stop_error = self._format_error or SpeechError(TOO_LARGE)

    def _use(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self._path_output is not None:
            self._path_output += chunk
            if len(self._path_output) > PATH_OUTPUT_MAX_BYTES:
                self._stop_error = SpeechError(NOT_A_PATH)
            return
        if self._format_error is not None:
            return  # dropped: the exit decides
        try:
            audio = self._check.feed(chunk)
        except SpeechError as error:
            self._format_error = error
            return
        if not self._give(audio):
            self._stream_failed = True

    def _give(self, audio: bytes) -> bool:
        """Append checked audio bytes; the headers go out with the first ones. False when a later
        part of another type failed the stream."""
        if not audio:
            return True
        if not self._headers_sent:
            content_type = self._check.content_type
            if self._index == 0:
                self._stream.set_headers(content_type, None)
                if self._paced:
                    self._stream.pace()
            elif not self._stream.add_part_headers(content_type, None):
                return False
            self._headers_sent = True
            if self._split:
                self._make_filter(content_type)
        self._gave_audio = True
        if self._head_filter is not None:
            audio = self._head_filter.feed(audio)
        elif self._trimmer is not None:
            audio = self._trimmer.feed(audio)
        self._stream.append(audio)
        return True

    def _make_filter(self, content_type: str) -> None:
        """The filter of a part of a split speech, as for OpenRouter parts."""
        media_type = content_type.split(";", 1)[0]
        if media_type == "audio/mpeg":
            self._head_filter = Mp3HeadFilter(drop_id3=self._index > 0)
        elif media_type == "audio/pcm":
            _rate, channels = parse_pcm_format(content_type)
            self._trimmer = PcmFrameTrimmer(channels * PCM_SAMPLE_WIDTH)

    def _stalled(self, now: float) -> bool:
        """An audio stream run silent for ``STALL_SECONDS`` after its first audio byte."""
        return self._request.output == "stream" and self._gave_audio and now - self._last_read_at > STALL_SECONDS

    async def _stop(self) -> None:
        self._stopped = True
        await _stop_process_group(self._proc.pid)  # its shell leads the group: pgid == pid

    async def _read_stdout(self, timeout: float) -> bytes | None:
        """The next stdout chunk (``b""`` at its end), or None when none came within *timeout*.
        A read not done yet stays pending for the next call."""
        if self._read_task is None:
            self._read_task = asyncio.create_task(self._proc.stdout.read(READ_CHUNK))
        done, _pending = await asyncio.wait({self._read_task}, timeout=timeout)
        if not done:
            return None
        task, self._read_task = self._read_task, None
        return task.result()

    async def _write_stdin(self, data: bytes) -> None:
        stdin = self._proc.stdin
        try:
            stdin.write(data)
            await stdin.drain()
            stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass  # a command that exits without reading its input is not an error

    async def _read_stderr(self) -> None:
        stderr = self._proc.stderr
        while chunk := await stderr.read(READ_CHUNK):
            self._stderr_tail += chunk
            del self._stderr_tail[:-STDERR_TAIL_BYTES]

    async def _close(self) -> None:
        """Cancel the readers and the writer, close the pipes, and let their close callbacks run
        (no descriptor leaks). Idempotent."""
        if self._closed:
            return
        self._closed = True
        for task in (self._read_task, *self._side_tasks):
            if task is not None:
                task.cancel()
        # The only close asyncio offers for the pipes; it does not signal an exited process.
        self._proc._transport.close()
        await asyncio.sleep(0)
