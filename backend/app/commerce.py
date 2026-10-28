"""Persistent demo cart and receipts. No external payment or booking is performed."""

from copy import deepcopy
from hashlib import sha256
import json
from uuid import uuid4

from bson import BSON
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .storage import ChatStore, timestamp


PREPAID_TRANSPORT = {"flight", "train", "rail", "bus", "ferry"}
MAX_CART_ITEMS = 20


class CommerceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def variant_hash(variant: dict) -> str:
    payload = json.dumps(variant, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def payable_lines(variant: dict, paid: set[tuple[int, int, str]]) -> list[dict]:
    lines = []
    chat_id, variant_id = variant["_chat_id"], variant["id"]
    for hotel in variant["habitations"]:
        line_id = f"hotel:{hotel['id']}"
        if (chat_id, variant_id, line_id) not in paid:
            lines.append({"id": line_id, "kind": "hotel", "name": hotel["name"],
                          "price": hotel["price"], "dates": deepcopy(hotel["dates"]),
                          "selected": True})
    for transport in variant["transports"]:
        transport_type = transport["type"].lower()
        if transport_type not in PREPAID_TRANSPORT:
            continue
        line_id = f"transport:{transport['id']}"
        if (chat_id, variant_id, line_id) not in paid:
            lines.append({"id": line_id, "kind": "transport", "type": transport["type"],
                          "name": transport["name"], "price": transport["price"],
                          "times": deepcopy(transport.get("times", {})), "selected": True})
    if not variant["habitations"] and not any(
        transport["type"].lower() in PREPAID_TRANSPORT for transport in variant["transports"]
    ):
        line_id = "trip:plan"
        if (chat_id, variant_id, line_id) not in paid:
            lines.append({"id": line_id, "kind": "trip", "name": variant["name"],
                          "price": variant["price"], "dates": deepcopy(variant["dates"]),
                          "selected": True})
    return lines


class CommerceStore:
    def __init__(self, chats: ChatStore):
        self.chats = chats
        self.collection = chats.db.commerce

    def _empty(self, user_id: int) -> dict:
        return {"_id": user_id, "version": 0, "items": [], "orders": [], "updated_at": timestamp()}

    def _read(self, user_id: int) -> dict:
        return self.collection.find_one({"_id": user_id}) or self._empty(user_id)

    def _mutate(self, user_id: int, change):
        for _ in range(20):
            current = self.collection.find_one({"_id": user_id})
            if current is None:
                try:
                    self.collection.insert_one(self._empty(user_id))
                except DuplicateKeyError:
                    pass
                continue
            modified = deepcopy(current)
            result, changed = change(modified)
            if not changed:
                return result
            modified["version"] += 1
            modified["updated_at"] = timestamp()
            if len(BSON.encode(modified)) > 3_000_000:
                raise CommerceError(409, "cart_capacity", "История покупок достигла лимита хранения")
            saved = self.collection.find_one_and_replace(
                {"_id": user_id, "version": current["version"]}, modified,
                return_document=ReturnDocument.AFTER)
            if saved is not None:
                return result
        raise CommerceError(409, "write_conflict", "Корзина меняется одновременно. Повторите действие")

    def _source(self, user_id: int, chat_id: int, variant_id: int) -> dict:
        chat = self.chats.get_chat(chat_id, user_id)
        if chat is None:
            raise CommerceError(404, "chat_not_found", "Чат не найден")
        variant = next((v for v in chat["variants"] if v["id"] == variant_id), None)
        if variant is None:
            raise CommerceError(409, "variant_unavailable", "Вариант больше не доступен")
        return variant

    @staticmethod
    def _paid_keys(doc: dict) -> set[tuple[int, int, str]]:
        return {(entry["chat_id"], entry["variant_id"], line["id"])
                for order in doc["orders"] for entry in order["items"] for line in entry["lines"]}

    @staticmethod
    def _item(doc: dict, item_id: str) -> dict:
        item = next((item for item in doc["items"] if item["id"] == item_id), None)
        if item is None:
            raise CommerceError(404, "cart_item_not_found", "Позиция корзины не найдена")
        return item

    def cart_view(self, user_id: int) -> dict:
        doc = self._read(user_id)
        items = [{**deepcopy(item), "stale": False} for item in doc["items"]]
        return {"items": items, "selected_count": sum(line["selected"] for item in items for line in item["lines"]),
                "demo_only": True}

    def add(self, user_id: int, chat_id: int, variant_id: int) -> dict:
        def change(doc):
            existing = next((item for item in doc["items"] if item["chat_id"] == chat_id
                             and item["variant_id"] == variant_id), None)
            if existing:
                return existing, False
            variant = self._source(user_id, chat_id, variant_id)
            if len(doc["items"]) >= MAX_CART_ITEMS:
                raise CommerceError(409, "cart_full", "В корзине может быть не больше 20 вариантов")
            lines = payable_lines({**variant, "_chat_id": chat_id}, self._paid_keys(doc))
            if not lines:
                raise CommerceError(409, "already_paid", "Все оплачиваемые части этого варианта уже оплачены")
            item = {"id": str(uuid4()), "chat_id": chat_id, "variant_id": variant_id,
                    "source_hash": variant_hash(variant), "name": variant["name"], "location": variant["location"],
                    "dates": deepcopy(variant["dates"]), "lines": lines, "added_at": timestamp()}
            doc["items"].append(item)
            return item, True

        return self._mutate(user_id, change)

    def remove(self, user_id: int, item_id: str) -> None:
        def change(doc):
            before = len(doc["items"])
            doc["items"] = [item for item in doc["items"] if item["id"] != item_id]
            return None, len(doc["items"]) != before
        self._mutate(user_id, change)

    def select_line(self, user_id: int, item_id: str, line_id: str, selected: bool) -> dict:
        def change(doc):
            item = self._item(doc, item_id)
            line = next((line for line in item["lines"] if line["id"] == line_id), None)
            if line is None:
                raise CommerceError(404, "cart_line_not_found", "Составная часть варианта не найдена")
            changed = line["selected"] != selected
            line["selected"] = selected
            return item, changed
        return self._mutate(user_id, change)

    def refresh(self, user_id: int, item_id: str) -> dict:
        current = self._item(self._read(user_id), item_id)
        variant = self._source(user_id, current["chat_id"], current["variant_id"])
        fingerprint = variant_hash(variant)

        def change(doc):
            item = self._item(doc, item_id)
            lines = payable_lines({**variant, "_chat_id": item["chat_id"]}, self._paid_keys(doc))
            if not lines:
                raise CommerceError(409, "nothing_to_add", "Все составные части варианта уже отмечены как оплаченные")
            old_selection = {line["id"]: line["selected"] for line in item["lines"]}
            for line in lines:
                line["selected"] = old_selection.get(line["id"], True)
            item.update({"source_hash": fingerprint, "name": variant["name"],
                         "location": variant["location"], "dates": deepcopy(variant["dates"]), "lines": lines})
            return item, True
        return self._mutate(user_id, change)

    def checkout(self, user_id: int, idempotency_key: str) -> dict:
        def change(doc):
            previous = next((order for order in doc["orders"] if order["idempotency_key"] == idempotency_key), None)
            if previous:
                return previous, False
            selected_items = [item for item in doc["items"] if any(line["selected"] for line in item["lines"])]
            if not selected_items:
                raise CommerceError(409, "empty_checkout", "Выберите отель или транспорт для оплаты")
            order_items = []
            for item in selected_items:
                selected = [deepcopy(line) for line in item["lines"] if line["selected"]]
                order_items.append({"chat_id": item["chat_id"], "variant_id": item["variant_id"],
                                    "name": item["name"], "lines": selected})
                item["lines"] = [line for line in item["lines"] if not line["selected"]]
            doc["items"] = [item for item in doc["items"] if item["lines"]]
            order = {"id": str(uuid4()), "idempotency_key": idempotency_key,
                     "status": "paid_demo", "paid_at": timestamp(), "items": order_items,
                     "message": "Демонстрационная оплата: деньги не списаны, бронирования не созданы"}
            doc["orders"].append(order)
            return order, True
        return self._mutate(user_id, change)

    def orders(self, user_id: int) -> list[dict]:
        return list(reversed(self._read(user_id)["orders"][-20:]))

    def order(self, user_id: int, order_id: str) -> dict:
        order = next((order for order in self._read(user_id)["orders"] if order["id"] == order_id), None)
        if order is None:
            raise CommerceError(404, "order_not_found", "Демонстрационный заказ не найден")
        return order
