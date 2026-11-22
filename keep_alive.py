from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging

logger = logging.getLogger(__name__)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"AnomalyAlert bot is running OK")

    def log_message(self, *args):
        pass


def keep_alive(port: int = 8080) -> None:
    server = HTTPServer(("0.0.0.0", port), Handler)
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info("Keep-alive server started on port %d", port)
