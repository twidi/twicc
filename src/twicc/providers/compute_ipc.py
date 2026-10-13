"""Per-run compute streams keep partial IPC frames off the asyncio thread."""

from __future__ import annotations

import asyncio
import multiprocessing
import queue
import threading
from contextlib import suppress

import orjson


class ComputeStopFlag:
    """An advisory byte avoids locks that a terminated worker can retain.

    Only the parent writes. The worker checks between commands. Completion
    and shutdown never depend on visibility of this flag: process joins and
    terminate/kill remain authoritative.
    """

    def __init__(self, context):
        self.value = context.RawValue('b', 0)

    def set(self):
        self.value.value = 1

    def is_set(self):
        return bool(self.value.value)


class ComputeResultSender:
    """Preserve the compute factory's .put(bytes) contract without a feeder."""

    def __init__(self, connection):
        self.connection = connection

    def put(self, message):
        self.connection.send_bytes(message)

    def close(self):
        self.connection.close()


class ComputeStatusSender:
    """Share the result stream; worker calls are sequential, so no lock is needed."""

    def __init__(self, sender):
        self.sender = sender

    def put(self, message):
        self.sender.put(orjson.dumps({'_compute_status': message}))


class ComputeResultStream:
    """Receive and decode complete frames on one owned thread per worker run.

    Queues bound message count, not bytes. A legitimate session result can
    be arbitrarily large. JSON decoding can hold the GIL; this transport
    removes indefinite IPC waits, not all CPU latency.
    """

    def __init__(self, ingress, status, provider, run_id, failed):
        reader, writer = multiprocessing.get_context('spawn').Pipe(duplex=False)
        self.reader = reader
        self.sender = ComputeResultSender(writer)
        self.ingress = ingress
        self.status = status
        self.provider = provider
        self.run_id = run_id
        self.failed = failed
        self.loop = asyncio.get_running_loop()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._receive, name=f'compute-receiver-{run_id}', daemon=True)
        self.started = False

    def start(self):
        """Call after successful spawn; only the child may retain the writer."""
        self.sender.close()
        self.thread.start()
        self.started = True

    def stop_admission(self):
        self.stopped.set()

    def _put(self, target, message):
        while not self.stopped.is_set():
            try:
                target.put(message, timeout=.05)
                return True
            except queue.Full:
                pass
        return False

    def _receive(self):
        done = False
        try:
            while not self.stopped.is_set():
                if not self.reader.poll(.1):
                    continue
                # poll sees the header, not a complete frame. recv_bytes may
                # wait here until EOF. This owned thread never runs on asyncio.
                message = orjson.loads(self.reader.recv_bytes())
                if not isinstance(message, dict):
                    raise TypeError('Compute frame must contain an object')
                if '_compute_status' in message:
                    if self.status is None or not isinstance(message['_compute_status'], dict):
                        raise ValueError('Unexpected compute status frame')
                    if not self._put(self.status, message['_compute_status']):
                        break
                    continue
                if message.get('provider') != self.provider or message.get('run_id') != self.run_id:
                    raise ValueError('Compute frame does not match its provider and run')
                if not self._put(self.ingress, message):
                    break
                if message.get('type') == 'done':
                    done = True
                    break
        except (EOFError, OSError, TypeError, ValueError) as error:
            if not done and not self.stopped.is_set():
                with suppress(RuntimeError):
                    self.loop.call_soon_threadsafe(self.failed, f'compute transport interrupted: {error}')
        finally:
            self.reader.close()

    async def close(self):
        """Call after worker exit; bounded joins also cover failed spawn."""
        self.stop_admission()
        self.sender.close()
        if self.started:
            await asyncio.to_thread(self.thread.join, 1)
            if self.thread.is_alive():
                raise RuntimeError(f'Compute receiver {self.run_id} did not stop after worker exit')
        else:
            self.reader.close()
