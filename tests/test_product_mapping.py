import unittest
from unittest.mock import MagicMock, patch
from app.products import (
    get_product_mapping, get_conversion_rate_to_usdc, registry, CoinbaseProduct, ProductMapping
)
from datetime import datetime, UTC

class ProductMappingTests(unittest.TestCase):

    def setUp(self):
        # Clear products in registry before each test
        registry._products.clear()
        registry._initialized = False

    @patch("httpx.get")
    def test_registry_dynamic_discovery(self, mock_get):
        """
        Test 1 & 2: Dynamic SPOT catalog discovery from Coinbase API
        and adding new products after refresh without code changes.
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "id": "BTC-USD",
                "base_currency": "BTC",
                "quote_currency": "USD",
                "status": "online"
            },
            {
                "id": "BTC-USDC",
                "base_currency": "BTC",
                "quote_currency": "USDC",
                "status": "online"
            },
            {
                "id": "USDT-USDC",
                "base_currency": "USDT",
                "quote_currency": "USDC",
                "status": "online"
            }
        ]
        mock_get.return_value = mock_response

        # Synchronize catalog
        registry.sync_universe()
        self.assertTrue(registry.is_initialized())
        
        # Verify BTC/USDC mapped generically to BTC-USD proxy (Test 4)
        mapping_btc = get_product_mapping("BTC/USDC")
        self.assertEqual(mapping_btc.canonical_symbol, "BTC/USDC")
        self.assertEqual(mapping_btc.execution_product_id, "BTC-USDC")
        self.assertEqual(mapping_btc.market_data_product_id, "BTC-USD")
        self.assertTrue(mapping_btc.market_data_is_proxy)

        # Verify USDT/USDC direct mapping (Test 5)
        mapping_usdt = get_product_mapping("USDT/USDC")
        self.assertEqual(mapping_usdt.canonical_symbol, "USDT/USDC")
        self.assertEqual(mapping_usdt.execution_product_id, "USDT-USDC")
        self.assertEqual(mapping_usdt.market_data_product_id, "USDT-USDC")
        self.assertFalse(mapping_usdt.market_data_is_proxy)

        # Test 2: Simulate a new product listing addition
        mock_response.json.return_value.append({
            "id": "NEWCOIN-USDC",
            "base_currency": "NEWCOIN",
            "quote_currency": "USDC",
            "status": "online"
        })
        mock_response.json.return_value.append({
            "id": "NEWCOIN-USD",
            "base_currency": "NEWCOIN",
            "quote_currency": "USD",
            "status": "online"
        })
        registry.sync_universe()

        # New product must be discovered automatically without restart
        mapping_new = get_product_mapping("NEWCOIN/USDC")
        self.assertEqual(mapping_new.canonical_symbol, "NEWCOIN/USDC")
        self.assertEqual(mapping_new.execution_product_id, "NEWCOIN-USDC")
        self.assertEqual(mapping_new.market_data_product_id, "NEWCOIN-USD")
        self.assertTrue(mapping_new.market_data_is_proxy)

    def test_disabled_product_becomes_non_executable(self):
        """
        Test 3: Disabled/delisted product becomes non-executable automatically.
        """
        p = CoinbaseProduct(
            product_id="TEST-USDC",
            product_type="SPOT",
            base_currency="TEST",
            quote_currency="USDC",
            canonical_asset="TEST",
            canonical_symbol="TEST/USDC",
            execution_product_id="TEST-USDC",
            market_data_product_id="TEST-USDC",
            market_data_is_proxy=False,
            is_disabled=False,
            trading_disabled=False,
            cancel_only=False,
            limit_only=False,
            post_only=False,
            base_increment=0.1,
            quote_increment=0.01,
            min_market_funds=1.0,
            market_data_eligible=True,
            paper_execution_eligible=True,
            updated_at=datetime.now(UTC)
        )
        registry._products["TEST-USDC"] = p
        
        # Verify it is initially eligible
        prod = registry.get_product("TEST/USDC")
        self.assertTrue(prod.paper_execution_eligible)

        # Update status to offline (WS status change)
        registry.update_product_status("TEST-USDC", {"status": "offline"})
        self.assertFalse(prod.paper_execution_eligible)
        self.assertEqual(prod.ineligibility_reason, "Product is offline/disabled")

    def test_direct_usdt_eurc_mappings(self):
        """
        Test 5 & 6: USDT-USDC and EURC-USDC direct mapping exceptions.
        """
        # Static check or populated check
        p1 = CoinbaseProduct(
            product_id="USDT-USDC",
            product_type="SPOT",
            base_currency="USDT",
            quote_currency="USDC",
            canonical_asset="USDT",
            canonical_symbol="USDT/USDC",
            execution_product_id="USDT-USDC",
            market_data_product_id="USDT-USDC",
            market_data_is_proxy=False,
            is_disabled=False,
            trading_disabled=False,
            cancel_only=False,
            limit_only=False,
            post_only=False,
            base_increment=0.1,
            quote_increment=0.01,
            min_market_funds=1.0,
            market_data_eligible=True,
            paper_execution_eligible=True,
            updated_at=datetime.now(UTC)
        )
        p2 = CoinbaseProduct(
            product_id="EURC-USDC",
            product_type="SPOT",
            base_currency="EURC",
            quote_currency="USDC",
            canonical_asset="EURC",
            canonical_symbol="EURC/USDC",
            execution_product_id="EURC-USDC",
            market_data_product_id="EURC-USDC",
            market_data_is_proxy=False,
            is_disabled=False,
            trading_disabled=False,
            cancel_only=False,
            limit_only=False,
            post_only=False,
            base_increment=0.1,
            quote_increment=0.01,
            min_market_funds=1.0,
            market_data_eligible=True,
            paper_execution_eligible=True,
            updated_at=datetime.now(UTC)
        )
        registry._products["USDT-USDC"] = p1
        registry._products["EURC-USDC"] = p2

        mapping1 = get_product_mapping("USDT/USDC")
        self.assertFalse(mapping1.market_data_is_proxy)
        self.assertEqual(mapping1.market_data_product_id, "USDT-USDC")

        mapping2 = get_product_mapping("EURC/USDC")
        self.assertFalse(mapping2.market_data_is_proxy)
        self.assertEqual(mapping2.market_data_product_id, "EURC-USDC")

    def test_arbitrary_quote_preserves_pair(self):
        """
        Test 7: Arbitrary non-USDC quote currencies (e.g. ASSET-EUR) preserve their actual pairs.
        """
        p = CoinbaseProduct(
            product_id="SOL-EUR",
            product_type="SPOT",
            base_currency="SOL",
            quote_currency="EUR",
            canonical_asset="SOL",
            canonical_symbol="SOL/EUR",
            execution_product_id="SOL-EUR",
            market_data_product_id="SOL-EUR",
            market_data_is_proxy=False,
            is_disabled=False,
            trading_disabled=False,
            cancel_only=False,
            limit_only=False,
            post_only=False,
            base_increment=0.1,
            quote_increment=0.01,
            min_market_funds=1.0,
            market_data_eligible=True,
            paper_execution_eligible=True,
            updated_at=datetime.now(UTC)
        )
        registry._products["SOL-EUR"] = p

        mapping = get_product_mapping("SOL/EUR")
        self.assertEqual(mapping.canonical_symbol, "SOL/EUR")
        self.assertEqual(mapping.execution_product_id, "SOL-EUR")
        self.assertEqual(mapping.market_data_product_id, "SOL-EUR")
        self.assertFalse(mapping.market_data_is_proxy)

    def test_multi_quote_conversions(self):
        """
        Test 13 & 14: Multi-quote graph conversion calculations.
        """
        # Define mock mark function returning price of EUR/USD = 1.08, and USDC/EUR inverse
        def mock_get_mark(pair: str) -> float | None:
            if pair == "EUR/USD":
                return 1.08
            if pair == "USD/EUR":
                return 1.0 / 1.08
            return None

        # EUR to USDC conversion
        rate = get_conversion_rate_to_usdc("EUR", mock_get_mark)
        self.assertAlmostEqual(rate, 1.08)

        # USDC is 1.0
        rate_usdc = get_conversion_rate_to_usdc("USDC", mock_get_mark)
        self.assertEqual(rate_usdc, 1.0)

        # Missing conversion returns None (Test 14)
        rate_missing = get_conversion_rate_to_usdc("GBP", mock_get_mark)
        self.assertIsNone(rate_missing)

if __name__ == "__main__":
    unittest.main()
