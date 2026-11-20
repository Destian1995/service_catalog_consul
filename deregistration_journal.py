"""Persistent journal of observed deregistration requests, never inferred deletions.

Portal commands are recorded at the HTTP transport. External commands require
Consul Enterprise HTTP audit files mounted read-only. Internal RPC is not covered.
"""
import glob
import ipaddress
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else ""
    except (ValueError, TypeError):
        return ""


def _text(value, limit=512):
    return str(value or "").replace("\x00", "")[:limit]


@contextmanager
def _db():
    path = Path(os.environ.get("CONSUL_JOURNAL_DB", str(Path(__file__).parent / "data" / "deregistrations.sqlite3")))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL, request_id TEXT, started_at TEXT, completed_at TEXT,
            method TEXT, endpoint TEXT, node_name TEXT, service_id TEXT, datacenter TEXT,
            server TEXT, peer_ip TEXT, forwarded_for TEXT, token_accessor TEXT,
            http_status INTEGER, outcome TEXT, recorded_at TEXT
        );
        CREATE TABLE IF NOT EXISTS cursors (
            path TEXT PRIMARY KEY, identity TEXT, offset INTEGER, invalid_lines INTEGER DEFAULT 0,
            last_read TEXT, error TEXT
        );
    """)
    # Журналы, созданные до входа через Keycloak, получают колонку с логином пользователя портала.
    if "actor" not in {row["name"] for row in conn.execute("PRAGMA table_info(events)")}:
        try:
            conn.execute("ALTER TABLE events ADD COLUMN actor TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():  # Другой воркер Gunicorn успел раньше.
                raise
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _insert(conn, key, fields):
    columns = ["source", "request_id", "started_at", "completed_at", "method", "endpoint", "node_name",
               "service_id", "datacenter", "server", "peer_ip", "forwarded_for", "token_accessor",
               "http_status", "outcome", "recorded_at", "actor"]
    conn.execute(
        "INSERT INTO events (event_key," + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in range(len(columns) + 1)) + ") "
        "ON CONFLICT(event_key) DO UPDATE SET " + ",".join(
            f"{c}=CASE WHEN excluded.{c} IS NOT NULL AND excluded.{c} != '' THEN excluded.{c} ELSE events.{c} END"
            for c in columns if c != "started_at"
        ), [key] + [fields.get(c) for c in columns]
    )


def _caller():
    from flask import has_request_context, request
    if not has_request_context():
        return "", "", ""
    forwarded = []
    for item in request.headers.get("X-Forwarded-For", "").split(",")[:8]:
        try:
            forwarded.append(str(ipaddress.ip_address(item.strip())))
        except ValueError:
            pass
    from auth import current_login  # Логин из сессии Keycloak; вне портала — пусто.
    return _text(request.remote_addr), ", ".join(forwarded), _text(current_login(), 256)


def begin_command(base_url, path, payload):
    """Persist the attempt BEFORE sending. DB failure prevents an unlogged send."""
    key = "portal:" + str(uuid.uuid4())
    peer, forwarded, actor = _caller()
    parsed = urlsplit(base_url)
    server = parsed.hostname or ""
    if parsed.port:
        server += ":" + str(parsed.port)
    with _db() as conn:
        _insert(conn, key, dict(source="portal", request_id=key, started_at=utcnow(), method="PUT",
                               endpoint="/v1" + path, node_name=_text(payload.get("Node")),
                               service_id=_text(payload.get("ServiceID")), datacenter=_text(payload.get("Datacenter")),
                               server=server, peer_ip=peer, forwarded_for=forwarded, outcome="requested", recorded_at=utcnow(),
                               actor=actor))
    return key


def finish_command(key, status=None, error=False):
    with _db() as conn:
        conn.execute("UPDATE events SET completed_at=?, http_status=?, outcome=? WHERE event_key=?",
                     (utcnow(), status, "transport_error" if error else ("http_ok" if status and 200 <= status < 300 else "http_error"), key))


def _audit_event(document, server):
    """Project an official HTTPEvent; never save the raw document or auth secret."""
    proxy = document.get("type") == "consul_http_proxy"
    if proxy:
        payload = dict(type="HTTPEvent", id=document.get("request_id"), timestamp=document.get("time"),
                       request=dict(operation=document.get("method"), endpoint=document.get("endpoint"),
                                    remote_addr=document.get("remote_addr")), response=dict(status=document.get("status")),
                       stage="OperationComplete")
        server = _text(document.get("upstream_addr")) or server
    else:
        payload = document.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "HTTPEvent":
        return None
    req = payload.get("request") or {}
    if not isinstance(req, dict):
        return None
    parsed = urlsplit(_text(req.get("endpoint"), 2048))
    endpoint = parsed.path
    if str(req.get("operation", "")).upper() != "PUT":
        return None
    prefixes = ("/v1/agent/service/deregister/", "/v1/agent/check/deregister/")
    if endpoint != "/v1/catalog/deregister" and not any(endpoint.startswith(p) for p in prefixes):
        return None
    request_id = _text(payload.get("id"))
    if not request_id:
        return None
    response = payload.get("response") or {}
    auth = payload.get("auth") or {}
    status = response.get("status") if isinstance(response, dict) else None
    try:
        status = int(status) if status is not None else None
    except (ValueError, TypeError):
        status = None
    when = _timestamp(payload.get("timestamp") or document.get("created_at"))
    complete = payload.get("stage") == "OperationComplete"
    fields = dict(source="http_proxy" if proxy else "consul_audit", request_id=request_id, started_at=when,
                  completed_at=when if complete else None, method="PUT", endpoint=endpoint,
                  service_id=unquote(endpoint[len(prefixes[0]):]) if endpoint.startswith(prefixes[0]) else "",
                  node_name="", datacenter=_text((parse_qs(parsed.query).get("dc") or [""])[0]),
                  server=server, peer_ip=_text(req.get("remote_addr")),
                  token_accessor=_text(auth.get("accessor_id")) if isinstance(auth, dict) else "",
                  http_status=status,
                  outcome=("http_ok" if status and 200 <= status < 300 else "http_error") if complete else "requested",
                  recorded_at=utcnow())
    # Audit request bodies are not assumed to exist: unknown node stays unknown.
    return ("proxy:" if proxy else "audit:") + request_id, fields, complete


def audit_patterns():
    try:
        patterns = json.loads(os.environ.get("CONSUL_AUDIT_FILES") or "[]")
        if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
            raise ValueError()
        return patterns
    except (ValueError, TypeError):
        raise ValueError("CONSUL_AUDIT_FILES must be a JSON array of file paths or glob patterns") from None


def import_audit():
    """Bounded incremental read, shared SQLite transaction across Gunicorn workers."""
    paths = sorted({p for pattern in audit_patterns() for p in glob.glob(pattern) if os.path.isfile(p)})
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        budget = 5000
        for path in paths:
            if budget <= 0:
                break
            row = conn.execute("SELECT * FROM cursors WHERE path=?", (path,)).fetchone()
            offset = row["offset"] if row else 0
            invalid = row["invalid_lines"] if row else 0
            identity = row["identity"] if row else ""
            error = ""
            try:
                with open(path, "rb") as stream:
                    stat = os.fstat(stream.fileno())
                    current = str(stat.st_dev) + ":" + str(stat.st_ino)
                    if current != identity or stat.st_size < offset:
                        offset = 0
                    identity = current
                    stream.seek(offset)
                    consumed = 0
                    while budget > 0 and consumed < 4 * 1024 * 1024:
                        position = stream.tell()
                        line = stream.readline(1024 * 1024 + 1)
                        if not line:
                            break
                        if len(line) > 1024 * 1024:
                            error = "line_exceeds_1MiB"; break
                        if not line.endswith(b"\n"):
                            break  # Keep partial append for the next read.
                        offset = stream.tell()
                        consumed += offset - position
                        budget -= 1
                        try:
                            doc = json.loads(line)
                            if not isinstance(doc, dict):
                                raise ValueError()
                            event = _audit_event(doc, os.path.basename(path))
                            if event:
                                key, fields, complete = event
                                existing = conn.execute("SELECT outcome FROM events WHERE event_key=?", (key,)).fetchone()
                                if complete or not existing or existing["outcome"] == "requested":
                                    _insert(conn, key, fields)
                        except (ValueError, TypeError, AttributeError):
                            invalid += 1
            except OSError as exc:
                error = type(exc).__name__  # Paths/content/secrets are not returned as exceptions.
            conn.execute("INSERT INTO cursors(path,identity,offset,invalid_lines,last_read,error) VALUES(?,?,?,?,?,?) "
                         "ON CONFLICT(path) DO UPDATE SET identity=excluded.identity,offset=excluded.offset,"
                         "invalid_lines=excluded.invalid_lines,last_read=excluded.last_read,error=excluded.error",
                         (path, identity, offset, invalid, utcnow(), error))
    return len(paths)


def portal_targets(config):
    """Configured destinations only; never expose credentials or probe servers."""
    targets = []
    for cluster_id, cluster in config.get("clusters", {}).items():
        for dc in cluster.get("datacenters", []):
            try:
                scheme = dc.get("scheme", "https")
                parsed = urlsplit(scheme + "://" + str(dc.get("host", "")))
                if scheme not in ("http", "https") or not parsed.hostname:
                    continue
                host = parsed.hostname
                if ":" in host:
                    host = "[" + host + "]"
                endpoint = scheme + "://" + host + (":" + str(parsed.port) if parsed.port else "")
            except ValueError:
                continue
            targets.append(dict(cluster=_text(cluster.get("name") or cluster_id),
                                datacenter=_text(dc.get("name")), endpoint=endpoint))
    return targets


def journal_data(query="", source="", outcome="", before=0, limit=100, config=None, node=""):
    patterns = audit_patterns()
    matched = import_audit() if patterns else 0
    clauses, args = [], []
    if node:
        clauses.append("node_name LIKE ? ESCAPE '\\'")
        args.append("%" + node.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if query:
        clauses.append("(" + " OR ".join(c + " LIKE ? ESCAPE '\\'" for c in
                       ("node_name", "service_id", "datacenter", "server", "peer_ip", "token_accessor", "endpoint",
                        "actor")) + ")")
        term = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args.extend([term] * 8)
    for column, value in (("source", source), ("outcome", outcome)):
        if value:
            clauses.append(column + "=?"); args.append(value)
    if before:
        clauses.append("id<?"); args.append(before)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM events" + where + " ORDER BY id DESC LIMIT ?", args + [limit + 1])]
        cursors = [dict(r) for r in conn.execute("SELECT path,offset,invalid_lines,last_read,error FROM cursors ORDER BY path")]
        proxy_destinations = [dict(r) for r in conn.execute(
            "SELECT server, MAX(recorded_at) AS last_seen FROM events WHERE source='http_proxy' AND server != '' GROUP BY server ORDER BY last_seen DESC LIMIT 20")]
    selected_paths = {os.path.normcase(os.path.abspath(p)) for pattern in patterns for p in glob.glob(pattern)}
    for cursor in cursors:
        cursor["present"] = os.path.isfile(cursor["path"])
        cursor["selected"] = os.path.normcase(os.path.abspath(cursor["path"])) in selected_paths
    more = len(rows) > limit
    rows = rows[:limit]
    return dict(events=rows, next_before=rows[-1]["id"] if more else None,
                coverage=dict(portal=True, audit_configured=bool(patterns), audit_files_found=matched,
                              internal_rpc=False, files=cursors, audit_patterns=patterns,
                              portal_mode=(config or {}).get("mode", "test"),
                              portal_targets=portal_targets(config or {}), proxy_destinations=proxy_destinations))


def start_audit_reader():
    if not os.environ.get("CONSUL_AUDIT_FILES"):
        return
    def run():
        import logging
        while True:
            try:
                import_audit()
            except Exception as exc:
                logging.getLogger(__name__).warning("Audit import unavailable: %s", type(exc).__name__)
            threading.Event().wait(10)
    threading.Thread(target=run, name="consul-audit-reader", daemon=True).start()
