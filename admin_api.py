"""Authenticated JSON API used by the MOEX HELP web admin panel."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from config import ADMIN_IDS
from health_state import get_health
from database.db import (
    activate_paid,
    deactivate_subscription,
    disable_promo_code,
    create_admin_session,
    get_admin_audit,
    get_all_active_user_ids,
    get_all_users,
    get_promo_codes,
    get_recent_alerts,
    get_runtime_config,
    get_signal_performance,
    get_stats,
    grant_trial,
    log_admin_action,
    set_app_config,
    upsert_promo_code,
    verify_admin_session,
)

logger = logging.getLogger(__name__)
ALLOWED_ORIGINS = {
    "https://moexhelp.trimmount.chatgpt.site",
    "https://moexhelp.ru",
    "https://www.moexhelp.ru",
}
_bot = None
_loop: asyncio.AbstractEventLoop | None = None
_login_attempts: dict[str, list[float]] = {}


def configure_admin_api(bot, loop: asyncio.AbstractEventLoop) -> None:
    global _bot, _loop
    _bot, _loop = bot, loop


def _json_safe(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _send(handler, status: int, payload: dict | list) -> None:
    body = json.dumps(_json_safe(payload), ensure_ascii=False).encode("utf-8")
    origin = handler.headers.get("Origin", "")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    if origin in ALLOWED_ORIGINS:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler) -> dict:
    length = min(int(handler.headers.get("Content-Length", "0") or 0), 64_000)
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def _admin_id(handler) -> int | None:
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    admin_id = verify_admin_session(auth[7:].strip())
    return admin_id if admin_id in ADMIN_IDS else None


def _rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [item for item in _login_attempts.get(ip, []) if now - item < 600]
    attempts.append(now)
    _login_attempts[ip] = attempts
    return len(attempts) > 8


async def _broadcast(text: str) -> dict:
    if _bot is None:
        raise RuntimeError("Bot runtime unavailable")
    sent = failed = 0
    for user_id in get_all_active_user_ids():
        try:
            await _bot.send_message(user_id, text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.035)
    return {"sent": sent, "failed": failed}


def handle_admin_request(handler) -> bool:
    parsed = urlparse(handler.path)
    if parsed.path == "/api/public/config" and handler.command in ("GET", "OPTIONS"):
        if handler.command == "OPTIONS":
            _send(handler, 204, {})
        else:
            config = get_runtime_config()
            _send(handler, 200, {
                "subscription_price_stars": config["subscription_price_stars"],
                "trial_hours": 24,
            })
        return True
    if not parsed.path.startswith("/api/admin/"):
        return False
    if handler.command == "OPTIONS":
        _send(handler, 204, {})
        return True

    if parsed.path == "/api/admin/login" and handler.command == "POST":
        ip = handler.client_address[0]
        if _rate_limited(ip):
            _send(handler, 429, {"error": "too_many_attempts"})
            return True
        try:
            data = _read_json(handler)
            expected_user = os.getenv("ADMIN_WEB_USERNAME", "admin")
            expected_password = os.getenv("ADMIN_WEB_PASSWORD", "")
            username = str(data.get("username", ""))
            password = str(data.get("password", ""))
            if not expected_password:
                _send(handler, 503, {"error": "web_login_not_configured"})
                return True
            if not (hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_password)):
                _send(handler, 401, {"error": "invalid_credentials"})
                return True
            admin_id = int(os.getenv("ADMIN_WEB_OWNER_ID", "5434913402"))
            if admin_id not in ADMIN_IDS:
                _send(handler, 403, {"error": "admin_not_allowed"})
                return True
            token = create_admin_session(admin_id)
            log_admin_action(admin_id, "web_login", "Web admin session created")
            _send(handler, 200, {"token": token, "admin_id": admin_id, "expires_in": 43200})
        except Exception:
            logger.exception("Admin login failed")
            _send(handler, 500, {"error": "server_error"})
        return True

    admin_id = _admin_id(handler)
    if not admin_id:
        _send(handler, 401, {"error": "unauthorized"})
        return True

    try:
        if handler.command == "GET" and parsed.path == "/api/admin/dashboard":
            _send(handler, 200, {
                "stats": get_stats(),
                "health": get_health(),
                "config": get_runtime_config(),
                "promos": get_promo_codes(),
                "performance": get_signal_performance(),
                "recent_alerts": get_recent_alerts(12),
            })
        elif handler.command == "GET" and parsed.path == "/api/admin/users":
            limit = min(int(parse_qs(parsed.query).get("limit", [100])[0]), 500)
            _send(handler, 200, {"users": get_all_users()[:limit]})
        elif handler.command == "GET" and parsed.path == "/api/admin/audit":
            _send(handler, 200, {"items": get_admin_audit(100)})
        elif handler.command == "POST" and parsed.path == "/api/admin/trial":
            data = _read_json(handler)
            user_id, hours = int(data["user_id"]), int(data.get("hours", 24))
            grant_trial(user_id, hours)
            log_admin_action(admin_id, "grant_trial", f"user={user_id}; hours={hours}")
            _send(handler, 200, {"ok": True})
        elif handler.command == "POST" and parsed.path == "/api/admin/subscription":
            data = _read_json(handler)
            user_id, months = int(data["user_id"]), int(data.get("months", 1))
            if months not in (1, 3, 6, 12):
                raise ValueError("Invalid months")
            activate_paid(user_id, months)
            log_admin_action(admin_id, "grant_subscription", f"user={user_id}; months={months}")
            _send(handler, 200, {"ok": True})
        elif handler.command == "POST" and parsed.path == "/api/admin/deactivate":
            user_id = int(_read_json(handler)["user_id"])
            deactivate_subscription(user_id)
            log_admin_action(admin_id, "deactivate_subscription", f"user={user_id}")
            _send(handler, 200, {"ok": True})
        elif handler.command == "POST" and parsed.path == "/api/admin/config":
            data = _read_json(handler)
            allowed = set(get_runtime_config())
            for key, value in data.items():
                if key not in allowed:
                    raise ValueError(f"Unknown config: {key}")
                set_app_config(key, str(value).lower() if isinstance(value, bool) else str(value))
            log_admin_action(admin_id, "update_config", json.dumps(data, ensure_ascii=False))
            _send(handler, 200, {"ok": True, "config": get_runtime_config()})
        elif handler.command == "POST" and parsed.path == "/api/admin/promo":
            data = _read_json(handler)
            upsert_promo_code(str(data["code"]), int(data["discount"]))
            log_admin_action(admin_id, "upsert_promo", f"{data['code']}:{data['discount']}")
            _send(handler, 200, {"ok": True, "promos": get_promo_codes()})
        elif handler.command == "POST" and parsed.path == "/api/admin/promo/disable":
            code = str(_read_json(handler)["code"])
            disable_promo_code(code)
            log_admin_action(admin_id, "disable_promo", code)
            _send(handler, 200, {"ok": True, "promos": get_promo_codes()})
        elif handler.command == "POST" and parsed.path == "/api/admin/broadcast":
            text = str(_read_json(handler).get("text", "")).strip()
            if not 1 <= len(text) <= 3500:
                raise ValueError("Broadcast length")
            if _loop is None:
                raise RuntimeError("Bot loop unavailable")
            result = asyncio.run_coroutine_threadsafe(_broadcast(text), _loop).result(timeout=300)
            log_admin_action(admin_id, "broadcast", f"sent={result['sent']}; failed={result['failed']}")
            _send(handler, 200, {"ok": True, **result})
        else:
            _send(handler, 404, {"error": "not_found"})
    except (KeyError, ValueError, TypeError) as exc:
        _send(handler, 400, {"error": "invalid_request", "detail": str(exc)})
    except Exception:
        logger.exception("Admin API error: %s", parsed.path)
        _send(handler, 500, {"error": "server_error"})
    return True
