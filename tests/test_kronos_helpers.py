import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "services" / "kronos" / "app" / "helpers.py"
SPEC = importlib.util.spec_from_file_location("kronos_main", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class KronosHelperTests(unittest.TestCase):
    def test_supported_timeframes(self):
        self.assertEqual(MODULE.timeframe_delta("5m").total_seconds(), 300)
        self.assertEqual(MODULE.timeframe_delta("1h").total_seconds(), 3600)

    def test_unsupported_timeframe(self):
        with self.assertRaises(ValueError):
            MODULE.timeframe_delta("weekly")


if __name__ == "__main__":
    unittest.main()