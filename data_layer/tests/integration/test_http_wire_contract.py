"""Integration wire-contract tests for data_layer HTTP API.

Run from repository root:

python -m unittest -v data_layer.tests.integration.test_http_wire_contract

These tests check the public HTTP contract only.
They do not import agent_worker code.
"""

import os
import unittest

os.environ["PARSER_SERVICE_TOKEN"] = "test-token"
os.environ.setdefault("DATA_MODE", "synthetic")

from fastapi.testclient import TestClient  # noqa: E402
from data_layer.service import api as api_module  # noqa: E402

api_module.EXPECTED_TOKEN = "test-token"


class HttpWireContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api_module.app)
        cls.auth = {"Authorization": "Bearer test-token"}

    def post_json(self, path: str, payload: dict, expect: int = 200, headers: dict | None = None):
        merged_headers = dict(self.auth)
        if headers:
            merged_headers.update(headers)

        response = self.client.post(path, json=payload, headers=merged_headers)
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def assert_envelope(self, body: dict, request_id: str):
        self.assertEqual(body.get("request_id"), request_id)
        self.assertIn(
            body.get("status"),
            {"ok", "empty", "unsupported", "partial", "error"},
        )
        self.assertIsInstance(body.get("items"), list)
        self.assertIsInstance(body.get("warnings"), list)

        if body["status"] in {"ok", "partial"}:
            self.assertTrue(body["items"], "ok/partial status must contain items")

        if body["status"] in {"empty", "unsupported"}:
            self.assertEqual(body["items"], [], "empty/unsupported status must not contain items")

    def assert_source(self, item: dict):
        source = item.get("source") or {}
        self.assertTrue(source.get("provider"))
        self.assertIn(source.get("data_mode"), {"synthetic", "sandbox", "live"})
        self.assertIn("retrieved_at", source)

    def assert_base_offer(self, item: dict):
        self.assertIn(item.get("kind"), {"hotel", "transport", "transfer", "activity"})
        self.assertTrue(item.get("offer_id"))
        self.assertTrue(str(item.get("content_hash", "")).startswith("sha256:"))
        self.assertIsInstance(item.get("guests"), dict)
        self.assertIsInstance(item.get("price"), dict)
        self.assertIsInstance(item["price"].get("amount_minor"), int)
        self.assertTrue(item["price"].get("currency"))
        self.assertIn(
            item.get("price_scope"),
            {
                "entire_stay_all_guests",
                "per_person",
                "total",
                "round_trip_all_guests",
            },
        )
        self.assertTrue(item.get("availability"))
        self.assertIsInstance(item.get("warnings"), list)
        self.assert_source(item)

    def assert_hotel_item(self, item: dict):
        self.assert_base_offer(item)
        self.assertEqual(item.get("kind"), "hotel")
        self.assertIn(item.get("meal_plan"), {"breakfast", "none", "unknown"})
        self.assertNotEqual(item.get("meal_plan"), "included")

        cancellation = item.get("cancellation") or {}
        self.assertIn(
            cancellation.get("kind"),
            {"free", "paid", "non_refundable", "unknown"},
        )

        self.assertIn(
            item.get("price_scope"),
            {"entire_stay_all_guests", "per_person", "total"},
        )

        self.assertIsInstance(item.get("pets_allowed"), (bool, type(None)))

    def assert_transport_item(self, item: dict):
        self.assert_base_offer(item)
        self.assertEqual(item.get("kind"), "transport")
        self.assertIn(item.get("price_scope"), {"round_trip_all_guests", "total"})

        segments = item.get("segments")
        self.assertIsInstance(segments, list)
        self.assertTrue(segments, "transport offer must contain segments")

        directions = []

        for segment in segments:
            self.assertIn(segment.get("direction"), {"outbound", "return"})
            directions.append(segment.get("direction"))

            self.assertTrue(segment.get("type"))
            self.assertTrue(str(segment.get("origin_id", "")).startswith("city:"))
            self.assertTrue(str(segment.get("destination_id", "")).startswith("city:"))
            self.assertIn("T", str(segment.get("departure", "")))
            self.assertIn("T", str(segment.get("arrival", "")))

        self.assertIn("outbound", directions)

        if "return" in directions:
            self.assertEqual(item.get("price_scope"), "round_trip_all_guests")
        else:
            self.assertEqual(item.get("price_scope"), "total")

        self.assertIn(item.get("checked_baggage"), (True, False, None))
        self.assertIsInstance(item.get("pets_allowed"), (bool, type(None)))

    def assert_transfer_item(self, item: dict):
        self.assert_base_offer(item)
        self.assertEqual(item.get("kind"), "transfer")
        self.assertEqual(item.get("price_scope"), "total")

    def hotel_payload(self, request_id: str = "hotels-1", **updates) -> dict:
        payload = {
            "request_id": request_id,
            "destination_id": "city:sochi",
            "check_in": "2026-11-10",
            "check_out": "2026-11-13",
            "guests": {
                "adults": 2,
                "children_ages": [],
                "pets": [],
            },
            "rooms": 1,
            "currency": "RUB",
            "limit": 10,
            "constraints": {},
        }
        payload.update(updates)
        return payload

    def transport_payload(self, request_id: str = "transport-1", **updates) -> dict:
        payload = {
            "request_id": request_id,
            "origin_id": "city:moscow",
            "destination_id": "city:sochi",
            "departure_date": "2026-11-10",
            "guests": {
                "adults": 2,
                "children_ages": [],
                "pets": [],
            },
            "modes": ["flight", "train"],
            "currency": "RUB",
            "limit": 10,
            "constraints": {},
        }
        payload.update(updates)
        return payload

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body.get("status"), "ok")
        self.assertEqual(body.get("service"), "data-layer")

    def test_places_requires_auth(self):
        response = self.client.post(
            "/internal/v1/search/places",
            json={"request_id": "places-no-auth", "query": "Казань"},
        )
        self.assertEqual(response.status_code, 401)

    def test_places_rejects_invalid_token(self):
        response = self.client.post(
            "/internal/v1/search/places",
            json={"request_id": "places-bad-token", "query": "Казань"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(response.status_code, 401)

    def test_places_ok(self):
        payload = {
            "request_id": "places-ok",
            "query": "Казань",
            "limit": 3,
        }

        body = self.post_json("/internal/v1/search/places", payload)
        self.assert_envelope(body, "places-ok")
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["items"])

        item = body["items"][0]
        self.assertTrue(str(item.get("place_id", "")).startswith("city:"))
        self.assertTrue(item.get("name"))
        self.assertTrue(item.get("timezone"))

    def test_places_empty(self):
        payload = {
            "request_id": "places-empty",
            "query": "НетТакогоГорода",
            "limit": 3,
        }

        body = self.post_json("/internal/v1/search/places", payload)
        self.assert_envelope(body, "places-empty")
        self.assertEqual(body["status"], "empty")
        self.assertEqual(body["items"], [])

    def test_hotels_basic_wire_contract(self):
        payload = self.hotel_payload(request_id="hotels-basic")

        body = self.post_json("/internal/v1/search/hotels", payload)
        self.assert_envelope(body, "hotels-basic")
        self.assertIn(body["status"], {"ok", "partial"})
        self.assertTrue(body["items"])

        for item in body["items"]:
            self.assert_hotel_item(item)

    def test_hotels_include_every_room_in_price(self):
        payload = self.hotel_payload(request_id="hotels-rooms", rooms=2)
        two = self.post_json("/internal/v1/search/hotels", payload)
        one = self.post_json("/internal/v1/search/hotels", self.hotel_payload(request_id="hotels-one", rooms=1))
        prices = {item["hotel_id"]: item["price"]["amount_minor"] for item in one["items"]}
        self.assertTrue(two["items"])
        for item in two["items"]:
            self.assertEqual(item["price"]["amount_minor"], prices[item["hotel_id"]] * 2)

    def test_hotels_rejects_missing_check_in(self):
        payload = self.hotel_payload(request_id="hotels-missing-date")
        del payload["check_in"]
        self.post_json("/internal/v1/search/hotels", payload, expect=400)

    def test_hotels_rejects_bad_date(self):
        payload = self.hotel_payload(request_id="hotels-bad-date", check_in="bad-date")
        self.post_json("/internal/v1/search/hotels", payload, expect=422)

    def test_hotels_filters_do_not_produce_ready_paid_cancellation(self):
        payload = self.hotel_payload(request_id="hotels-filters")
        payload["constraints"] = {
            "refundable_only": True,
            "breakfast_included": True,
            "pets": True,
        }
        payload["guests"]["pets"] = [{"type": "dog", "size": "small"}]

        body = self.post_json("/internal/v1/search/hotels", payload)
        self.assert_envelope(body, "hotels-filters")

        for item in body["items"]:
            self.assert_hotel_item(item)

            if item.get("readiness") == "ready":
                cancellation_kind = (item.get("cancellation") or {}).get("kind")
                self.assertNotIn(
                    cancellation_kind,
                    {"paid", "non_refundable"},
                    "ready hotel must not violate refundable_only constraint",
                )

    def test_transport_round_trip_wire_contract(self):
        payload = self.transport_payload(
            request_id="transport-round",
            return_date="2026-11-13",
        )

        body = self.post_json("/internal/v1/search/transport", payload)
        self.assert_envelope(body, "transport-round")
        self.assertIn(body["status"], {"ok", "partial"})
        self.assertTrue(body["items"])

        for item in body["items"]:
            self.assert_transport_item(item)
            self.assertEqual(item.get("price_scope"), "round_trip_all_guests")

            directions = {segment.get("direction") for segment in item.get("segments", [])}
            self.assertEqual(directions, {"outbound", "return"})

    def test_transport_one_way_wire_contract(self):
        payload = self.transport_payload(request_id="transport-oneway")

        body = self.post_json("/internal/v1/search/transport", payload)
        self.assert_envelope(body, "transport-oneway")
        self.assertIn(body["status"], {"ok", "partial"})
        self.assertTrue(body["items"])

        for item in body["items"]:
            self.assert_transport_item(item)
            self.assertEqual(item.get("price_scope"), "total")

            directions = {segment.get("direction") for segment in item.get("segments", [])}
            self.assertEqual(directions, {"outbound"})

    def test_intercity_transfer_is_unsupported(self):
        payload = {
            "request_id": "transfer-intercity",
            "pickup_place_id": "city:moscow",
            "dropoff_place_id": "city:sochi",
            "pickup_at": "2026-11-10T12:00:00+03:00",
            "passengers": {
                "adults": 2,
                "children_ages": [],
                "pets": [],
            },
            "luggage": 1,
        }

        body = self.post_json("/internal/v1/search/transfers", payload)
        self.assert_envelope(body, "transfer-intercity")
        self.assertEqual(body["status"], "unsupported")
        self.assertEqual(body["items"], [])
        self.assertTrue(body["warnings"])

    def test_local_transfer_wire_contract(self):
        payload = {
            "request_id": "transfer-local",
            "pickup_place_id": "city:sochi",
            "dropoff_place_id": "city:sochi",
            "pickup_at": "2026-11-10T12:00:00+03:00",
            "passengers": {
                "adults": 2,
                "children_ages": [5],
                "pets": [],
            },
            "luggage": 2,
        }

        body = self.post_json("/internal/v1/search/transfers", payload)
        self.assert_envelope(body, "transfer-local")
        self.assertIn(body["status"], {"ok", "partial"})
        self.assertTrue(body["items"])

        for item in body["items"]:
            self.assert_transfer_item(item)

    def test_revalidate_confirms_previous_hotel_offer(self):
        hotel_body = self.post_json(
            "/internal/v1/search/hotels",
            self.hotel_payload(request_id="hotels-for-revalidate"),
        )

        self.assert_envelope(hotel_body, "hotels-for-revalidate")
        self.assertIn(hotel_body["status"], {"ok", "partial"})
        self.assertTrue(hotel_body["items"])

        offer = hotel_body["items"][0]

        revalidate_payload = {
            "request_id": "reval-1",
            "reason": "checkout_preview_check",
            "offers": [
                {
                    "offer_id": offer["offer_id"],
                    "content_hash": offer["content_hash"],
                }
            ],
        }

        body = self.post_json("/internal/v1/revalidate", revalidate_payload)

        self.assertEqual(body.get("request_id"), "reval-1")
        self.assertIn(
            body.get("status"),
            {
                "unchanged",
                "changed",
                "partial_unknown",
                "unavailable",
                "timeout",
                "rate_limited",
                "error",
            },
        )
        self.assertIn(offer["offer_id"], body.get("confirmed_offer_ids", []))
        self.assertIsInstance(body.get("warnings"), list)
        self.assertTrue(body.get("checked_at"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
