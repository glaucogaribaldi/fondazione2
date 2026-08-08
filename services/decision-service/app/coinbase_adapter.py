import httpx
from datetime import datetime, UTC
from typing import Any
from .products import get_product_mapping

COINBASE_EXCHANGE_URL = "https://api.exchange.coinbase.com"

class CoinbasePublicAdapter:
    """
    Coinbase Public Market Data Integration Adapter (Blocker G5 / M5).
    Uses Coinbase Exchange API as an approved, deliberate deviation to enable 
    robust, unauthenticated public market data access without requiring private credentials.
    """
    def __init__(self, base_url: str = COINBASE_EXCHANGE_URL):
        self.base_url = base_url
        self.headers = {"User-Agent": "Fondazione2/1.0.0"}

    def map_symbol(self, symbol: str, proxy_to_usd: bool = False) -> str:
        """
        Maps standard symbol using the canonical ProductMapping contract (Blocker M5).
        """
        mapping = get_product_mapping(symbol)
        if proxy_to_usd and mapping.market_data_is_proxy:
            return mapping.market_data_product_id
        return mapping.execution_product_id

    async def get_product_metadata(self, symbol: str, proxy_to_usd: bool = False) -> dict[str, Any]:
        """
        Retrieves public product metadata for symbol discovery and specifications.
        """
        product_id = self.map_symbol(symbol, proxy_to_usd)
        url = f"{self.base_url}/products/{product_id}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, timeout=10.0)
            response.raise_for_status()
            return response.json()

    async def get_ticker(self, symbol: str, proxy_to_usd: bool = False) -> dict[str, Any]:
        """
        Fetches the public ticker for the mapped symbol from Coinbase, enforcing strict freshness (Blocker G5).
        """
        product_id = self.map_symbol(symbol, proxy_to_usd)
        url = f"{self.base_url}/products/{product_id}/ticker"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, timeout=10.0)
            response.raise_for_status()
            data = response.json()
        
        # Verify freshness of the ticker (Blocker G5: missing/invalid timestamp => stale/error)
        ticker_time_str = data.get("time")
        if not ticker_time_str:
            raise ValueError(f"Missing price timestamp for {symbol} ticker on Coinbase")
            
        try:
            # Strip trailing Z/offset for safety
            if ticker_time_str.endswith("Z"):
                ticker_time_str = ticker_time_str[:-1] + "+00:00"
            ticker_time = datetime.fromisoformat(ticker_time_str)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid price timestamp format on Coinbase ticker: {ticker_time_str}") from e

        now = datetime.now(UTC)
        age = (now - ticker_time).total_seconds()
        
        # Max market data age is 90 seconds
        is_fresh = abs(age) <= 90.0
        
        return {
            "product_id": product_id,
            "price": float(data["price"]),
            "bid": float(data["bid"]),
            "ask": float(data["ask"]),
            "time": ticker_time_str,
            "freshness_seconds": age,
            "is_fresh": is_fresh
        }

    async def get_candles(self, symbol: str, granularity: int = 300, proxy_to_usd: bool = False) -> list[list[Any]]:
        """
        Fetches public historical candles from Coinbase for the mapped symbol.
        granularity: 60, 300, 900, 3600, 21600, 86400 (seconds)
        """
        product_id = self.map_symbol(symbol, proxy_to_usd)
        url = f"{self.base_url}/products/{product_id}/candles"
        params = {"granularity": granularity}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=self.headers, timeout=10.0)
            response.raise_for_status()
            return response.json()
