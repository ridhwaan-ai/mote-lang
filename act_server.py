"""Records what is posted to it. python act_server.py <port>. POST anything (stored as the last body); GET /last returns it."""
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
last = {"body": None, "count": 0}
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        last["body"] = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"); last["count"] += 1
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
    def do_GET(self):
        b = (last["body"] or "null").encode("utf-8"); self.send_response(200); self.send_header("X-Count", str(last["count"])); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
if __name__ == "__main__": ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
