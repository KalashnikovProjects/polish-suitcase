"""Тесты фабрики адаптеров и логики fallback."""
import unittest
from unittest.mock import AsyncMock, patch

from data_layer.adapters.base import AdapterResult, BaseAdapter
from data_layer.adapters.composite import CompositeAdapter
from data_layer.adapters.fallback import FallbackAdapter
from data_layer.adapters.factory import AdapterFactory
from data_layer.adapters.synthetic import SyntheticAdapter
from data_layer.catalog import Catalog
from data_layer.config import Settings
from data_layer.contracts import DataMode, SearchStatus


class StubAdapter(BaseAdapter):
    """Тестовый адаптер с настраиваемым результатом."""

    data_mode = DataMode.SYNTHETIC
    provider_name = "stub"

    def __init__(self, result: AdapterResult | None = None):
        self._result = result or AdapterResult(status=SearchStatus.OK)

    async def search_hotels(self, query):
        return self._result

    async def search_transport(self, query):
        return self._result

    async def estimate_transfers(self, query):
        return self._result


class FallbackAdapterTests(unittest.IsolatedAsyncioTestCase):

    async def test_first_adapter_success(self):
        """Первый адаптер вернул OK — fallback не нужен."""
        first = StubAdapter(AdapterResult(status=SearchStatus.OK))
        second = StubAdapter(AdapterResult(status=SearchStatus.EMPTY))

        chain = FallbackAdapter([first, second])
        result = await chain.search_hotels(None)

        self.assertEqual(result.status, SearchStatus.OK)

    async def test_fallback_on_timeout(self):
        """Первый адаптер вернул TIMEOUT — используется второй."""
        first = StubAdapter(
            AdapterResult(status=SearchStatus.TIMEOUT, warnings=["timed out"])
        )
        second = StubAdapter(AdapterResult(status=SearchStatus.OK))

        chain = FallbackAdapter([first, second])
        result = await chain.search_hotels(None)

        self.assertEqual(result.status, SearchStatus.OK)
        self.assertTrue(
            any("fallback" in w.lower() for w in result.warnings),
            f"Expected fallback warning, got: {result.warnings}",
        )

    async def test_fallback_on_rate_limited(self):
        """Первый адаптер вернул RATE_LIMITED — используется второй."""
        first = StubAdapter(AdapterResult(status=SearchStatus.RATE_LIMITED))
        second = StubAdapter(AdapterResult(status=SearchStatus.OK))

        chain = FallbackAdapter([first, second])
        result = await chain.search_hotels(None)

        self.assertEqual(result.status, SearchStatus.OK)

    async def test_no_fallback_on_empty(self):
        """EMPTY не триггерит fallback — источник ответил, но данных нет."""
        first = StubAdapter(AdapterResult(status=SearchStatus.EMPTY))
        second = StubAdapter(AdapterResult(status=SearchStatus.OK))

        chain = FallbackAdapter([first, second])
        result = await chain.search_hotels(None)

        self.assertEqual(result.status, SearchStatus.EMPTY)

    async def test_no_fallback_on_insufficient_coverage(self):
        """INSUFFICIENT_COVERAGE не триггерит fallback."""
        first = StubAdapter(
            AdapterResult(status=SearchStatus.INSUFFICIENT_COVERAGE)
        )
        second = StubAdapter(AdapterResult(status=SearchStatus.OK))

        chain = FallbackAdapter([first, second])
        result = await chain.search_hotels(None)

        self.assertEqual(result.status, SearchStatus.INSUFFICIENT_COVERAGE)

    async def test_all_adapters_fail(self):
        """Все адаптеры упали — возвращается последний результат."""
        first = StubAdapter(AdapterResult(status=SearchStatus.TIMEOUT))
        second = StubAdapter(AdapterResult(status=SearchStatus.RATE_LIMITED))

        chain = FallbackAdapter([first, second])
        result = await chain.search_hotels(None)

        self.assertEqual(result.status, SearchStatus.RATE_LIMITED)

    async def test_adapter_exception_triggers_fallback(self):
        """Исключение в адаптере триггерит fallback."""

        class BrokenAdapter(BaseAdapter):
            provider_name = "broken"

            async def search_hotels(self, query):
                raise RuntimeError("boom")

        first = BrokenAdapter()
        second = StubAdapter(AdapterResult(status=SearchStatus.OK))

        chain = FallbackAdapter([first, second])
        result = await chain.search_hotels(None)

        self.assertEqual(result.status, SearchStatus.OK)
        self.assertTrue(
            any("broken" in w for w in result.warnings),
            f"Expected broken adapter warning, got: {result.warnings}",
        )


class AdapterFactoryTests(unittest.TestCase):

    def test_synthetic_mode_returns_synthetic(self):
        """В режиме synthetic используется только синтетика."""
        settings = Settings(data_mode="synthetic")
        catalog = Catalog()
        factory = AdapterFactory(catalog, settings)
        adapter = factory.build()

        self.assertIsInstance(adapter, CompositeAdapter)

    def test_unknown_mode_is_rejected(self):
        """A typo must not silently enable fabricated inventory."""
        with self.assertRaises(ValueError):
            Settings(data_mode="unknown_mode")


class SettingsTests(unittest.TestCase):

    def test_defaults(self):
        settings = Settings()
        self.assertEqual(settings.data_mode, "synthetic")
        self.assertTrue(settings.is_synthetic_only)
        self.assertFalse(settings.allow_real_transport)
        self.assertFalse(settings.allow_real_hotels)

    def test_live_mode(self):
        settings = Settings(data_mode="live")
        self.assertFalse(settings.is_synthetic_only)
        self.assertTrue(settings.allow_real_transport)
        self.assertTrue(settings.allow_real_hotels)

    def test_live_transport_mode(self):
        with self.assertRaises(ValueError):
            Settings(data_mode="live_transport")

    def test_sandbox_mode(self):
        settings = Settings(data_mode="sandbox")
        self.assertFalse(settings.is_synthetic_only)
        self.assertFalse(settings.allow_real_transport)
        self.assertTrue(settings.allow_real_hotels)


if __name__ == "__main__":
    unittest.main(verbosity=2)
