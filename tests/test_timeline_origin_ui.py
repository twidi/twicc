"""The web UI sets the origin: WebSocket frames and REST requests (design sections 5, 6.1)."""

import asyncio

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory

from twicc import asgi
from twicc.asgi import WSConsumer
from twicc.auth.middleware import RequestOriginMiddleware
from twicc.request_origin import UI_ORIGIN, current_origin


def test_each_frame_runs_with_the_ui_origin_and_restores_the_carrier(monkeypatch):
    seen = []

    async def probe(self, content):
        seen.append(current_origin())

    monkeypatch.setattr(WSConsumer, "_handle_changelog_seen", probe)

    async def scenario():
        await WSConsumer().receive_json({"type": "changelog_seen"})
        return current_origin()

    assert asyncio.run(scenario()) is None
    assert seen == [UI_ORIGIN]


def test_a_detached_task_spawned_by_a_frame_inherits_the_ui_origin(monkeypatch):
    seen = []

    async def probe(self, session_id, content):
        seen.append(current_origin())

    monkeypatch.setattr(WSConsumer, "_run_send_message", probe)

    async def scenario():
        await WSConsumer().receive_json(
            {"type": "send_message", "session_id": "s1", "project_id": "-p", "text": "hello"}
        )
        await asyncio.gather(*list(asgi._DETACHED_TASKS))

    asyncio.run(scenario())
    assert seen == [UI_ORIGIN]


def test_the_frame_origin_is_restored_even_when_the_handler_raises(monkeypatch):
    async def boom(self, content):
        raise RuntimeError("handler failed")

    monkeypatch.setattr(WSConsumer, "_handle_changelog_seen", boom)

    async def scenario():
        with pytest.raises(RuntimeError):
            await WSConsumer().receive_json({"type": "changelog_seen"})
        return current_origin()

    assert asyncio.run(scenario()) is None


async def _through_middleware(path):
    seen = []

    async def get_response(request):
        seen.append(current_origin())
        return HttpResponse()

    await RequestOriginMiddleware(get_response)(RequestFactory().get(path))
    return seen, current_origin()


def test_the_middleware_holds_the_ui_origin_around_a_view():
    assert asyncio.run(_through_middleware("/api/projects/")) == ([UI_ORIGIN], None)


def test_the_middleware_leaves_rpc_requests_to_the_rpc_view():
    assert asyncio.run(_through_middleware("/rpc/sessions")) == ([None], None)


def test_the_middleware_is_installed_after_the_auth_middlewares():
    chain = list(settings.MIDDLEWARE)
    assert "twicc.auth.middleware.RequestOriginMiddleware" in chain
    assert chain.index("twicc.auth.middleware.RequestOriginMiddleware") > chain.index(
        "twicc.auth.middleware.RpcTokenAuthMiddleware"
    )
