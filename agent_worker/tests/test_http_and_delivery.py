import asyncio
import json

import httpx
import pytest

from ostrovok_agent.backend import HttpBackend
from ostrovok_agent.data import HttpDataProvider
from ostrovok_agent.errors import (
    AuthenticationFailure,
    Conflict,
    PermanentFailure,
    SourceUnavailable,
)
from ostrovok_agent.models import Requirements
from ostrovok_agent.worker import consume_deliveries, process_delivery


async def test_parser_wire_contract_and_request_id(settings):
    seen = []

    def respond(request):
        payload = json.loads(request.content)
        seen.append((request.url.path, payload))
        return httpx.Response(
            200,
            json={
                "request_id": payload["request_id"],
                "status": "empty",
                "items": [],
                "warnings": [],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://parser"
    ) as client:
        data = HttpDataProvider(settings, client)
        await data.places("Казань")
        from ostrovok_agent.models import Place

        place = Place(place_id="city:kazan", name="Казань", timezone="Europe/Moscow")
        req = Requirements(check_in="2026-11-10", check_out="2026-11-13", adults=2)
        await data.hotels(place, req)
        await data.transport(place, place, req)
    assert [x[0] for x in seen] == [
        "/internal/v1/search/places",
        "/internal/v1/search/hotels",
        "/internal/v1/search/transport",
    ]
    assert seen[1][1]["guests"] == {"adults": 2, "children_ages": []}
    assert seen[2][1]["return_date"] == "2026-11-13"


@pytest.mark.parametrize(
    "status,error",
    [(401, AuthenticationFailure), (422, PermanentFailure), (503, SourceUnavailable)],
)
async def test_parser_errors_not_empty(status, error, settings):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json={})),
        base_url="http://parser",
    ) as client:
        with pytest.raises(error):
            await HttpDataProvider(settings, client).places("Казань")


async def test_backend_does_not_send_from_and_preserves_revision(settings):
    def respond(request):
        payload = json.loads(request.content)
        assert "from" not in payload and payload["from_revision"] == 19
        return httpx.Response(
            409, json={"error": {"code": "version_conflict", "details": {"current_revision": 20}}}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://backend"
    ) as client:
        with pytest.raises(Conflict) as caught:
            await HttpBackend(settings, client).message(1, {"from_revision": 19, "operations": []})
        assert caught.value.revision == 20


class Delivery:
    def __init__(self, body=b'{"chat_id":1}'):
        self.body, self.headers, self.processed, self.settlement = body, {}, False, None

    async def ack(self):
        self.processed, self.settlement = True, "ack"

    async def nack(self, requeue):
        self.processed, self.settlement = True, ("nack", requeue)

    async def reject(self, requeue):
        self.processed, self.settlement = True, ("reject", requeue)


@pytest.mark.parametrize(
    "failure,settlement",
    [(None, "ack"), (ConnectionError(), ("nack", True)), (PermanentFailure(), ("reject", False))],
)
async def test_delivery_settlement(failure, settlement, settings):
    class Session:
        async def run(self, chat_id):
            if failure:
                raise failure

    delivery = Delivery()
    await process_delivery(delivery, Session(), settings, None)
    assert delivery.settlement == settlement


async def test_invalid_task_and_exhausted_delivery_rejected(settings):
    delivery = Delivery(b'{"chat_id":true}')
    await process_delivery(delivery, None, settings, None)
    assert delivery.settlement == ("reject", False)
    delivery = Delivery()
    delivery.headers = {"x-delivery-count": 3}
    await process_delivery(delivery, None, settings, None)
    assert delivery.settlement == ("reject", False)


async def test_waiting_chat_does_not_block_another_delivery(settings):
    stop = asyncio.Event()
    second_started = asyncio.Event()
    first = Delivery()
    second = Delivery(b'{"chat_id":2}')

    class Session:
        async def run(self, chat_id):
            if chat_id == 1:
                await second_started.wait()
            else:
                second_started.set()

    async def deliveries():
        yield first
        yield second
        await stop.wait()

    worker = asyncio.create_task(consume_deliveries(deliveries().__aiter__(), stop,
                                                     Session(), settings, None))
    try:
        await asyncio.wait_for(second_started.wait(), 1)
        async def both_settled():
            while first.settlement != "ack" or second.settlement != "ack":
                await asyncio.sleep(0.01)
        await asyncio.wait_for(both_settled(), 1)
    finally:
        stop.set()
        await asyncio.wait_for(worker, 1)


async def test_shutdown_requeues_waiting_delivery(settings):
    stop = asyncio.Event()
    started = asyncio.Event()
    delivery = Delivery()

    class Session:
        async def run(self, _chat_id):
            started.set()
            await asyncio.Event().wait()

    async def deliveries():
        yield delivery
        await stop.wait()

    worker = asyncio.create_task(consume_deliveries(deliveries().__aiter__(), stop,
                                                     Session(), settings, None))
    await asyncio.wait_for(started.wait(), 1)
    stop.set()
    await asyncio.wait_for(worker, 1)
    assert delivery.settlement == ("nack", True)


async def test_duplicate_chat_deliveries_run_one_session_at_a_time(settings):
    stop = asyncio.Event()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    first, second = Delivery(), Delivery()
    calls = 0

    class Session:
        async def run(self, _chat_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                await release_first.wait()
            else:
                second_started.set()

    async def deliveries():
        yield first
        yield second
        await stop.wait()

    worker = asyncio.create_task(consume_deliveries(deliveries().__aiter__(), stop,
                                                     Session(), settings, None))
    try:
        await asyncio.wait_for(first_started.wait(), 1)
        await asyncio.sleep(0.05)
        assert not second_started.is_set()
        release_first.set()
        await asyncio.wait_for(second_started.wait(), 1)
        async def both_settled():
            while first.settlement != "ack" or second.settlement != "ack":
                await asyncio.sleep(0.01)
        await asyncio.wait_for(both_settled(), 1)
    finally:
        stop.set()
        await asyncio.wait_for(worker, 1)
