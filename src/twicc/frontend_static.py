"""Keep BlackNoise's file index in sync with frontend builds."""

import asyncio
import threading
from pathlib import Path

from blacknoise import BlackNoise
from starlette.responses import PlainTextResponse


class FrontendStaticFiles:
    def __init__(self, application, directory):
        self.application = application
        self.directory = Path(directory).resolve()
        self._lock = threading.Lock()
        self._server = None
        self._build = None
        self._refresh()

    def _stamp(self):
        try:
            info = (self.directory / "index.html").stat()
            return info.st_ino, info.st_mtime_ns, info.st_size
        except FileNotFoundError:
            return None

    def _refresh(self):
        with self._lock:
            stamp = self._stamp()
            if self._server is None or stamp != self._build:
                server = BlackNoise(
                    self.application,
                    immutable_file_test=lambda path: path.startswith("/static/assets/"),
                )
                server.add(self.directory, "/static")
                # Publish a complete index. Concurrent requests can use the old snapshot.
                self._server = server
                self._build = stamp

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "").removeprefix(scope.get("root_path", ""))
        if scope["type"] != "http" or not path.startswith("/static/"):
            return await self.application(scope, receive, send)
        if self._stamp() != self._build:
            await asyncio.to_thread(self._refresh)
        target = (self.directory / path.removeprefix("/static/")).resolve()
        if not target.is_relative_to(self.directory) or not target.is_file():
            response = PlainTextResponse("Not Found", status_code=404, headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)

        async def no_cached_errors(message):
            if message["type"] == "http.response.start" and message["status"] >= 400:
                message = {**message, "headers": [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ] + [(b"cache-control", b"no-store")]}
            await send(message)

        return await self._server(scope, receive, no_cached_errors)
