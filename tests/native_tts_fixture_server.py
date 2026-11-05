"""Deterministic owned-audio fixture server. No provider or TwiCC data directory is used.

Run with an explicit test TLS certificate and key:
    uv run python tests/native_tts_fixture_server.py --cert /tmp/test.pem --key /tmp/test.key

GET /api/tts/native/session/audio/{pcm,wav,mp3}/ streams three delayed fragments.
GET .../ending/ returns the configured ending. DELETE releases that resource.
The fixture secret is ``fixture-secret``. It is never a production credential.
"""

import argparse
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import orjson

from twicc.tts.script_audio import OutputCheck

FIXTURES = Path(__file__).parent / "fixtures" / "native_tts_audio"


def normalized_audio(kind: str) -> tuple[str, bytes]:
    """Use the real script normalizer, including incremental WAV header stripping."""
    check = OutputCheck(kind, 16000 if kind == "pcm" else None, 1)
    raw = (FIXTURES / f"three-tones.{kind}").read_bytes()
    chunks = [check.feed(raw[offset : offset + 797]) for offset in range(0, len(raw), 797)]
    chunks.append(check.end())
    assert check.content_type is not None
    return check.content_type, b"".join(chunks)


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ending = "complete"
    delay = 1.7

    def log_message(self, format, *args):
        pass

    def do_DELETE(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        if self.headers.get("X-TwiCC-TTS-Session") != "fixture-secret":
            self.send_error(403)
            return
        if self.headers.get("Range"):
            self.send_error(416)
            return
        if self.path.endswith("/ending/"):
            body = orjson.dumps({"state": self.ending, "error": "Fixture generation failed" if self.ending == "failed" else None})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        kind = self.path.rstrip("/").split("/")[-1]
        if kind not in {"pcm", "wav", "mp3"}:
            self.send_error(404)
            return
        content_type, body = normalized_audio(kind)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        step = (len(body) + 2) // 3
        try:
            for offset in range(0, len(body), step):
                if offset:
                    time.sleep(self.delay)
                fragment = body[offset : offset + step]
                self.wfile.write(f"{len(fragment):x}\r\n".encode() + fragment + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--port", type=int, default=38443)
    parser.add_argument("--ending", choices=["complete", "running", "failed", "unknown"], default="complete")
    args = parser.parse_args()
    FixtureHandler.ending = args.ending
    server = ThreadingHTTPServer(("127.0.0.1", args.port), FixtureHandler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(args.cert, args.key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
