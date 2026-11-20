import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import deregistration_journal as journal


def audit(request_id, stage="OperationComplete", status=200, method="PUT", endpoint="/v1/catalog/deregister"):
    return dict(created_at="2026-11-03T12:00:00+04:00", payload=dict(
        type="HTTPEvent", id=request_id, stage=stage, timestamp="2026-11-03T12:00:00+04:00",
        request=dict(operation=method, endpoint=endpoint, remote_addr="10.1.2.3:1234"),
        response=dict(status=str(status)), auth=dict(accessor_id="accessor-public", secret_id="SECRET-MUST-NOT-BE-SAVED")))


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = self.root / "audit.jsonl"
        self.env = patch.dict(os.environ, CONSUL_JOURNAL_DB=str(self.root / "journal.db"),
                              CONSUL_AUDIT_FILES=json.dumps([str(self.root / "*.jsonl*")]))
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def append(self, document):
        with self.log.open("ab") as stream:
            stream.write((json.dumps(document) + "\n").encode())

    def test_audit_dedup_and_secret_projection(self):
        self.append(audit("r1", "OperationStart"))
        self.append(audit("r1"))
        self.append(audit("read", method="GET"))
        self.append(audit("register", endpoint="/v1/catalog/register"))
        journal.import_audit()
        result = journal.journal_data()
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(event["http_status"], 200)
        self.assertEqual(event["started_at"], "2026-11-03T08:00:00+00:00")
        self.assertEqual(event["node_name"], "")
        self.assertEqual(event["peer_ip"], "10.1.2.3:1234")
        self.assertNotIn("SECRET-MUST-NOT-BE-SAVED", json.dumps(result))
        self.assertEqual(len(journal.journal_data()["events"]), 1)

    def test_partial_lines_rotation_and_invalid_json(self):
        raw = json.dumps(audit("r1")).encode()
        self.log.write_bytes(raw[:100])
        self.assertEqual(journal.journal_data()["events"], [])
        with self.log.open("ab") as stream:
            stream.write(raw[100:] + b"\nnot json\n")
        self.assertEqual(len(journal.journal_data()["events"]), 1)
        self.log.rename(self.root / "audit.jsonl.1")
        self.append(audit("r2", status=403))
        result = journal.journal_data()
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["events"][0]["outcome"], "http_error")

    def test_complete_is_not_downgraded_by_late_start(self):
        self.append(audit("r1"))
        journal.import_audit()
        self.append(audit("r1", "OperationStart"))
        self.assertEqual(journal.journal_data()["events"][0]["outcome"], "http_ok")

    def test_proxy_and_pagination(self):
        for n in range(3):
            self.append(dict(type="consul_http_proxy", request_id=f"proxy-{n}", method="PUT",
                             endpoint="/v1/agent/service/deregister/vector", remote_addr="10.2.3.4",
                             upstream_addr="10.3.4.5:8500", status=200, time="2026-11-03T08:00:00Z"))
        result = journal.journal_data(query="vector", source="http_proxy", limit=2)
        self.assertEqual(len(result["events"]), 2)
        older = journal.journal_data(before=result["next_before"], limit=2)
        self.assertEqual(len(older["events"]), 1)
        self.assertEqual(older["events"][0]["service_id"], "vector")
        self.assertEqual(journal.journal_data(query="%'")["events"], [])

    def test_portal_actor_and_transport(self):
        from flask import Flask
        from consul_client import ConsulClient
        app = Flask(__name__)
        client = ConsulClient("consul:8500", scheme="http", token="SECRET-MUST-NOT-BE-SAVED")
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers={"X-Forwarded-For": "10.1.1.2, arbitrary"}):
            with patch("consul_client.requests.put") as send:
                send.return_value.status_code = 200
                self.assertTrue(client.deregister_node("uknrd101", "1xx"))
                self.assertEqual(len(journal.journal_data()["events"]), 1)
            event = journal.journal_data()["events"][0]
            self.assertEqual(event["node_name"], "uknrd101")
            self.assertEqual(event["forwarded_for"], "10.1.1.2")
            self.assertNotIn("SECRET-MUST-NOT-BE-SAVED", json.dumps(event))
            import requests
            with patch("consul_client.requests.put", side_effect=requests.exceptions.Timeout()):
                self.assertFalse(client.deregister_node("uknrd101", "1xx"))
            self.assertEqual(journal.journal_data()["events"][0]["outcome"], "transport_error")

    def test_api_and_page_without_contacting_consul(self):
        # Если портал ещё не загружен другим тестом, он запускается без входа; иначе входим читателем.
        with patch.dict(os.environ, CONSUL_AUDIT_FILES="", AUTH_MODE="disabled"), patch("consul_client.start_cache_warmer"):
            import app as portal
            with portal.app.test_client() as client, patch.object(portal, "load_config", return_value={"mode": "test", "clusters": {}}):
                with client.session_transaction() as state:
                    state["user"] = {"login": "reader1", "name": "Reader", "is_admin": False, "exp": time.time() + 60}
                response = client.get("/api/deregistrations")
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json["coverage"]["audit_configured"])
                self.assertEqual(client.get("/api/deregistrations?limit=oops").status_code, 400)
                with client.get("/deregistrations") as page:
                    self.assertEqual(page.status_code, 200)

    def test_unavailable_journal_blocks_send(self):
        from consul_client import ConsulClient
        client = ConsulClient("consul:8500", scheme="http")
        with patch("deregistration_journal.begin_command", side_effect=OSError()), patch("consul_client.requests.put") as send:
            with self.assertRaises(OSError):
                client.deregister_node("uknrd101", "1xx")
            send.assert_not_called()

    def test_result_storage_failure_does_not_change_successful_http_result(self):
        from consul_client import ConsulClient
        client = ConsulClient("consul:8500", scheme="http")
        with patch("deregistration_journal.finish_command", side_effect=OSError()), patch("consul_client.requests.put") as send:
            send.return_value.status_code = 200
            self.assertTrue(client.deregister_node("uknrd101", "1xx"))
        self.assertEqual(journal.journal_data()["events"][0]["outcome"], "requested")


    def test_tracking_targets_hide_secrets_and_do_not_infer_proxy(self):
        config = dict(mode="live", clusters={"prod": dict(name="Production", datacenters=[
            dict(name="1xx", scheme="https", host="user:SECRET@consul.example:8501/private?token=SECRET", token="SECRET")])})
        result = journal.journal_data(config=config)
        self.assertEqual(result["coverage"]["portal_mode"], "live")
        self.assertEqual(result["coverage"]["portal_targets"], [dict(cluster="Production", datacenter="1xx", endpoint="https://consul.example:8501")])
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertEqual(result["coverage"]["proxy_destinations"], [])
        self.assertEqual(result["coverage"]["audit_patterns"], [str(self.root / "*.jsonl*")])


    def test_journal_created_before_login_gets_actor_column(self):
        db = self.root / "journal.db"
        with sqlite3.connect(str(db)) as conn:
            conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE, "
                         "source TEXT NOT NULL, request_id TEXT, started_at TEXT, completed_at TEXT, method TEXT, "
                         "endpoint TEXT, node_name TEXT, service_id TEXT, datacenter TEXT, server TEXT, peer_ip TEXT, "
                         "forwarded_for TEXT, token_accessor TEXT, http_status INTEGER, outcome TEXT, recorded_at TEXT)")
            conn.execute("INSERT INTO events (event_key, source, node_name, outcome) VALUES ('old', 'portal', 'old101', 'http_ok')")
        conn.close()
        events = journal.journal_data()["events"]
        self.assertEqual([(e["node_name"], e["actor"]) for e in events], [("old101", None)])
        key = journal.begin_command("https://consul.example", "/catalog/deregister", {"Node": "new101"})
        journal.finish_command(key, 200)
        self.assertEqual(journal.journal_data(node="new101")["events"][0]["actor"], "")

    def test_node_filter_matches_node_not_service_or_unknown_target(self):
        for node in ("uknrd101.am-aton.local", "nrd101.moscow.aton.holding", "literal_percent%"):
            key = journal.begin_command("https://consul.example", "/catalog/deregister", {"Node": node})
            journal.finish_command(key, 200)
        self.append(audit("unknown-node"))
        events = journal.journal_data(node="uknrd101")["events"]
        self.assertEqual([e["node_name"] for e in events], ["uknrd101.am-aton.local"])
        self.assertEqual(len(journal.journal_data(node="%")["events"]), 1)
        self.assertEqual(journal.journal_data(node="vector")["events"], [])


if __name__ == "__main__":
    unittest.main()
