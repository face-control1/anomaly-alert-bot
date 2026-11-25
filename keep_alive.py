"""
Мини HTTP-сервер чтобы Render не усыплял процесс.
Бот сам пингует себя каждые 10 минут — не зависит от UptimeRobot.
Render сам выставляет переменную PORT (обычно 10000).
"""
import os
import json
import time
import urllib.request
from threading import Thread
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import logging
from health_state import get_health

logger = logging.getLogger(__name__)

SELF_PING_INTERVAL = 10 * 60  # 10 минут


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        from admin_api import handle_admin_request
        if handle_admin_request(self):
            return
        if self.path.startswith("/live"):
            body = b'{"status":"alive"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        state = get_health()
        now = time.time()
        last_success = state.get("last_success_at")
        startup_age = now - state["started_at"]
        healthy = bool(last_success and now - last_success < 300)
        starting = last_success is None and startup_age < 180
        status = "ok" if healthy else ("starting" if starting else "degraded")
        body = json.dumps({"status": status, **state}, ensure_ascii=False).encode("utf-8")
        self.send_response(200 if healthy or starting else 503)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        from admin_api import handle_admin_request
        if not handle_admin_request(self):
            self.send_error(404)

    def do_OPTIONS(self):
        from admin_api import handle_admin_request
        if not handle_admin_request(self):
            self.send_error(404)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

    def log_message(self, *args):
        pass  # подавить стандартные логи HTTP


def _self_ping_loop(url: str) -> None:
    """Пингует сам себя каждые 10 минут чтобы Render не усыпил процесс."""
    # Подождём первый раз — дадим серверу подняться
    time.sleep(60)
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                logger.debug("Self-ping OK: %d", resp.status)
        except Exception as e:
            logger.debug("Self-ping failed: %s", e)
        time.sleep(SELF_PING_INTERVAL)


def keep_alive(bot=None, loop=None) -> None:
    if bot is not None and loop is not None:
        from admin_api import configure_admin_api
        configure_admin_api(bot, loop)
    port = int(os.environ.get("PORT", 8080))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info("Keep-alive сервер запущен на порту %d", port)

    # Self-ping: бот сам не даёт себе заснуть
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if render_url:
        ping_url = render_url.rstrip("/") + "/"
        pt = Thread(target=_self_ping_loop, args=(ping_url,), daemon=True)
        pt.start()
        logger.info("Self-ping запущео: %s (каждые %d мин)", ping_url, SELF_PING_INTERVAL // 60)
    else:
        logger.info("RENDER_EXTERNAL_URL не задан — self-ping отключён (только UptimeRobot)")
