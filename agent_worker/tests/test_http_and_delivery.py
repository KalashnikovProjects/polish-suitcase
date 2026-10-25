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
from ostrovok_agent.worker import process_delivery


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
