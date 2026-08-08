import unittest
from app.products import get_product_mapping, ProductMapping

class ProductMappingTests(unittest.TestCase):

    def test_btc_mapping(self):
        mapping = get_product_mapping("BTC/USDC")
        self.assertEqual(mapping.canonical_symbol, "BTC/USDC")
        self.assertEqual(mapping.execution_product_id, "BTC-USDC")
        self.assertEqual(mapping.market_data_product_id, "BTC-USD")
        self.assertTrue(mapping.market_data_is_proxy)

    def test_eth_mapping(self):
        mapping = get_product_mapping("ETH/USDC")
        self.assertEqual(mapping.canonical_symbol, "ETH/USDC")
        self.assertEqual(mapping.execution_product_id, "ETH-USDC")
        self.assertEqual(mapping.market_data_product_id, "ETH-USD")
        self.assertTrue(mapping.market_data_is_proxy)

    def test_sol_mapping(self):
        mapping = get_product_mapping("SOL/USDC")
        self.assertEqual(mapping.canonical_symbol, "SOL/USDC")
        self.assertEqual(mapping.execution_product_id, "SOL-USDC")
        self.assertEqual(mapping.market_data_product_id, "SOL-USD")
        self.assertTrue(mapping.market_data_is_proxy)

    def test_fallback_mapping(self):
        mapping = get_product_mapping("ADA/USDC")
        self.assertEqual(mapping.canonical_symbol, "ADA/USDC")
        self.assertEqual(mapping.execution_product_id, "ADA-USDC")
        self.assertEqual(mapping.market_data_product_id, "ADA-USDC")
        self.assertFalse(mapping.market_data_is_proxy)

if __name__ == "__main__":
    unittest.main()
