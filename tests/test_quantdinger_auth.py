import os
import unittest
import requests

class QuantDingerAuthTests(unittest.TestCase):

    def test_quantdinger_endpoint_auth(self):
        """
        O2: Verify unauthenticated requests are rejected (401/403)
        and an authorized read-only request succeeds. No skipping allowed.
        """
        url = os.getenv("QUANTDINGER_API_URL", "http://localhost:8082") + "/api/agent/v1/trading/canonical-ledger"
        token = os.getenv("QUANTDINGER_READ_TOKEN")
        
        # 1. Require QUANTDINGER_READ_TOKEN
        if not token:
            self.fail("QUANTDINGER_READ_TOKEN is missing or empty. This token is mandatory for certification.")

        # 2. Try to reach the API (do not skip if unreachable, fail instead)
        try:
            r = requests.get(url, timeout=5.0)
        except Exception as e:
            self.fail(f"QuantDinger API is not reachable at {url}: {e}")
            
        # 3. Unauthenticated request -> should return 401 or 403
        self.assertIn(r.status_code, [401, 403], f"Unauthenticated request to {url} returned {r.status_code}")
        
        # 4. Invalid token request -> should return 401 or 403
        try:
            r_invalid = requests.get(url, headers={"Authorization": f"Bearer qd_agent_invalid_token_123"}, timeout=5.0)
        except Exception as e:
            self.fail(f"Failed to query {url} with invalid token: {e}")
        self.assertIn(r_invalid.status_code, [401, 403], f"Invalid token request returned {r_invalid.status_code}")
        
        # 5. Authorized request -> should succeed with 200
        try:
            r_valid = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=5.0)
        except Exception as e:
            self.fail(f"Failed to query {url} with valid token: {e}")
        self.assertEqual(r_valid.status_code, 200, f"Authorized request with token failed with {r_valid.status_code}: {r_valid.text}")

if __name__ == "__main__":
    unittest.main()
