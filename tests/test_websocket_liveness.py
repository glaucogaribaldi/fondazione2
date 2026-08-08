import unittest
import asyncio
import json
import websockets
from unittest.mock import MagicMock, patch, AsyncMock
from app.products import registry, CoinbaseProduct
from app.websocket_service import (
    CoinbaseWebSocketService, WS_SEQUENCE_GAPS, WS_RECONNECTS,
    WS_DUPLICATES, WS_OUT_OF_ORDER
)
from datetime import datetime, UTC, timedelta

# Save original asyncio.sleep before any mocking to prevent mock recursion loop (Blocker T1)
ORIGINAL_SLEEP = asyncio.sleep

class FakeWebSocket:
    def __init__(self, service, id_label):
        self.service = service
        self.id_label = id_label
        self.closed = False
        self.sent_messages = []
        self.recv_queue = asyncio.Queue()

    async def send(self, msg):
        self.sent_messages.append(msg)

    async def recv(self):
        while not self.closed:
            try:
                return await asyncio.wait_for(self.recv_queue.get(), timeout=0.01)
            except asyncio.TimeoutError:
                continue
        raise websockets.ConnectionClosed(None, None)

    async def close(self, code=1000, reason=""):
        self.closed = True

class MockWebSocket:
    def __init__(self):
        self.closed = False
        self.sent_messages = []

    async def send(self, msg):
        self.sent_messages.append(msg)

    async def recv(self):
        if self.closed:
            raise websockets.ConnectionClosed(None, None)
        await asyncio.sleep(0.01)
        return '{"channel": "heartbeats", "sequence_num": 1}'

    async def close(self, code=1000, reason=""):
        self.closed = True

class TestWebSocketLiveness(unittest.TestCase):

    def test_sequence_gap_tracking(self):
        """
        Test 12 / R5: Sequence gap behavior is deterministic and observable.
        """
        service = CoinbaseWebSocketService()
        WS_SEQUENCE_GAPS._value.set(0.0)

        # First message (seq = 100)
        asyncio.run(service._handle_message('{"channel": "ticker", "sequence_num": 100}'))
        self.assertEqual(service.last_sequence_num, 100)

        # Gap (seq = 105, expected = 101, gap = 4)
        asyncio.run(service._handle_message('{"channel": "ticker", "sequence_num": 105}'))
        self.assertEqual(service.last_sequence_num, 105)
        self.assertEqual(WS_SEQUENCE_GAPS._value.get(), 4.0)

    def test_duplicate_message_discarded(self):
        """
        R5: Duplicate messages (seq == last_seq) must be discarded and metrics incremented.
        """
        service = CoinbaseWebSocketService()
        WS_DUPLICATES._value.set(0.0)

        # First message (seq = 200)
        asyncio.run(service._handle_message('{"channel": "ticker", "sequence_num": 200}'))
        self.assertEqual(service.last_sequence_num, 200)

        # Duplicate message (seq = 200)
        asyncio.run(service._handle_message('{"channel": "ticker", "sequence_num": 200}'))
        self.assertEqual(service.last_sequence_num, 200)
        self.assertEqual(WS_DUPLICATES._value.get(), 1.0)

    def test_out_of_order_message_discarded(self):
        """
        R5: Out-of-order messages (seq < last_seq) must be discarded and metrics incremented.
        """
        service = CoinbaseWebSocketService()
        WS_OUT_OF_ORDER._value.set(0.0)

        # First message (seq = 300)
        asyncio.run(service._handle_message('{"channel": "ticker", "sequence_num": 300}'))
        self.assertEqual(service.last_sequence_num, 300)

        # Out-of-order message (seq = 299)
        asyncio.run(service._handle_message('{"channel": "ticker", "sequence_num": 299}'))
        self.assertEqual(service.last_sequence_num, 300) # Sequence not brought backward
        self.assertEqual(WS_OUT_OF_ORDER._value.get(), 1.0)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("websockets.connect")
    def test_reconnect_exponential_backoff_integrated(self, mock_connect, mock_sleep):
        """
        T1: Comprehensive integration test showing that a stale heartbeat causes
        the watchdog to close websocket A, main loop exits recv, reconnects to websocket B,
        and resubscribes all normalized market data IDs.
        """
        registry._initialized = True
        p = CoinbaseProduct(
            product_id="BTC-USDC",
            product_type="SPOT",
            base_currency="BTC",
            quote_currency="USDC",
            canonical_asset="BTC",
            canonical_symbol="BTC/USDC",
            execution_product_id="BTC-USDC",
            market_data_product_id="BTC-USD",
            market_data_is_proxy=True,
            is_disabled=False,
            trading_disabled=False,
            cancel_only=False,
            limit_only=False,
            post_only=False,
            base_increment=0.00000001,
            quote_increment=0.01,
            min_market_funds=1.0,
            market_data_eligible=True,
            paper_execution_eligible=True,
            updated_at=datetime.now(UTC)
        )
        registry._products["BTC-USDC"] = p

        service = CoinbaseWebSocketService()
        WS_RECONNECTS._value.set(0.0)

        # We mock websockets.connect to yield two sequential fake sockets
        socket_a = FakeWebSocket(service, "Socket_A")
        socket_b = FakeWebSocket(service, "Socket_B")
        socket_gen = [socket_a, socket_b]

        class AsyncContextManagerMock:
            def __init__(self, ws):
                self.ws = ws
            async def __aenter__(self):
                return self.ws
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        def connect_side_effect(url):
            if socket_gen:
                ws = socket_gen.pop(0)
                return AsyncContextManagerMock(ws)
            service.running = False
            return AsyncContextManagerMock(FakeWebSocket(service, "Socket_C"))

        mock_connect.side_effect = connect_side_effect

        # Helper to simulate watchdog sleep and loop stop (avoiding mock recursion)
        async def mock_sleep_impl(secs):
            if len(socket_b.sent_messages) > 0:
                service.running = False
            await ORIGINAL_SLEEP(0.001)

        mock_sleep.side_effect = mock_sleep_impl

        # Start the service main loop
        service.running = True

        async def run_test():
            main_task = asyncio.create_task(service._main_loop())
            await asyncio.sleep(0.05)
            self.assertEqual(service.ws.id_label, "Socket_A")
            self.assertGreater(len(socket_a.sent_messages), 0)

            # Verify subscription includes the normalized market-data ID "BTC-USD"
            btc_sub_found = any("BTC-USD" in m for m in socket_a.sent_messages)
            self.assertTrue(btc_sub_found)

            # Simulate stale heartbeat
            service.last_heartbeat_time = datetime.now(UTC) - timedelta(seconds=30)

            # Run a single watchdog check
            await service._watchdog_loop()

            # Watchdog must have closed Socket_A
            self.assertTrue(socket_a.closed)

            # Give main loop a moment to reconnect and subscribe on Socket_B
            await asyncio.sleep(0.05)

            self.assertEqual(service.ws.id_label, "Socket_B")
            self.assertGreater(len(socket_b.sent_messages), 0)

            # Verify Socket_B also received the resubscriptions for "BTC-USD"
            btc_sub_b_found = any("BTC-USD" in m for m in socket_b.sent_messages)
            self.assertTrue(btc_sub_b_found)

            # Stop service
            service.running = False
            main_task.cancel()
            try:
                await main_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_test())

    @patch("asyncio.sleep", new_callable=AsyncMock)
    def test_heartbeat_timeout_watchdog_forces_reconnect(self, mock_sleep):
        """
        R4: Watchdog must physically close the active websocket to force reconnection.
        """
        service = CoinbaseWebSocketService()
        service.running = True
        mock_ws = MockWebSocket()
        service.ws = mock_ws

        # Set last heartbeat time to 30 seconds ago (timeout limit is 25s)
        service.last_heartbeat_time = datetime.now(UTC) - timedelta(seconds=30)

        # Stop loop after first execution step
        async def mock_sleep_impl(secs):
            service.running = False
        mock_sleep.side_effect = mock_sleep_impl

        # Run watchdog step
        asyncio.run(service._watchdog_loop())
        
        # Watchdog must have closed the websocket to force a reconnect!
        self.assertTrue(mock_ws.closed)
        self.assertIsNone(service.last_heartbeat_time)

if __name__ == "__main__":
    unittest.main()
