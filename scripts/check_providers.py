"""Opt-in read-only provider smoke. Never prints keys or creates bookings."""
import asyncio
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

import httpx
from dotenv import load_dotenv
import truststore

truststore.inject_into_ssl()


async def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        key = os.getenv("OPENAI_API_KEY")
        if key:
            response = await client.get(
                "https://api.openai.com/v1/models/" + os.getenv("OPENAI_MODEL", "gpt-6-luna"),
                headers={"Authorization": "Bearer " + key})
            print(json.dumps({"provider": "openai", "operation": "model_access", "status": response.status_code}))
            if "--extract" in sys.argv and response.is_success:
                sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent_worker/src"))
                from ostrovok_agent.config import Settings
                from ostrovok_agent.models import Requirements
                from ostrovok_agent.reasoning import OpenAIReasoner
                start = date.today() + timedelta(days=28)
                intent = await OpenAIReasoner(Settings()).extract(
                    f"Из Москвы в Рим, {start} — {start + timedelta(days=3)}, 2 взрослых, бюджет 100000 рублей",
                    Requirements(), {"now": date.today().isoformat(), "timezone": "Europe/Moscow"})
                print(json.dumps({"provider": "openai", "operation": "real_extraction", "origin": intent.origin,
                                  "destinations": intent.destinations, "adults": intent.adults}, ensure_ascii=False))
        key = os.getenv("LITEAPI_KEY")
        if key:
            start = date.today() + timedelta(days=28)
            response = await client.post("https://api.liteapi.travel/v3.0/hotels/rates",
                headers={"X-API-Key": key}, json={
                    "countryCode": "IT", "cityName": "Rome", "currency": "RUB",
                    "guestNationality": "RU", "checkin": start.isoformat(),
                    "checkout": (start + timedelta(days=3)).isoformat(),
                    "occupancies": [{"adults": 2}], "limit": 3,
                    "maxRatesPerHotel": 1, "includeHotelData": True, "timeout": 6})
            try:
                body = response.json()
            except ValueError:
                body = {}
            print(json.dumps({"provider": "liteapi", "operation": "rates", "status": response.status_code,
                              "keys": list(body), "hotels": len(body.get("data", [])),
                              "sandbox": body.get("sandbox")}))
            # A reduced public shape is useful for mapping the provider DTO, without auth data.
            if response.is_success and body.get("data"):
                item = body["data"][0]
                room = item.get("roomTypes", [{}])[0]
                rate = room.get("rates", [{}])[0]
                print(json.dumps({"hotel_keys": list(item), "room_keys": list(room),
                                  "rate_keys": list(rate), "price": rate.get("retailRate"),
                                  "cancellation": rate.get("cancellationPolicies"),
                                  "board": rate.get("boardName"),
                                  "hotel_metadata_keys": list(body.get("hotels", [{}])[0])}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        detail = str(exc)
        for name in ("OPENAI_API_KEY", "LITEAPI_KEY"):
            if os.getenv(name):
                detail = detail.replace(os.environ[name], "[redacted]")
        print(json.dumps({"error_type": type(exc).__name__, "detail": detail[:200]}))
        raise SystemExit(1) from None
