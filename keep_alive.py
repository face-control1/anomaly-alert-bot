"""
Мини HTTP-сервер чтобы Render не усыплял процесс.
Бот сам пингует себя каждые 10 минут — не зависит от UptimeRobot.
Render сам выставляет переменную PORT (обычно 10000).
"""
import os
import time
import urllib.request
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging

logger = logging.getLogger(__name__)

SELF_PING_INTERVAL = 10 * 60  # 10 минут


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"AnomalyAlert bot is running OK")

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


def keep_alive() -> None:
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
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
