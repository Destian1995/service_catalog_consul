"""Вход через Keycloak и роли: читатель видит данные, менять может только администратор.

Keycloak и Consul не вызываются: метаданные, ключи и токены подменяются в тестах.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import auth

ISSUER = "https://keycloak.test/realms/catalog"
CLIENT_ID = "service-catalog"
PUBLIC_URL = "https://catalog.test"
KEYCLOAK_ENV = {
    "AUTH_MODE": "keycloak",
    "KEYCLOAK_ISSUER": ISSUER,
    "KEYCLOAK_CLIENT_ID": CLIENT_ID,
    "KEYCLOAK_CLIENT_SECRET": "client-secret",
    "KEYCLOAK_ADMIN_ROLE": "catalog-admin",
    "KEYCLOAK_CA_BUNDLE": "",
    "PUBLIC_URL": PUBLIC_URL,
    "FLASK_SECRET_KEY": "s" * 40,
    "SESSION_LIFETIME_HOURS": "8",
    "SESSION_COOKIE_SECURE": "true",
    "CONSUL_AUDIT_FILES": "",
}
METADATA = {
    "issuer": ISSUER,
    "authorization_endpoint": ISSUER + "/protocol/openid-connect/auth",
    "token_endpoint": ISSUER + "/protocol/openid-connect/token",
    "jwks_uri": ISSUER + "/protocol/openid-connect/certs",
    "end_session_endpoint": ISSUER + "/protocol/openid-connect/logout",
    "id_token_signing_alg_values_supported": ["RS256"],
    "_loaded_at": 0,
}
REPO = Path(__file__).resolve().parent


def login(client, admin):
    with client.session_transaction() as state:
        state["user"] = {"login": "admin1" if admin else "reader1", "name": "Тест",
                         "is_admin": admin, "exp": time.time() + 600}
        state["id_token"] = "id-token-value"


class SettingsTests(unittest.TestCase):
    def settings(self, **overrides):
        env = dict(KEYCLOAK_ENV, **overrides)
        with patch.dict(os.environ, env):
            settings = auth.Settings()
            settings.validate()
            return settings

    def test_keycloak_mode_requires_complete_settings(self):
        for name in ("KEYCLOAK_ISSUER", "KEYCLOAK_CLIENT_ID", "KEYCLOAK_CLIENT_SECRET", "PUBLIC_URL", "FLASK_SECRET_KEY"):
            with self.subTest(name=name), self.assertRaises(auth.AuthConfigError) as error:
                self.settings(**{name: ""})
            self.assertIn(name, str(error.exception))

    def test_rejects_weak_secret_unknown_mode_and_missing_ca(self):
        for overrides in ({"FLASK_SECRET_KEY": "change-me-in-production"}, {"FLASK_SECRET_KEY": "short"},
                          {"AUTH_MODE": "basic"}, {"KEYCLOAK_CA_BUNDLE": "/nonexistent/ca.pem"},
                          {"PUBLIC_URL": "catalog.test"}):
            with self.subTest(overrides=overrides), self.assertRaises(auth.AuthConfigError):
                self.settings(**overrides)

    def test_disabled_mode_grants_admin_for_local_development(self):
        from flask import Flask
        app = Flask(__name__)
        app.add_url_rule("/api/change", "change", lambda: "ok", methods=["POST"])
        with patch.dict(os.environ, {"AUTH_MODE": "disabled", "FLASK_SECRET_KEY": ""}):
            auth.init_app(app)
        client = app.test_client()
        self.assertTrue(client.get("/api/me").json["is_admin"])
        self.assertEqual(client.post("/api/change").status_code, 200)


class PortalAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.config_path = root / "config.json"
        shutil.copy(REPO / "consul_manager_config.json", cls.config_path)
        cls.env = patch.dict(os.environ, dict(KEYCLOAK_ENV, CONSUL_JOURNAL_DB=str(root / "journal.db")))
        cls.env.start()
        with patch("consul_client.start_cache_warmer"):
            import app as portal
        cls.portal = portal
        cls.settings = auth.configure(portal.app)
        cls.patches = [
            patch.object(portal, "CONFIG_PATH", str(cls.config_path)),
            patch.object(portal, "HISTORY_PATH", str(root / "history.json")),
            patch.object(portal, "OWNERS_PATH", str(root / "owners.json")),
            patch.object(portal, "ARCH_PATH", str(root / "architecture.json")),
        ]
        for item in cls.patches:
            item.start()

    @classmethod
    def tearDownClass(cls):
        for item in cls.patches:
            item.stop()
        cls.env.stop()
        cls.portal._config_cache["data"] = None
        cls.tmp.cleanup()

    def setUp(self):
        self.portal._config_cache["data"] = None
        self.client = self.portal.app.test_client()

    def config(self):
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def write_config(self, cfg):
        self.config_path.write_text(json.dumps(cfg), encoding="utf-8")
        self.portal._config_cache["data"] = None

    def test_anonymous_gets_401_for_api_and_login_redirect_for_pages(self):
        self.assertEqual(self.client.get("/api/nodes").status_code, 401)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        for path, next_path in (("/", "/"), ("/admin", "/admin"), ("/deregistrations", "/deregistrations")):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302, path)
            location = urlsplit(response.headers["Location"])
            self.assertEqual(location.path, "/auth/login")
            self.assertEqual(parse_qs(location.query)["next"], [next_path])
        self.assertEqual(self.client.get("/css/style.css").status_code, 200)
        self.assertEqual(self.client.get("/static/js/auth.js").status_code, 200)

    def test_reader_reads_but_cannot_change(self):
        login(self.client, admin=False)
        before = self.config()
        self.assertEqual(self.client.get("/api/nodes").status_code, 200)
        self.assertEqual(self.client.get("/api/admin/excluded-systems").status_code, 200)
        me = self.client.get("/api/me").json
        self.assertEqual((me["login"], me["is_admin"]), ("reader1", False))
        writes = [
            ("post", "/api/nodes/prod-web-01/deregister", None),
            ("post", "/api/owner_assignments", {}),
            ("post", "/api/admin/toggle-monitored-system", {"system_name": "ERP"}),
            ("post", "/api/admin/toggle-excluded-system", {"system_name": "ERP"}),
            ("post", "/api/admin/expected-exporters", {"expected": {}}),
            ("post", "/api/admin/inventory-excluded", {"systems": []}),
            ("post", "/api/admin/sla-suppress", {"rules": []}),
            ("post", "/api/architecture", {"nodes": [], "links": []}),
            ("post", "/api/admin/config", {"config": {}}),
            ("post", "/api/admin/mode", {"mode": "live"}),
            ("post", "/api/admin/cluster", {"cluster": {"name": "x"}}),
            ("delete", "/api/admin/cluster/infra-prod", None),
            ("post", "/api/admin/monitored-systems", {"systems": []}),
            ("post", "/api/admin/test-connection", {"host": "consul.test"}),
        ]
        for method, path, body in writes:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, json=body)
                self.assertEqual(response.status_code, 403)
                self.assertIn("catalog-admin", response.json["error"])
        for path in ("/api/admin/config", "/api/admin/diagnose", "/api/admin/debug-service/x"):
            self.assertEqual(self.client.get(path).status_code, 403, path)
        page = self.client.get("/admin")
        self.assertEqual(page.status_code, 403)
        self.assertIn("catalog-admin", page.get_data(as_text=True))
        self.assertEqual(self.config(), before)

    def test_every_write_route_is_admin_only_by_default(self):
        login(self.client, admin=False)
        checked = 0
        for rule in self.portal.app.url_map.iter_rules():
            unsafe = rule.methods & auth.UNSAFE_METHODS
            if not unsafe or rule.endpoint in auth.READ_ONLY_POST_ENDPOINTS:
                continue
            url = rule.rule.replace("<path:path>", "x")
            for arg in rule.arguments:
                url = url.replace("<" + arg + ">", "x")
            for method in unsafe:
                with self.subTest(rule=rule.rule, method=method):
                    self.assertEqual(self.client.open(url, method=method, json={}).status_code, 403)
                    checked += 1
        self.assertGreaterEqual(checked, 14)  # На 20.11.2026 в портале 14 изменяющих маршрутов.

    def test_reader_can_probe_exporter_metrics(self):
        login(self.client, admin=False)
        reply = MagicMock(status_code=200, text="node_cpu_seconds_total 1\n")
        with patch.object(self.portal.http_requests, "get", return_value=reply):
            response = self.client.post("/api/check-metrics", json={"host": "srv", "port": 9100, "service": "node-exporter"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["all_expected_found"])

    def test_admin_change_is_saved_with_login(self):
        login(self.client, admin=True)
        response = self.client.post("/api/admin/toggle-excluded-system", json={"system_name": "ERP"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("ERP", self.config()["excluded_systems"])
        entry = self.portal._load_history()[-1]
        self.assertEqual((entry["action"], entry["user"]), ("exclude_toggle", "admin1"))
        self.client.post("/api/admin/toggle-excluded-system", json={"system_name": "ERP"})
        self.assertNotIn("ERP", self.config()["excluded_systems"])
        self.assertTrue(self.client.post("/api/nodes/prod-web-01/deregister").json["ok"])

    def test_admin_config_hides_legacy_password_and_tokens(self):
        cfg = self.config()
        cfg["app"].update(admin_password="admin", secret_key="old")
        cfg["clusters"]["infra-prod"]["datacenters"][0]["token"] = "0123456789-secret-token"
        self.write_config(cfg)
        try:
            login(self.client, admin=True)
            body = self.client.get("/api/admin/config").get_data(as_text=True)
            self.assertNotIn("admin_password", body)
            self.assertNotIn("secret-token", body)
        finally:
            cfg["app"].pop("admin_password"); cfg["app"].pop("secret_key")
            cfg["clusters"]["infra-prod"]["datacenters"][0]["token"] = ""
            self.write_config(cfg)

    def test_cross_site_post_is_rejected_even_for_admin(self):
        login(self.client, admin=True)
        hostile = self.client.post("/api/admin/toggle-excluded-system", json={"system_name": "ERP"},
                                   headers={"Origin": "https://evil.test"})
        self.assertEqual(hostile.status_code, 403)
        self.assertNotIn("ERP", self.config().get("excluded_systems", []))
        for origin in ("null", "https://catalog.test.evil.test"):
            response = self.client.post("/api/nodes/x/deregister", headers={"Origin": origin})
            self.assertEqual(response.status_code, 403, origin)
        same = self.client.post("/api/admin/toggle-monitored-system", json={"system_name": "ERP"},
                                headers={"Origin": PUBLIC_URL})
        self.assertEqual(same.status_code, 200)
        self.client.post("/api/admin/toggle-monitored-system", json={"system_name": "ERP"})

    def test_expired_session_requires_new_login(self):
        with self.client.session_transaction() as state:
            state["user"] = {"login": "admin1", "name": "Тест", "is_admin": True, "exp": time.time() - 1}
        self.assertEqual(self.client.get("/api/nodes").status_code, 401)

    def test_html_files_open_only_through_their_routes(self):
        login(self.client, admin=False)
        for path in ("/admin.html", "/index.html", "/deregistrations.html"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_live_deregistration_records_portal_user(self):
        cfg = self.config()
        live = dict(cfg, mode="live", clusters={"c": {"name": "c", "datacenters": [
            {"name": "1xx", "host": "consul.test", "token": "", "scheme": "https", "verify_ssl": True}]}})
        self.write_config(live)
        node = {"ID": "1", "Node": "uknrd101", "Address": "10.0.0.1", "Datacenter": "1xx", "Meta": {}}
        try:
            login(self.client, admin=True)
            with patch("consul_client.ConsulClient.get_nodes", return_value=[node]), \
                    patch("consul_client.requests.put") as send:
                send.return_value.status_code = 200
                self.assertEqual(self.client.post("/api/nodes/uknrd101/deregister").status_code, 200)
            from deregistration_journal import journal_data
            event = journal_data(node="uknrd101")["events"][0]
            self.assertEqual((event["actor"], event["outcome"]), ("admin1", "http_ok"))
            self.assertEqual(journal_data(query="admin1")["events"][0]["node_name"], "uknrd101")
        finally:
            self.write_config(cfg)

    # ── Вход через Keycloak ──

    def start_login(self, next_path="/admin"):
        with patch.object(self.settings.client, "load_server_metadata", return_value=METADATA):
            response = self.client.get("/auth/login", query_string={"next": next_path})
        self.assertEqual(response.status_code, 302)
        return response

    def test_login_redirect_uses_pkce_and_ignores_external_next(self):
        location = urlsplit(self.start_login("//evil.test/").headers["Location"])
        query = parse_qs(location.query)
        self.assertEqual(location.scheme + "://" + location.netloc + location.path, METADATA["authorization_endpoint"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], [PUBLIC_URL + "/auth/callback"])
        self.assertEqual(query["client_id"], [CLIENT_ID])
        self.assertIn("nonce", query)
        with self.client.session_transaction() as state:
            self.assertEqual(state["login_next"], "/")

    def complete_login(self, access_overrides=None, sign_with=None):
        from authlib.jose import JsonWebKey, jwt
        key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
        public = dict(key.as_dict(is_private=False), use="sig", alg="RS256")
        signer = sign_with or key
        query = parse_qs(urlsplit(self.start_login().headers["Location"]).query)
        now = int(time.time())
        header = {"alg": "RS256", "kid": "k1"}
        # ID token всегда настоящий: проверяется именно разбор access token с ролями.
        id_token = jwt.encode(header, {"iss": ISSUER, "aud": CLIENT_ID, "azp": CLIENT_ID, "sub": "u-1", "iat": now,
                                       "exp": now + 300, "nonce": query["nonce"][0],
                                       "preferred_username": "admin1", "name": "Администратор Каталога"}, key)
        access = {"iss": ISSUER, "azp": CLIENT_ID, "sub": "u-1", "iat": now, "exp": now + 300,
                  "preferred_username": "admin1",
                  "resource_access": {CLIENT_ID: {"roles": ["catalog-admin"]}, "other": {"roles": ["x"]}}}
        access.update(access_overrides or {})
        token = {"access_token": jwt.encode(header, access, signer).decode(), "id_token": id_token.decode(),
                 "token_type": "Bearer", "expires_in": 300}
        client = self.settings.client
        with patch.object(client, "load_server_metadata", return_value=METADATA), \
                patch.object(client, "fetch_jwk_set", return_value={"keys": [public]}), \
                patch.object(client, "fetch_access_token", return_value=token):
            return self.client.get("/auth/callback", query_string={"code": "code-1", "state": query["state"][0]})

    def test_callback_logs_in_admin_from_client_role(self):
        response = self.complete_login()
        self.assertEqual((response.status_code, response.headers["Location"]), (302, "/admin"))
        me = self.client.get("/api/me").json
        self.assertEqual((me["login"], me["name"], me["is_admin"]), ("admin1", "Администратор Каталога", True))
        cookie = response.headers["Set-Cookie"]
        for flag in ("HttpOnly", "Secure", "SameSite=Lax"):
            self.assertIn(flag, cookie)

    def test_callback_without_role_gives_read_only_access(self):
        self.complete_login({"resource_access": {"other-client": {"roles": ["catalog-admin"]}},
                             "realm_access": {"roles": ["catalog-admin"]}})
        self.assertFalse(self.client.get("/api/me").json["is_admin"])
        self.assertEqual(self.client.get("/api/nodes").status_code, 200)

    def test_callback_rejects_foreign_or_forged_access_token(self):
        from authlib.jose import JsonWebKey
        forged_key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
        for overrides, signer in (({"azp": "other-client"}, None), ({"iss": "https://evil.test/realms/catalog"}, None),
                                  ({"exp": int(time.time()) - 3600}, None), ({}, forged_key)):
            with self.subTest(overrides=overrides, forged=signer is not None):
                self.client = self.portal.app.test_client()
                response = self.complete_login(overrides, sign_with=signer)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_logout_ends_keycloak_session(self):
        login(self.client, admin=True)
        with patch.object(self.settings.client, "load_server_metadata", return_value=METADATA):
            response = self.client.get("/auth/logout")
        location = urlsplit(response.headers["Location"])
        query = parse_qs(location.query)
        self.assertEqual(location.scheme + "://" + location.netloc + location.path, METADATA["end_session_endpoint"])
        self.assertEqual(query["post_logout_redirect_uri"], [PUBLIC_URL + "/"])
        self.assertEqual(query["id_token_hint"], ["id-token-value"])
        self.assertEqual(self.client.get("/api/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
