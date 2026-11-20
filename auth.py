"""Вход через Keycloak (OpenID Connect) и ролевая модель каталога.

Роли:
  * любой пользователь, вошедший в realm, — чтение;
  * клиентская роль KEYCLOAK_ADMIN_ROLE (по умолчанию catalog-admin) — изменение данных.

Изменяющие запросы (POST, PUT, PATCH, DELETE) по умолчанию доступны только администратору.
Исключения — READ_ONLY_POST_ENDPOINTS: POST-запросы, которые ничего не меняют.

Режим AUTH_MODE=disabled отключает вход и даёт всем права администратора.
Он нужен только для локальной разработки.
"""

import logging
import os
import time
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from flask import current_app, has_request_context, jsonify, redirect, request, session, url_for
from markupsafe import escape

log = logging.getLogger("auth")

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PUBLIC_ENDPOINTS = {"auth_login", "auth_callback", "auth_logout", "static"}
# POST-запросы, доступные читателю: проверка доступности экспортеров, без изменения данных.
READ_ONLY_POST_ENDPOINTS = {"api_check_metrics", "api_check_exporters_alive"}
# GET-запросы, доступные только администратору.
ADMIN_ONLY_ENDPOINTS = {"admin_page", "admin_get_config", "admin_debug_service", "admin_diagnose"}
TOKEN_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]
DEFAULT_SECRET_PLACEHOLDERS = {"change-me", "change-me-in-production"}


class AuthConfigError(RuntimeError):
    """Настройки входа неполные или небезопасные: приложение не должно стартовать."""


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


class Settings:
    def __init__(self):
        self.mode = _env("AUTH_MODE", "keycloak").lower()
        self.issuer = _env("KEYCLOAK_ISSUER").rstrip("/")
        self.client_id = _env("KEYCLOAK_CLIENT_ID")
        self.client_secret = _env("KEYCLOAK_CLIENT_SECRET")
        self.admin_role = _env("KEYCLOAK_ADMIN_ROLE", "catalog-admin")
        self.ca_bundle = _env("KEYCLOAK_CA_BUNDLE")
        self.public_url = _env("PUBLIC_URL").rstrip("/")
        self.secret_key = _env("FLASK_SECRET_KEY")
        self.cookie_secure = _env("SESSION_COOKIE_SECURE", "true").lower() not in ("0", "false", "no")
        try:
            self.session_hours = float(_env("SESSION_LIFETIME_HOURS", "8"))
        except ValueError:
            raise AuthConfigError("SESSION_LIFETIME_HOURS должен быть числом") from None
        self.client = None

    def validate(self):
        if self.mode not in ("keycloak", "disabled"):
            raise AuthConfigError("AUTH_MODE должен быть keycloak или disabled")
        if self.mode == "disabled":
            return
        missing = [name for name, value in (
            ("KEYCLOAK_ISSUER", self.issuer), ("KEYCLOAK_CLIENT_ID", self.client_id),
            ("KEYCLOAK_CLIENT_SECRET", self.client_secret), ("PUBLIC_URL", self.public_url),
            ("FLASK_SECRET_KEY", self.secret_key)) if not value]
        if missing:
            raise AuthConfigError("Не заданы переменные для входа через Keycloak: " + ", ".join(missing))
        if self.secret_key in DEFAULT_SECRET_PLACEHOLDERS or len(self.secret_key) < 32:
            raise AuthConfigError("FLASK_SECRET_KEY должен быть случайной строкой длиной от 32 символов")
        for name, value in (("KEYCLOAK_ISSUER", self.issuer), ("PUBLIC_URL", self.public_url)):
            parsed = urlsplit(value)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise AuthConfigError(name + " должен быть абсолютным http(s)-адресом")
        if self.ca_bundle and not os.path.isfile(self.ca_bundle):
            raise AuthConfigError("Файл KEYCLOAK_CA_BUNDLE не найден: " + self.ca_bundle)
        if self.session_hours <= 0:
            raise AuthConfigError("SESSION_LIFETIME_HOURS должен быть больше нуля")
        if urlsplit(self.issuer).scheme == "http" or urlsplit(self.public_url).scheme == "http":
            log.warning("Keycloak или портал настроены по HTTP: годится только для локальной проверки")


def _settings():
    return current_app.extensions["catalog_auth"]


def configure(app):
    """Прочитать настройки из окружения и подключить клиента Keycloak. Можно вызывать повторно."""
    settings = Settings()
    settings.validate()
    app.secret_key = settings.secret_key or os.urandom(32)
    app.config.update(
        SESSION_COOKIE_NAME="catalog_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.cookie_secure,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=settings.session_hours),
    )
    if settings.mode == "keycloak":
        from authlib.integrations.flask_client import OAuth
        oauth = OAuth(app)
        settings.client = oauth.register(
            "keycloak",
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            server_metadata_url=settings.issuer + "/.well-known/openid-configuration",
            client_kwargs={
                "scope": "openid profile email",
                "code_challenge_method": "S256",
                "verify": settings.ca_bundle or True,
                "default_timeout": 10,
            },
        )
        log.info("Вход через Keycloak: issuer=%s client=%s admin_role=%s",
                 settings.issuer, settings.client_id, settings.admin_role)
    else:
        log.warning("AUTH_MODE=disabled: вход отключён, у всех запросов права администратора. "
                    "Только для локальной разработки.")
    app.extensions["catalog_auth"] = settings
    return settings


def init_app(app):
    configure(app)
    app.before_request(_guard)
    app.add_url_rule("/auth/login", "auth_login", auth_login)
    app.add_url_rule("/auth/callback", "auth_callback", auth_callback)
    app.add_url_rule("/auth/logout", "auth_logout", auth_logout)
    app.add_url_rule("/api/me", "api_me", api_me)


# ── Текущий пользователь ──

def current_user():
    """Вошедший пользователь: {"login", "name", "is_admin"} или None."""
    if not has_request_context():
        return None
    settings = current_app.extensions.get("catalog_auth")
    if settings is None:
        return None
    if settings.mode == "disabled":
        return {"login": "local-dev", "name": "Локальный режим без входа", "is_admin": True}
    user = session.get("user")
    if not isinstance(user, dict) or user.get("exp", 0) < time.time():
        return None
    return user


def current_login():
    user = current_user()
    return (user or {}).get("login", "")


# ── Проверка доступа перед каждым запросом ──

def _is_public_request():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return True
    # Стили, скрипты и картинки из static/. HTML-страницы открываются только через свои маршруты.
    if request.endpoint == "static_files":
        path = (request.view_args or {}).get("path", "")
        return not path.lower().endswith((".html", ".htm"))
    return False


def _same_origin(settings):
    origin = request.headers.get("Origin")
    if origin is None:
        # Браузеры передают Origin во всех кросс-сайтовых POST, поэтому его отсутствие
        # означает не-браузерного клиента. Чужую сессионную cookie такой клиент не получит.
        return True
    expected = urlsplit(settings.public_url).netloc if settings.public_url else request.host
    return urlsplit(origin).netloc.lower() == expected.lower()


def _wants_json():
    return request.path.startswith("/api/")


def _guard():
    settings = _settings()
    if _is_public_request():
        return None
    if request.method in UNSAFE_METHODS and not _same_origin(settings):
        log.warning("Отклонён %s %s: Origin %s не совпадает с порталом",
                    request.method, request.path, request.headers.get("Origin"))
        return jsonify({"error": "Запрос с другого сайта отклонён"}), 403
    user = current_user()
    if user is None:
        if _wants_json():
            return jsonify({"error": "Требуется вход", "login_url": "/auth/login"}), 401
        return redirect(url_for("auth_login", next=_safe_next(request.full_path.rstrip("?"))))
    needs_admin = request.endpoint in ADMIN_ONLY_ENDPOINTS or (
        request.method in UNSAFE_METHODS and request.endpoint not in READ_ONLY_POST_ENDPOINTS)
    if needs_admin and not user.get("is_admin"):
        if _wants_json():
            return jsonify({"error": "Недостаточно прав: нужна роль " + settings.admin_role}), 403
        return _message_page("Недостаточно прав",
                             "Раздел доступен пользователям с ролью «" + settings.admin_role + "» в Keycloak.",
                             403)
    return None


# ── Вход и выход ──

def _safe_next(value):
    """Только относительный путь внутри портала, без открытого редиректа."""
    if not value or not value.startswith("/") or value.startswith("//") or value.startswith("/\\"):
        return "/"
    return value


def auth_login():
    settings = _settings()
    if settings.mode != "keycloak":
        return redirect("/")
    session["login_next"] = _safe_next(request.args.get("next"))
    return settings.client.authorize_redirect(settings.public_url + "/auth/callback")


def _roles(settings, access_token):
    """Клиентские роли из access token после проверки подписи, издателя и клиента."""
    from authlib.jose import JsonWebToken
    claims = JsonWebToken(TOKEN_ALGORITHMS).decode(
        access_token, key=settings.client.create_load_key(),
        claims_options={"iss": {"essential": True, "value": settings.issuer}, "exp": {"essential": True}})
    claims.validate(leeway=60)
    if claims.get("azp") != settings.client_id:
        raise ValueError("access token выдан другому клиенту")
    client_access = (claims.get("resource_access") or {}).get(settings.client_id) or {}
    return claims, set(client_access.get("roles") or [])


def auth_callback():
    settings = _settings()
    if settings.mode != "keycloak":
        return redirect("/")
    try:
        token = settings.client.authorize_access_token()
        access_claims, roles = _roles(settings, token["access_token"])
    except Exception as exc:
        detail = getattr(exc, "error", "") or getattr(exc, "description", "") or ""
        log.warning("Вход через Keycloak не удался: %s %s", type(exc).__name__, detail)
        return _message_page("Не удалось войти",
                             "Keycloak не подтвердил вход. Попробуйте ещё раз.", 401, retry=True)
    identity = token.get("userinfo") or {}
    login = (identity.get("preferred_username") or access_claims.get("preferred_username")
             or identity.get("sub") or access_claims.get("sub") or "")
    next_url = _safe_next(session.pop("login_next", "/"))
    session.clear()
    session.permanent = True
    session["user"] = {
        "login": login,
        "name": identity.get("name") or access_claims.get("name") or login,
        "is_admin": settings.admin_role in roles,
        "exp": int(time.time() + settings.session_hours * 3600),
    }
    session["id_token"] = token.get("id_token", "")
    log.info("Вход: %s, администратор: %s", login, "да" if settings.admin_role in roles else "нет")
    return redirect(next_url)


def auth_logout():
    settings = _settings()
    id_token = session.get("id_token", "")
    session.clear()
    if settings.mode != "keycloak":
        return redirect("/")
    try:
        end_session = settings.client.load_server_metadata().get("end_session_endpoint")
    except Exception as exc:
        log.warning("Нет адреса выхода Keycloak: %s", type(exc).__name__)
        end_session = None
    if not end_session:
        return redirect("/")
    params = {"client_id": settings.client_id, "post_logout_redirect_uri": settings.public_url + "/"}
    if id_token:
        params["id_token_hint"] = id_token
    return redirect(end_session + "?" + urlencode(params))


def api_me():
    settings = _settings()
    user = current_user() or {}
    return jsonify({
        "login": user.get("login", ""),
        "name": user.get("name", ""),
        "is_admin": bool(user.get("is_admin")),
        "admin_role": settings.admin_role,
        "auth_mode": settings.mode,
        "logout_url": "/auth/logout" if settings.mode == "keycloak" else None,
    })


def _message_page(title, text, status, retry=False):
    links = '<a href="/">На портал</a>'
    if retry:
        links = '<a href="/auth/login">Войти снова</a>'
    elif _settings().mode == "keycloak":
        links += ' · <a href="/auth/logout">Выйти</a>'
    html = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>" + str(escape(title)) + " · Каталог</title>"
        "<style>body{margin:0;background:#0c1020;color:#eef2ff;font:15px/1.6 system-ui,sans-serif}"
        "main{max-width:560px;margin:15vh auto;padding:0 20px}h1{font-size:24px;margin:0 0 8px}"
        "p{color:#a1acc5}a{color:#a6a5ff}</style></head><body><main>"
        "<h1>" + str(escape(title)) + "</h1><p>" + str(escape(text)) + "</p><p>" + links + "</p>"
        "</main></body></html>"
    )
    return html, status, {"Content-Type": "text/html; charset=utf-8"}
