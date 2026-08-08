import os
import unittest
import requests

class QuantDingerAuthTests(unittest.TestCase):

    def test_quantdinger_endpoint_auth(self):
        """
        N3: Verify unauthenticated requests are rejected (401/403)
        and an authorized read-only request succeeds.
        """
        url = os.getenv("QUANTDINGER_API_URL", "http://localhost:8082") + "/api/agent/v1/trading/canonical-ledger"
        token = os.getenv("QUANTDINGER_READ_TOKEN")
        
        # If no token is provided or we can't reach the endpoint, skip the test
        try:
            r = requests.get(url, timeout=2.0)
        except Exception:
            self.skipTest(f"QuantDinger API is not reachable at {url}")
            return
            
        # 1. Unauthenticated request -> should return 401 or 403
        self.assertIn(r.status_code, [401, 403], f"Unauthenticated request to {url} returned {r.status_code}")
        
        # 2. Invalid token request -> should return 401 or 403
        r_invalid = requests.get(url, headers={"Authorization": "Bearer qd_agent_invalid_token_123"}, timeout=5.0)
        self.assertIn(r_invalid.status_code, [401, 403], f"Invalid token request returned {r_invalid.status_code}")
        
        # 3. Authorized request -> should succeed with 200 (if token is provided)
        if token:
            r_valid = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=5.0)
            self.assertEqual(r_valid.status_code, 200, f"Authorized request with token failed: {r_valid.text}")
        else:
            print("Skipping authorized request test: QUANTDINGER_READ_TOKEN env var not set.")

if __name__ == "__main__":
    unittest.main()
