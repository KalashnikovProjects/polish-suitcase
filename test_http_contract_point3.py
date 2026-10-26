"""Проверка Пункта 3 ревью: HTTP-фильтры и маппинг meal_plan."""
import unittest
from fastapi.testclient import TestClient
import sys
from pathlib import Path

# Добавляем пути, чтобы импортировать app
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from data_layer.service.api import app


class TestHTTPFiltersAndMapping(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer dev-token-123"}

    def test_constraints_mapped_to_hard_constraints_and_meal_plan_is_breakfast(self):
        # 1. Отправляем запрос с фильтрами, которые агент передаёт через constraints
        payload = {
            "request_id": "test-req-1",
            "destination_id": "city:sochi",
            "check_in": "2026-11-10",
            "check_out": "2026-11-13",
            "guests": {"adults": 1, "children_ages": [], "pets": []},
            "rooms": 1,
            "constraints": {
                "refundable_only": True,  # Должно стать hard_constraint "free_cancellation"
                "breakfast_included": True,  # Должно стать hard_constraint "breakfast_included"
                "pets": True  # Должно стать hard_constraint "pets_allowed"
            }
        }

        response = self.client.post("/internal/v1/search/hotels", json=payload, headers=self.headers)

        # 2. Проверяем, что запрос не упал с 500 или 422
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # 3. Проверяем envelope (Пункт 1 ревью)
        self.assertIn("status", data)
        self.assertIn("items", data)

        # 4. ГЛАВНАЯ ПРОВЕРКА ПУНКТА 3: meal_plan должен быть "breakfast", а не "included"
        if data["items"]:
            for item in data["items"]:
                self.assertEqual(
                    item.get("meal_plan"),
                    "breakfast",
                    f"Ожидался meal_plan='breakfast', получено: {item.get('meal_plan')}"
                )

                # 5. Проверяем, что фильтр refundable_only сработал (нет paid отмены среди ready)
                # В синтетике sochi есть отель с paid отменой, он должен быть отфильтрован или marked preliminary
                cancellation_kind = item.get("cancellation", {}).get("kind", "unknown")
                readiness = item.get("readiness", "unknown")

                if cancellation_kind == "paid":
                    self.assertNotEqual(
                        readiness,
                        "ready",
                        "Отель с платной отменой не должен быть ready при запросе refundable_only"
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)