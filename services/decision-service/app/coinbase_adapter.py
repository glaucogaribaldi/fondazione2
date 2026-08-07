import httpx
from datetime import datetime, UTC
from typing import Any

COINBASE_PUBLIC_URL = "https://api.exchange.coinbase.com"

class CoinbasePublicAdapter:
    def __init__(self, base_url: str = COINBASE_PUBLIC_URL):
        self.base_url = base_url
        self.headers = {"User-Agent": "Fondazione2/1.0.0"}

    def map_symbol(self, symbol: str, proxy_to_usd: bool = False) -> str:
        """
        Maps a standard symbol like BTC/USDC to Coinbase product ID like BTC-USDC or BTC-USD (if proxy_to_usd is True).
        """
        mapped = symbol.replace("/", "-")
        if proxy_to_usd and mapped.endswith("-USDC"):
            mapped = mapped[:-5] + "-USD"
        return mapped

    async def get_ticker(self, symbol: str, proxy_to_usd: bool = False) -> dict[str, Any]:
        """
        Fetches the public ticker for the mapped symbol from Coinbase.
        """
        product_id = self.map_symbol(symbol, proxy_to_usd)
        url = f"{self.base_url}/products/{product_id}/ticker"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, timeout=10.0)
            response.raise_for_status()
            data = response.json()
        
        # Verify freshness of the ticker
        # Coinbase exchange ticker returns 'time' in ISO format
        ticker_time_str = data.get("time")
        if ticker_time_str:
            # Strip trailing Z/offset for safety
            if ticker_time_str.endswith("Z"):
                ticker_time_str = ticker_time_str[:-1] + "+00:00"
            ticker_time = datetime.fromisoformat(ticker_time_str)
            now = datetime.now(UTC)
            age = (now - ticker_time).total_seconds()
            data["freshness_seconds"] = age
            data["is_fresh"] = abs(age) <= 90.0
        else:
            data["freshness_seconds"] = 0.0
            data["is_fresh"] = True # assume fresh if no timestamp
            
        data["product_id"] = product_id
        return data

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
