"""End-to-end API tests for the persistent demonstration checkout."""

from uuid import uuid4

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.commerce import CommerceStore
from app.main import app
from app.storage import ChatStore


class NoopPublisher:
    def wake(self):
        pass


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-at-least-32-characters")
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "test-agent-token")
    store = ChatStore(mongomock.MongoClient(), "test_commerce")
    store.ensure_indexes()
    app.state.store = store
    app.state.commerce = CommerceStore(store)
    app.state.publisher = NoopPublisher()
    client = TestClient(app)
    users = []
    for email in ("buyer@example.com", "other@example.com"):
        assert client.post("/api/v1/auth/register", json={"email": email, "password": "password123"}).status_code == 201
        login = client.post("/api/v1/auth/login", json={"email": email, "password": "password123"})
        users.append({"Authorization": f"Bearer {login.json()['access_token']}"})
    created = client.post("/api/v1/chats", headers=users[0], json={"name": "Поездка", "message": "Поедем"})
    chat_id = created.json()["id"]
    variant = {
        "id": 7, "name": "Неделя у моря", "location": "Сочи", "price": "от 50 000 ₽",
        "dates": {"from": "01.11", "to": "08.11"}, "completed": True,
        "description": "", "urls": [], "additional": [],
        "habitations": [{"id": 11, "name": "Отель", "description": "",
                         "dates": {"from": "01.11", "to": "08.11"}, "url": "https://example.test/hotel",
                         "price": "20 000 ₽", "additional": []}],
        "transports": [{"id": 21, "name": "Самолёт", "type": "flight", "price": "15 000 ₽", "additional": []},
                       {"id": 22, "name": "Такси", "type": "taxi", "price": "900 ₽", "additional": []}],
    }
    store.chats.update_one({"_id": chat_id}, {"$set": {"variants": [variant]}})
    return client, store, users[0], users[1], chat_id, variant


def test_add_select_partial_checkout_and_idempotency(context):
    client, _store, buyer, other, chat_id, _variant = context
    assert client.get("/api/v1/cart", headers=buyer).json() == {
        "items": [], "selected_count": 0, "demo_only": True}
    assert client.post("/api/v1/cart/items", headers=other,
                       json={"chat_id": chat_id, "variant_id": 7}).status_code == 404
    added = client.post("/api/v1/cart/items", headers=buyer,
                        json={"chat_id": chat_id, "variant_id": 7})
    assert added.status_code == 201
    item = added.json()
    assert {line["id"] for line in item["lines"]} == {"hotel:11", "transport:21"}
    assert client.post("/api/v1/cart/items", headers=buyer,
                       json={"chat_id": chat_id, "variant_id": 7}).json()["id"] == item["id"]
    assert client.get("/api/v1/cart", headers=other).json()["items"] == []
    line_url = f"/api/v1/cart/items/{item['id']}/lines/hotel:11"
    assert client.put(line_url, headers=other, json={"selected": False}).status_code == 404
    assert client.put(line_url, headers=buyer, json={"selected": False}).status_code == 200
    key = str(uuid4())
    paid = client.post("/api/v1/cart/checkout", headers=buyer, json={"idempotency_key": key})
    assert paid.status_code == 201
    receipt = paid.json()
    assert receipt["status"] == "paid_demo"
    assert [line["id"] for line in receipt["items"][0]["lines"]] == ["transport:21"]
    assert "деньги не списаны" in receipt["message"]
    assert client.post("/api/v1/cart/checkout", headers=buyer,
                       json={"idempotency_key": key}).json()["id"] == receipt["id"]
    cart = client.get("/api/v1/cart", headers=buyer).json()
    assert [line["id"] for line in cart["items"][0]["lines"]] == ["hotel:11"]
    assert cart["selected_count"] == 0
    assert client.post("/api/v1/cart/checkout", headers=buyer,
                       json={"idempotency_key": str(uuid4())}).status_code == 409
    assert client.put(line_url, headers=buyer, json={"selected": True}).status_code == 200
    second = client.post("/api/v1/cart/checkout", headers=buyer,
                         json={"idempotency_key": str(uuid4())})
    assert second.status_code == 201
    assert client.get("/api/v1/cart", headers=buyer).json()["items"] == []
    assert len(client.get("/api/v1/orders", headers=buyer).json()) == 2
    assert client.get(f"/api/v1/orders/{second.json()['id']}", headers=buyer).status_code == 200
    assert client.get(f"/api/v1/orders/{second.json()['id']}", headers=other).status_code == 404
    paid_again = client.post("/api/v1/cart/items", headers=buyer,
                             json={"chat_id": chat_id, "variant_id": 7})
    assert paid_again.status_code == 409
    assert paid_again.json()["error"] == {"code": "already_paid",
                                           "message": "Все оплачиваемые части этого варианта уже оплачены"}


def test_cart_snapshot_survives_agent_changes_and_source_removal(context):
    client, store, buyer, _other, chat_id, variant = context
    item = client.post("/api/v1/cart/items", headers=buyer,
                       json={"chat_id": chat_id, "variant_id": 7}).json()
    updated = {**variant, "name": "Новый маршрут",
               "habitations": [{**variant["habitations"][0], "price": "25 000 ₽"}]}
    store.chats.update_one({"_id": chat_id}, {"$set": {"variants": [updated]}})
    snapshot = client.get("/api/v1/cart", headers=buyer).json()["items"][0]
    assert snapshot["stale"] is False
    assert snapshot["name"] == variant["name"]
    assert snapshot["lines"][0]["price"] == "20 000 ₽"
    added_again = client.post("/api/v1/cart/items", headers=buyer,
                              json={"chat_id": chat_id, "variant_id": 7})
    assert added_again.status_code == 201
    assert added_again.json()["id"] == item["id"]
    assert added_again.json()["lines"][0]["price"] == "20 000 ₽"

    store.chats.update_one({"_id": chat_id}, {"$set": {"variants": []}})
    assert client.get("/api/v1/cart", headers=buyer).json()["items"][0]["stale"] is False
    assert client.post("/api/v1/cart/items", headers=buyer,
                       json={"chat_id": chat_id, "variant_id": 7}).json()["id"] == item["id"]
    paid = client.post("/api/v1/cart/checkout", headers=buyer,
                       json={"idempotency_key": str(uuid4())})
    assert paid.status_code == 201
    assert paid.json()["items"][0]["name"] == variant["name"]
    assert paid.json()["items"][0]["lines"][0]["price"] == "20 000 ₽"
    assert client.get("/api/v1/cart", headers=buyer).json()["items"] == []


def test_explicit_refresh_replaces_saved_snapshot(context):
    client, store, buyer, _other, chat_id, variant = context
    item = client.post("/api/v1/cart/items", headers=buyer,
                       json={"chat_id": chat_id, "variant_id": 7}).json()
    updated = {**variant, "habitations": [{**variant["habitations"][0], "price": "25 000 ₽"}]}
    store.chats.update_one({"_id": chat_id}, {"$set": {"variants": [updated]}})
    refreshed = client.post(f"/api/v1/cart/items/{item['id']}/refresh", headers=buyer)
    assert refreshed.status_code == 200
    assert refreshed.json()["lines"][0]["price"] == "25 000 ₽"
    assert client.get("/api/v1/cart", headers=buyer).json()["items"][0]["stale"] is False
    store.chats.update_one({"_id": chat_id}, {"$set": {"variants": []}})
    assert client.post(f"/api/v1/cart/items/{item['id']}/refresh", headers=buyer).status_code == 409
    assert client.get("/api/v1/cart", headers=buyer).json()["items"][0]["stale"] is False
    assert client.post("/api/v1/cart/checkout", headers=buyer,
                       json={"idempotency_key": str(uuid4())}).status_code == 201
    assert client.get("/api/v1/cart", headers=buyer).json()["items"] == []


def test_incomplete_variant_and_no_card_data(context):
    client, store, buyer, _other, chat_id, variant = context
    store.chats.update_one({"_id": chat_id}, {"$set": {"variants": [{**variant, "completed": False}]}})
    added = client.post("/api/v1/cart/items", headers=buyer,
                       json={"chat_id": chat_id, "variant_id": 7})
    assert added.status_code == 201
    assert {line["id"] for line in added.json()["lines"]} == {"hotel:11", "transport:21"}
    assert client.post("/api/v1/cart/checkout", headers=buyer,
                       json={"idempotency_key": str(uuid4()), "card_number": "1234"}).status_code == 422
    assert client.get("/api/v1/cart").status_code == 401


def test_variant_without_prepaid_parts_still_enters_demo_cart(context):
    client, store, buyer, _other, chat_id, variant = context
    store.chats.update_one({"_id": chat_id}, {"$set": {"variants": [{
        **variant, "habitations": [], "transports": [variant["transports"][1]]}]}})
    response = client.post("/api/v1/cart/items", headers=buyer,
                           json={"chat_id": chat_id, "variant_id": 7})
    assert response.status_code == 201
    assert response.json()["lines"][0]["id"] == "trip:plan"
    assert response.json()["lines"][0]["kind"] == "trip"
    paid = client.post("/api/v1/cart/checkout", headers=buyer,
                       json={"idempotency_key": str(uuid4())})
    assert paid.status_code == 201
    assert paid.json()["items"][0]["lines"][0]["kind"] == "trip"
