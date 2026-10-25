"""Тесты стабильности хэша: каноническая форма, служебные поля,
существенные изменения, значимость unknown и влияние price_scope."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from data_layer.contracts import DataMode, Offer, Price, Terms
from data_layer.hashing import calculate_offer_hash


def _offer(retrieved_at=None, **details_overrides) -> Offer:
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
        retrieved_at=retrieved_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        content_hash="",
    )


def test_key_order_irrelevant():
    a = _offer()
    b = _offer(room={"capacity": 3, "type": "standard"})
    assert calculate_offer_hash(a) == calculate_offer_hash(b)


def test_service_fields_ignored():
    a = _offer()
    b = _offer(retrieved_at=datetime(2027, 5, 5, tzinfo=timezone.utc))
    assert calculate_offer_hash(a) == calculate_offer_hash(b)


def test_material_change_detected():
    a = _offer()
    b = _offer(check_out="2026-06-18", nights=8)
    assert calculate_offer_hash(a) != calculate_offer_hash(b)


def test_unknown_state_is_significant():
    a = _offer()
    b = _offer()
    b.terms.cancellation = "unknown"
    assert calculate_offer_hash(a) != calculate_offer_hash(b)


# P5 regression: price_scope должен влиять на хэш.
def test_price_scope_in_hash():
    a = _offer()
    b = _offer()
    b.price_scope = "per_person"
    assert calculate_offer_hash(a) != calculate_offer_hash(b)