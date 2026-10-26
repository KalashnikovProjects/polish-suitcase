import json

import httpx
from conftest import FakeBackend

from ostrovok_agent.backend import HttpBackend
from ostrovok_agent.session import ChatSession


async def test_http_sse_rest_entire_session(settings, snapshot, planner):
    server = FakeBackend(snapshot)
    paths = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            async for event in server.events(1):
                yield (
                    f"event: {event.name}\nid: {event.id}\ndata: "
                    + json.dumps(event.data, ensure_ascii=False)
                    + "\n\n"
                ).encode()

    async def route(request):
        assert request.headers["Authorization"] == "Bearer local-test-token"
        paths.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, stream=Stream()
            )
        data = json.loads(request.content)
        if request.url.path.endswith("/messages"):
            result = await server.message(1, data)
            return httpx.Response(201, json=result)
        result = await server.status(1, data)
        return httpx.Response(200, json=result)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(route),
        base_url="http://backend",
        headers={"Authorization": "Bearer local-test-token"},
    ) as client:
        await ChatSession(settings, HttpBackend(settings, client), planner).run(1)
    assert paths[0] == ("GET", "/api/v1/chats/1/events")
    assert ("POST", "/api/v1/chats/1/messages") in paths
    assert server.chat.agent.status == "completed"
    assert len(server.writes) == 1
