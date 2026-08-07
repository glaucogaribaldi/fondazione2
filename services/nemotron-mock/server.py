import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in {"/health", "/healthz"}:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        self.send_error(404)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        proposal = {
            "action": "HOLD",
            "allocation_pct": 0,
            "confidence": 0.5,
            "stop_loss_pct": None,
            "take_profit_pct": None,
            "reason_codes": ["MOCK_BACKEND"],
        }
        response = {
            "choices": [{"message": {"role": "assistant", "content": json.dumps(proposal)}}]
        }
        payload = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


ThreadingHTTPServer(("0.0.0.0", 30000), Handler).serve_forever()
