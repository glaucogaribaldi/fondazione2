import os
import json
import asyncio
import threading
import traceback
import websockets
from datetime import datetime, UTC
from typing import Any
from prometheus_client import Counter, Gauge
from .products import registry, get_product_mapping
from .executor import PaperExecutor

# WebSocket Observability Metrics (Blocker K5 / G)
WS_CONNECTED = Gauge("foundation_ws_connection_state", "WebSocket connection state (1=connected, 0=disconnected)")
WS_RECONNECTS = Counter("foundation_ws_reconnects_total", "Total WebSocket reconnects")
WS_HEARTBEAT_AGE = Gauge("foundation_ws_heartbeat_age_seconds", "Heartbeat age in seconds")
WS_MESSAGES_RECEIVED = Counter("foundation_ws_messages_received_total", "Total WebSocket messages received")
WS_SEQUENCE_GAPS = Counter("foundation_ws_sequence_gaps_total", "Total WebSocket sequence gaps detected")
WS_NORMALIZATION_FAILURES = Counter("foundation_ws_normalization_failures_total", "Total proxy/normalization failures")
WS_STALE_PRODUCTS = Counter("foundation_ws_stale_products_total", "Total stale products detected")
QUOTE_CONVERSION_FAILURES = Counter("foundation_quote_conversion_failures_total", "Total quote-conversion failures")

class CoinbaseWebSocketService:
    def __init__(self, ws_url: str = "wss://advanced-trade-ws.coinbase.com"):
        self.ws_url = ws_url
        self.running = False
        self.last_heartbeat_time = None
        self.last_sequence_num = None
        self.executor = None
        self._task = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.executor = PaperExecutor()
        self._task = asyncio.create_task(self._main_loop())
        asyncio.create_task(self._watchdog_loop())

    def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()

    async def _main_loop(self):
        backoff = 1.0
        while self.running:
            try:
                # 1. Ensure registry is synchronized before subscribing
                if not registry.is_initialized():
                    print("WebSocket Service: Registry not initialized yet. Synchronizing...")
                    await asyncio.to_thread(registry.sync_universe)
                
                active_products = [p for p in registry.list_products() if p.market_data_eligible]
                product_ids = [p.product_id for p in active_products]
                
                if not product_ids:
                    print("WebSocket Service: No active market-data eligible products found. Retrying in 5s...")
                    await asyncio.sleep(5)
                    continue

                print(f"WebSocket Service: Connecting to {self.ws_url}...")
                WS_CONNECTED.set(0)
                async with websockets.connect(self.ws_url) as ws:
                    print("WebSocket Service: Connected successfully!")
                    WS_CONNECTED.set(1)
                    backoff = 1.0  # Reset backoff on successful connection
                    
                    # 2. Subscribe to channels in batches/shards (Blocker P1 / C)
                    await self._subscribe_all(ws, product_ids)

                    # 3. Read messages
                    while self.running:
                        try:
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=15.0)
                            WS_MESSAGES_RECEIVED.inc()
                            await self._handle_message(raw_msg)
                        except asyncio.TimeoutError:
                            print("WebSocket Service: Receive timeout. Reconnecting...")
                            break
                        except Exception as e:
                            print(f"WebSocket Service: Error in receive loop: {e}")
                            break
            except Exception as e:
                print(f"WebSocket Service: Connection error: {e}")
                WS_CONNECTED.set(0)
                WS_RECONNECTS.inc()
                
            if self.running:
                # Bounded exponential backoff reconnect (Blocker C)
                print(f"WebSocket Service: Reconnecting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)

    async def _subscribe_all(self, ws, product_ids: list[str]):
        batch_size = 100
        print(f"WebSocket Service: Subscribing to status, ticker, heartbeats for {len(product_ids)} products in batches of {batch_size}...")
        
        # Batch subscribe to ticker
        for i in range(0, len(product_ids), batch_size):
            batch = product_ids[i:i+batch_size]
            ticker_sub = {
                "type": "subscribe",
                "product_ids": batch,
                "channel": "ticker"
            }
            await ws.send(json.dumps(ticker_sub))
            await asyncio.sleep(0.05)
            
        # Batch subscribe to status
        for i in range(0, len(product_ids), batch_size):
            batch = product_ids[i:i+batch_size]
            status_sub = {
                "type": "subscribe",
                "product_ids": batch,
                "channel": "status"
            }
            await ws.send(json.dumps(status_sub))
            await asyncio.sleep(0.05)

        # Subscribe to heartbeats
        if product_ids:
            hb_sub = {
                "type": "subscribe",
                "product_ids": [product_ids[0]],
                "channel": "heartbeats"
            }
            await ws.send(json.dumps(hb_sub))

    async def _handle_message(self, raw_msg: str):
        try:
            msg = json.loads(raw_msg)
        except Exception as e:
            print(f"WebSocket Service: Malformed json message: {e}")
            return

        channel = msg.get("channel")
        seq_num = msg.get("sequence_num")
        
        # Observable sequence gap tracking (Blocker C)
        if seq_num is not None:
            if self.last_sequence_num is not None:
                expected = self.last_sequence_num + 1
                if seq_num > expected:
                    gap = seq_num - expected
                    print(f"WebSocket Service: Sequence gap detected! Expected {expected}, got {seq_num} (gap size: {gap})")
                    WS_SEQUENCE_GAPS.inc(gap)
            self.last_sequence_num = seq_num

        if channel == "heartbeats":
            self.last_heartbeat_time = datetime.now(UTC)
            WS_HEARTBEAT_AGE.set(0.0)
            
        elif channel == "status":
            events = msg.get("events", [])
            for ev in events:
                if ev.get("type") in ["snapshot", "update"]:
                    products = ev.get("products", [])
                    for prod in products:
                        await self._handle_status_update(prod)

        elif channel == "ticker":
            events = msg.get("events", [])
            for ev in events:
                if ev.get("type") in ["snapshot", "update"]:
                    tickers = ev.get("tickers", [])
                    for tick in tickers:
                        await self._handle_ticker_update(tick)

    async def _handle_status_update(self, prod: dict[str, Any]):
        product_id = prod.get("id")
        if not product_id:
            return
        
        status = prod.get("status", "online")
        is_disabled = status != "online"
        
        # Update product status dynamically in the registry (Blocker B / C)
        registry.update_product_status(product_id, prod)

    async def _handle_ticker_update(self, tick: dict[str, Any]):
        product_id = tick.get("product_id")
        price_str = tick.get("price")
        if not product_id or not price_str:
            return

        try:
            price = float(price_str)
        except ValueError:
            return

        # Normalization routing check (Blocker D): find all canonical pairs pointing to this market_data_product_id
        mapped_symbols = registry.get_canonical_symbols_for_market_data(product_id)
        if not mapped_symbols:
            WS_NORMALIZATION_FAILURES.inc()
            return

        for symbol in mapped_symbols:
            try:
                # Update database market marks, triggering live MTM recalculations and snapshots! (Blocker P1 / O1 / F)
                await asyncio.to_thread(self.executor.update_market_mark, symbol, price)
            except Exception as e:
                print(f"WebSocket Service: Failed to update market mark for {symbol}: {e}")

    async def _watchdog_loop(self):
        """
        Liveness watchdog monitoring heartbeat timeout (Blocker C).
        """
        while self.running:
            await asyncio.sleep(5)
            if self.last_heartbeat_time:
                age = (datetime.now(UTC) - self.last_heartbeat_time).total_seconds()
                WS_HEARTBEAT_AGE.set(age)
                # Heartbeat timeout is 25 seconds (since Coinbase sends hb every 1s-10s)
                if age > 25.0:
                    print(f"WebSocket Service Watchdog: Heartbeat is stale (age: {age:.1f}s > 25s). Marking unhealthy!")
                    WS_STALE_PRODUCTS.inc()
                    # Trigger reconnect by closing connection (unhealthy path)
                    self.last_heartbeat_time = None
                    # We don't exit, the main loop recv timeout will catch it or we can force disconnect

# Singleton instance of the WebSocket Service
websocket_service = CoinbaseWebSocketService()
