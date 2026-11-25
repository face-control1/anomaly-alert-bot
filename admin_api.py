"""Authenticated JSON API used by the MOEX HELP web admin panel."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from health_state import get_health
from database.db import (
    activate_paid,
    deactivate_subscription,
    disable_promo_code,
    create_admin_session,
    ensure_admin_owner,
    authenticate_admin_staff,
    verify_admin_session_info,
    list_admin_staff,
    create_admin_staff,
    update_admin_staff,
    change_admin_password,
    get_admin_audit,
    get_all_active_user_ids,
    get_all_users,
    get_promo_codes,
    get_recent_payments,
    get_recent_alerts,
    get_runtime_config,
    get_signal_performance,
    get_stats,
    grant_trial,
    log_admin_action,
    set_app_config,
    upsert_promo_code,
    verify_admin_session,
    revoke_admin_session,
)

logger = logging.getLogger(__name__)
ALLOWED_ORIGINS = {
    "https://moexhelp.trimmount.chatgpt.site",
    "https://moexhelp.ru",
    "https://www.moexhelp.ru",
    "http://moexhelp.ru",
    "http://www.moexhelp.ru",
    "http://moexhelp.ru.swtest.ru",
    "https://moexhelp.ru.swtest.ru",
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


def _admin_info(handler) -> dict | None:
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return verify_admin_session_info(auth[7:].strip())


def _allowed(role: str, path: str, method: str) -> bool:
    if role == "owner":
        return True
    if path.startswith("/api/admin/staff"):
        return False
    if path in {"/api/admin/config", "/api/admin/promo", "/api/admin/promo/disable", "/api/admin/broadcast"}:
        return role == "admin"
    if path in {"/api/admin/trial", "/api/admin/subscription", "/api/admin/deactivate", "/api/admin/message"}:
        return role in {"admin", "support"}
    return method == "GET" or path in {"/api/admin/logout", "/api/admin/password"}


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


async def _send_direct(user_id: int, text: str) -> None:
    if _bot is None:
        raise RuntimeError("Bot runtime unavailable")
    await _bot.send_message(user_id, text, parse_mode="HTML")


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
            ensure_admin_owner(expected_user, expected_password)
            staff = authenticate_admin_staff(username, password)
            if not staff:
                _send(handler, 401, {"error": "invalid_credentials"})
                return True
            admin_id = int(staff["id"])
            token = create_admin_session(admin_id, username=staff["username"], role=staff["role"])
            log_admin_action(admin_id, "web_login", "Web admin session created")
            _send(handler, 200, {"token": token, "admin_id": admin_id, "username": staff["username"], "role": staff["role"], "expires_in": 43200})
        except Exception:
            logger.exception("Admin login failed")
            _send(handler, 500, {"error": "server_error"})
        return True

    admin = _admin_info(handler)
    if not admin:
        _send(handler, 401, {"error": "unauthorized"})
        return True
    admin_id, role = int(admin["user_id"]), str(admin.get("role") or "analyst")
    if not _allowed(role, parsed.path, handler.command):
        _send(handler, 403, {"error": "forbidden"})
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
                "current_admin": {"username": admin.get("username"), "role": role},
            })
        elif handler.command == "GET" and parsed.path == "/api/admin/users":
            limit = min(int(parse_qs(parsed.query).get("limit", [100])[0]), 500)
            _send(handler, 200, {"users": get_all_users()[:limit]})
        elif handler.command == "GET" and parsed.path == "/api/admin/audit":
            _send(handler, 200, {"items": get_admin_audit(100)})
        elif handler.command == "GET" and parsed.path == "/api/admin/payments":
            _send(handler, 200, {"items": get_recent_payments(200)})
        elif handler.command == "GET" and parsed.path == "/api/admin/staff":
            _send(handler, 200, {"items": list_admin_staff()})
        elif handler.command == "POST" and parsed.path == "/api/admin/staff":
            data = _read_json(handler)
            create_admin_staff(str(data["username"]), str(data["password"]), str(data["role"]))
            log_admin_action(admin_id, "create_staff", f"username={data['username']}; role={data['role']}")
            _send(handler, 200, {"ok": True, "items": list_admin_staff()})
        elif handler.command == "POST" and parsed.path == "/api/admin/staff/update":
            data = _read_json(handler)
            update_admin_staff(int(data["id"]), password=data.get("password"), role=data.get("role"), active=data.get("active"))
            log_admin_action(admin_id, "update_staff", f"id={data['id']}")
            _send(handler, 200, {"ok": True, "items": list_admin_staff()})
        elif handler.command == "POST" and parsed.path == "/api/admin/password":
            data = _read_json(handler)
            if not change_admin_password(str(admin.get("username")), str(data["current_password"]), str(data["new_password"])):
                _send(handler, 400, {"error": "wrong_current_password"})
            else:
                log_admin_action(admin_id, "change_password", "Own password changed")
                _send(handler, 200, {"ok": True})
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
            ranges = {
                "subscription_price_stars": (1, 100000),
                "volume_multiplier": (1, 100),
                "price_threshold_pct": (0.1, 100),
                "bollinger_sigma": (0.5, 20),
                "min_signal_value_rub": (0, 10_000_000_000),
            }
            for key, value in data.items():
                if key not in allowed:
                    raise ValueError(f"Unknown config: {key}")
                if key in ranges and not ranges[key][0] <= float(value) <= ranges[key][1]:
                    raise ValueError(f"Out of range: {key}")
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
        elif handler.command == "POST" and parsed.path == "/api/admin/message":
            data = _read_json(handler)
            user_id = int(data["user_id"])
            text = str(data.get("text", "")).strip()
            if not 1 <= len(text) <= 3500:
                raise ValueError("Message length")
            if _loop is None:
                raise RuntimeError("Bot loop unavailable")
            asyncio.run_coroutine_threadsafe(_send_direct(user_id, text), _loop).result(timeout=30)
            log_admin_action(admin_id, "direct_message", f"user={user_id}; chars={len(text)}")
            _send(handler, 200, {"ok": True})
        elif handler.command == "POST" and parsed.path == "/api/admin/logout":
            auth = handler.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                revoke_admin_session(auth[7:].strip())
            log_admin_action(admin_id, "web_logout", "Admin session revoked")
            _send(handler, 200, {"ok": True})
        else:
            _send(handler, 404, {"error": "not_found"})
    except (KeyError, ValueError, TypeError) as exc:
        _send(handler, 400, {"error": "invalid_request", "detail": str(exc)})
    except Exception:
        logger.exception("Admin API error: %s", parsed.path)
        _send(handler, 500, {"error": "server_error"})
    return True
