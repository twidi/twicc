#!/usr/bin/env python3
"""Check production startup with isolated IndexedDB fixtures.

Build into a scratch directory with `vite build --outDir <absolute-path>`.
Run with `uvx --with playwright python scripts/diagnose_frontend_startup.py
--build-dir <absolute-path> --browser <chromium-executable>`.

All API and WebSocket traffic stays in the test. No TwiCC backend runs.
Each case uses a new browser context. No user profile or drafts are read.
"""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError, sync_playwright


SEED = """async (kind) => {
    const request = indexedDB.open('twicc', 11);
    request.onupgradeneeded = () => {
        const db = request.result;
        for (const name of ['draftMessages', 'draftSessions', 'ephemeralControls',
            'inflightSends', 'asyncQuestionDrafts']) db.createObjectStore(name);
        for (const name of ['draftMedias', 'draftAttachments']) {
            const store = db.createObjectStore(name, { keyPath: 'id' });
            store.createIndex('sessionId', 'sessionId');
        }
        db.createObjectStore('codeComments', {
            keyPath: ['projectId', 'sessionId', 'filePath', 'source', 'sourceRef', 'lineNumber']
        });
        db.createObjectStore('pendingRequestDrafts', { keyPath: ['sessionId', 'requestId'] });
    };
    const db = await new Promise((resolve, reject) => {
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
    if (kind !== 'empty') {
        const tx = db.transaction(['draftMessages', 'draftMedias', 'draftAttachments'], 'readwrite');
        tx.objectStore('draftMessages').put({ message: 'First preserved draft',
            mediaIds: ['fixture-media'] }, 'fixture-session');
        tx.objectStore('draftMessages').put({ message: 'Second preserved draft' }, 'fixture-second');
        if (kind === 'legacy') tx.objectStore('draftMedias').put({
            id: 'fixture-media', sessionId: 'fixture-session', name: 'fixture.txt',
            type: 'txt', mimeType: 'text/plain', data: 'Preserved attachment content', createdAt: 1
        });
        if (kind === 'modern') tx.objectStore('draftAttachments').put({
            id: 'fixture-media', sessionId: 'fixture-session', bucket: 'fixture-session',
            position: 0, name: 'fixture.txt', size: 28, mimeType: 'text/plain', kind: 'text'
        });
        await new Promise((resolve, reject) => {
            tx.oncomplete = resolve;
            tx.onerror = () => reject(tx.error);
        });
    }
    db.close();
}"""

SNAPSHOT = """async () => {
    const request = indexedDB.open('twicc', 11);
    const db = await new Promise((resolve, reject) => {
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
    const result = {};
    await Promise.all(Array.from(db.objectStoreNames, name => new Promise((resolve, reject) => {
        const request = db.transaction(name).objectStore(name).getAll();
        request.onsuccess = () => { result[name] = request.result; resolve(); };
        request.onerror = () => reject(request.error);
    })));
    db.close();
    return result;
}"""


class BuildHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/__seed__":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<!doctype html><title>Isolated startup fixture</title>")
            return
        self.path = path.removeprefix("/static") if path.startswith("/static/") else "/index.html"
        super().do_GET()

    def log_message(self, *_args):
        pass


def check_case(browser, origin, kind, mode, timeout, screenshot_dir):
    context = browser.new_context()
    calls = []
    errors = []
    bootstrap = {
        "settings": {},
        "settings_version": 1,
        "default_settings": {},
        "dev_mode": False,
        "uvx_mode": False,
        "twicc_launch_prefix": "twicc",
        "update_instructions": "",
        "claudeHybridEnabled": False,
        "workspaces": [],
        "terminal_config": {},
        "message_snippets": {},
        "seen_tips": {},
        "tips_manifest": {},
        "tips_default_enabled": False,
        "seen_help": {},
        "help_manifest": {},
        "providers": {},
        "disabledProvidersPresent": False,
        "disabledProviders": [],
        "providerStates": {},
    }

    def api(route):
        path = urlsplit(route.request.url).path
        calls.append(path)
        if path == "/api/auth/check/":
            route.fulfill(
                json={
                    "authenticated": mode != "login",
                    "password_required": mode == "login",
                    "access_denied": "remote" if mode == "denied" else None,
                }
            )
        elif path == "/api/bootstrap/":
            route.fulfill(status=503 if mode == "bootstrap-failure" else 200, json=bootstrap)
        elif path == "/api/composer-attachments/status/":
            # Another tab owns the upload: preserve the legacy payload and
            # exercise migration without starting a real upload.
            refs = route.request.post_data_json["refs"]
            route.fulfill(
                json={"statuses": [{**ref, "state": "uploading", "client_id": "other-tab:fixture"} for ref in refs]}
            )
        elif path == "/api/composer-attachments/touch/":
            route.fulfill(json={"ok": True})
        elif route.request.method == "HEAD" and path.startswith("/api/sessions/"):
            route.fulfill(status=200)
        elif path == "/api/home/":
            route.fulfill(json={"projects": [], "global_weekly_activity": []})
        elif path == "/api/projects/":
            route.fulfill(json=[])
        elif path == "/api/uploads/":
            route.fulfill(json={"uploads": []})
        elif path == "/api/sessions/":
            route.fulfill(json={"sessions": [], "has_more": False})
        elif path == "/api/artifact-bookmarks/":
            route.fulfill(json={"bookmarks": []})
        elif path == "/api/mcp/":
            route.fulfill(json={"connections": [], "requests": [], "config": {}, "protection": {}})
        else:
            raise AssertionError(f"Unexpected API request: {route.request.method} {path}")

    context.route("**/api/**", api)
    context.route_web_socket("**/ws/**", lambda _socket: None)

    def asset(route):
        if route.request.url.startswith(origin + "/"):
            route.fallback()
        elif urlsplit(route.request.url).hostname == "ka-f.fontawesome.com":
            # Icons do not participate in startup. Keep the test offline.
            route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg"/>')
        else:
            errors.append(f"Unexpected external request: {route.request.url}")
            route.abort()

    context.route("**/*", asset)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    try:
        page.goto(origin + "/__seed__")
        page.evaluate(SEED, kind)
        before = page.evaluate(SNAPSHOT)
        page.goto(origin + "/", wait_until="load")
        mounted = True
        try:
            if mode in ("denied", "bootstrap-failure"):
                text = "Access blocked" if mode == "denied" else "Backend unreachable"
                page.get_by_text(text, exact=True).wait_for(timeout=timeout)
            else:
                page.wait_for_function("!!document.querySelector('#app')?.__vue_app__", timeout=timeout)
                page.wait_for_function("document.querySelector('#app').innerText.trim().length > 0", timeout=timeout)
                if kind != "empty" and mode == "authenticated":
                    page.wait_for_function(
                        """() => {
                        const app = document.querySelector('#app').__vue_app__;
                        const data = app.config.globalProperties.$pinia._s.get('data');
                        return data.localState.attachmentRecords['fixture-session']?.['fixture-media']
                            && data.localState.draftMessages['fixture-session']?.message === 'First preserved draft'
                            && data.localState.draftMessages['fixture-second']?.message === 'Second preserved draft';
                    }""",
                        timeout=timeout,
                    )
        except TimeoutError:
            mounted = False
        after = page.evaluate(SNAPSHOT)
        assert after["draftMessages"] == before["draftMessages"], "Draft messages changed"
        assert after["draftMedias"] == before["draftMedias"], "Legacy payload changed"
        if kind != "empty" and mode == "authenticated" and mounted:
            assert any(record["id"] == "fixture-media" for record in after["draftAttachments"])
        expected_error = "Access blocked" if mode == "denied" else "Backend unreachable"
        unexpected = [
            error
            for error in set(errors)
            if not (mode in ("denied", "bootstrap-failure") and expected_error in error)
            and not (mode == "bootstrap-failure" and "503 (Service Unavailable)" in error)
        ]
        assert not unexpected, f"Unexpected JavaScript errors: {unexpected}"
        label = f"{mode}-{kind}"
        print(
            f"{label}: {'PASS' if mounted else 'FAIL (startup timeout)'}; "
            f"app children={page.locator('#app > *').count()}; errors={errors}; "
            f"initial stores={ {key: len(value) for key, value in before.items()} }; API={calls}",
            flush=True,
        )
        if screenshot_dir:
            page.screenshot(path=str(screenshot_dir / f"{label}.png"))
        return mounted
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--browser", help="Existing Chromium executable; otherwise use Playwright default")
    parser.add_argument("--timeout", type=int, default=10000, help="Startup timeout in milliseconds")
    parser.add_argument("--screenshot-dir", type=Path)
    args = parser.parse_args()
    if not (args.build_dir / "index.html").is_file():
        parser.error("--build-dir must contain a Vite production build")
    if args.screenshot_dir:
        args.screenshot_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(BuildHandler, directory=str(args.build_dir)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.browser, headless=True, args=["--no-sandbox"])
            print(f"Browser: Chromium {browser.version}", flush=True)
            try:
                outcomes = [
                    check_case(browser, origin, kind, mode, args.timeout, args.screenshot_dir)
                    for kind, mode in [
                        ("empty", "authenticated"),
                        ("legacy", "authenticated"),
                        ("modern", "authenticated"),
                        ("legacy", "login"),
                        ("empty", "denied"),
                        ("empty", "bootstrap-failure"),
                    ]
                ]
            finally:
                browser.close()
        return 0 if all(outcomes) else 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    raise SystemExit(main())
