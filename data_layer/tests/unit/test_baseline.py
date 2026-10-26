"""Базовые тесты текущего коммита: применённые правки P2, P5, P9.
Запуск из корня репозитория:  python -m unittest -v data_layer.tests.unit.test_baseline
Ожидаемо: 7 passed."""
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from data_layer import Catalog, DataGateway, SyntheticAdapter
from data_layer.contracts import (
    DataMode, Guests, HotelSearchQuery, Offer, OfferCheckRef, Price,
    Readiness, RevalidateReason, RevalidateRequest, RevalidateStatus, Terms,
)
from data_layer.hashing import calculate_offer_hash


def _offer(**details_overrides) -> Offer:
    details = {
        "hotel_id": "h1", "check_in": "2026-06-10", "check_out": "2026-06-17",
        "nights": 7, "rooms": 1,
        "guests": {"adults": 2, "children_ages": [], "pets": []},
        "room": {"type": "standard", "capacity": 3},
    }
    details.update(details_overrides)
    return Offer(
        id="of_1", provider="synthetic_v1", provider_offer_id="hotel:h1",
        kind="hotel", data_mode=DataMode.SYNTHETIC,
        price=Price(amount_minor=7000000), price_scope="entire_stay_all_guests",
        terms=Terms(cancellation="free", breakfast="included", pets_allowed=True),
        details=details,
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        content_hash="",
    )


class TestHashing(unittest.TestCase):
    def test_key_order_irrelevant(self):
        a = _offer()
        b = _offer(room={"capacity": 3, "type": "standard"})
        self.assertEqual(calculate_offer_hash(a), calculate_offer_hash(b))

    def test_service_fields_ignored(self):
        a = _offer()
        b = _offer(retrieved_at=datetime(2027, 5, 5, tzinfo=timezone.utc))
        self.assertEqual(calculate_offer_hash(a), calculate_offer_hash(b))

    def test_material_change_detected(self):
        a = _offer()
        b = _offer(check_out="2026-06-18", nights=8)
        self.assertNotEqual(calculate_offer_hash(a), calculate_offer_hash(b))

    def test_unknown_state_is_significant(self):
        a = _offer()
        b = _offer()
        b.terms.cancellation = "unknown"
        self.assertNotEqual(calculate_offer_hash(a), calculate_offer_hash(b))

    def test_price_scope_in_hash(self):  # регрессия P5
        a = _offer()
        b = _offer()
        b.price_scope = "per_person"
        self.assertNotEqual(calculate_offer_hash(a), calculate_offer_hash(b))


class TestSnapshotIsolation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gateway = DataGateway(Catalog(), SyntheticAdapter(Catalog()))

    def _query(self, qid, guests):
        return HotelSearchQuery(
            query_id=qid, destination_ids=["place_sochi"],
            check_in=date(2026, 11, 10), check_out=date(2026, 11, 13),
            guests=guests)

    async def test_distinct_guests_give_distinct_snapshots(self):  # регрессия P2
        one = (await self.gateway.search_hotels(
            self._query("h-1", Guests(adults=1)))).offers[0]
        two = (await self.gateway.search_hotels(
            self._query("h-2", Guests(adults=2)))).offers[0]
        self.assertNotEqual(one.id, two.id,
                            "Разные составы гостей должны давать разные снимки")

    async def test_revalidate_without_changes_is_unchanged(self):
        offer = (await self.gateway.search_hotels(
            self._query("h-1", Guests(adults=1)))).offers[0]
        result = await self.gateway.revalidate_offers(RevalidateRequest(
            reason=RevalidateReason.PLAN_CANDIDATE_CHECK,
            offers=[OfferCheckRef(offer_id=offer.id, content_hash=offer.content_hash)]))
        self.assertEqual(result.status, RevalidateStatus.UNCHANGED)
        self.assertIn(offer.id, result.confirmed_offer_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)