import json

import httpx
from openai import AsyncOpenAI
from pydantic import SecretStr

from ostrovok_agent.models import Intent, Requirements
from ostrovok_agent.reasoning import OpenAIReasoner


async def test_responses_model_reasoning_and_structured_output_without_network(settings):
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        text = json.dumps(Intent(origin="Москва").model_dump(mode="json"))
        return httpx.Response(
            200,
            json={
                "id": "resp_unit_test",
                "object": "response",
                "created_at": 1792920000,
                "status": "completed",
                "model": "gpt-6-luna",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_unit_test",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 10,
                    "total_tokens": 20,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "input_tokens_details": {"cached_tokens": 0},
                },
            },
        )

    settings.openai_api_key = SecretStr("unit-test-placeholder")
    reasoner = OpenAIReasoner(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        client = AsyncOpenAI(api_key="unit-test-placeholder", http_client=http_client)
        reasoner.model.root_async_client = client
        reasoner.model.async_client = client.responses
        intent = await reasoner.extract("Из Москвы", Requirements(), {"now": "2026-10-25"})
    assert intent.origin == "Москва"
    path, payload = requests[0]
    assert path == "/v1/responses"
    assert payload["model"] == "gpt-6-luna"
    assert payload["reasoning"]["effort"] == "low"
    assert payload["store"] is False
    assert payload["text"]["format"]["type"] == "json_schema"
