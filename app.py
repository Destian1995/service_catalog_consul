"""
Каталог сервисов — Flask backend.
Поддержка режимов: test (тестовые данные) / live (реальный Consul API).
Админ-панель для управления подключениями.
"""

import os
import json
import logging
from flask import Flask, jsonify, request, send_from_directory, session, redirect
from flask_cors import CORS

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("app")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "consul_manager_config.json")

app = Flask(__name__, static_folder="static")
CORS(app)

# ──────────────────────────────────────
# Config management (cached in memory)
# ──────────────────────────────────────

_config_cache = {"data": None, "mtime": 0}

def load_config():
    """Read config from disk, cached until file changes."""
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        mtime = 0
    if _config_cache["data"] is not None and mtime == _config_cache["mtime"]:
        return _config_cache["data"]
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    _config_cache["data"] = cfg
    _config_cache["mtime"] = mtime
    return cfg

def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    _config_cache["data"] = None  # invalidate cache

def get_mode():
    return load_config().get("mode", "test")

# ──────────────────────────────────────
# Data providers: test vs live
# ──────────────────────────────────────

def _test_nodes(filters=None):
    from test_data import NODES
    result = NODES[:]
    if filters:
        if filters.get("dc"):
            result = [n for n in result if n["Datacenter"] == filters["dc"]]
        if filters.get("env"):
            result = [n for n in result if n["Meta"].get("environment") == filters["env"]]
        if filters.get("team"):
            result = [n for n in result if n["Meta"].get("team") == filters["team"]]
        if filters.get("system_name"):
            result = [n for n in result if n["Meta"].get("system_name") == filters["system_name"]]
        if filters.get("search"):
            s = filters["search"].lower()
            result = [n for n in result if s in n["Node"].lower() or s in n["Address"]]
    return result

def _live_nodes(filters=None):
    from consul_client import ConsulAggregator
    agg = ConsulAggregator(load_config())
    result = agg.get_all_nodes()
    if filters:
        if filters.get("dc"):
            result = [n for n in result if n["Datacenter"] == filters["dc"]]
        if filters.get("env"):
            result = [n for n in result if n["Meta"].get("environment") == filters["env"]]
        if filters.get("team"):
            result = [n for n in result if n["Meta"].get("team") == filters["team"]]
        if filters.get("system_name"):
            result = [n for n in result if n["Meta"].get("system_name") == filters["system_name"]]
        if filters.get("search"):
            s = filters["search"].lower()
            result = [n for n in result if s in n["Node"].lower() or s in n["Address"]]
    return result

def get_nodes(filters=None):
    return _test_nodes(filters) if get_mode() == "test" else _live_nodes(filters)

def _test_node_detail(node_name):
    from test_data import NODES, SERVICES, build_health_checks
    node = next((n for n in NODES if n["Node"] == node_name), None)
    if not node:
        return None
    svcs = []
    for svc in SERVICES:
        if node_name in svc["Nodes"]:
            svcs.append({"ID": svc["ID"], "Service": svc["Service"], "Tags": svc["Tags"],
                         "Port": svc["Port"], "Meta": svc["Meta"]})
    checks = [c for c in build_health_checks() if c["Node"] == node_name]
    return {"node": node, "services": svcs, "checks": checks}

def _live_node_detail(node_name):
    from consul_client import ConsulAggregator
    agg = ConsulAggregator(load_config())
    all_nodes = agg.get_all_nodes()
    node = next((n for n in all_nodes if n["Node"] == node_name), None)
    if not node:
        return None
    detail = agg.get_node_detail(node_name)
    return {"node": node, "services": detail["services"], "checks": detail["checks"]}

def get_node_detail(node_name):
    return _test_node_detail(node_name) if get_mode() == "test" else _live_node_detail(node_name)

def _test_services(filters=None):
    from test_data import NODES, SERVICES
    seen = {}
    for svc in SERVICES:
        if filters:
            if filters.get("dc"):
                svc_nodes = [n for n in NODES if n["Node"] in svc["Nodes"] and n["Datacenter"] == filters["dc"]]
                if not svc_nodes:
                    continue
            if filters.get("tag") and filters["tag"] not in svc["Tags"]:
                continue
            if filters.get("search") and filters["search"].lower() not in svc["Service"].lower():
                continue
        name = svc["Service"]
        if name not in seen:
            seen[name] = {"name": name, "tags": set(), "instances": 0, "ports": set()}
        seen[name]["tags"].update(svc["Tags"])
        seen[name]["instances"] += len(svc["Nodes"])
        seen[name]["ports"].add(svc["Port"])
    result = []
    for data in seen.values():
        result.append({"name": data["name"], "tags": sorted(data["tags"]),
                       "instances": data["instances"], "ports": sorted(data["ports"])})
    return sorted(result, key=lambda x: x["name"])

def _live_services(filters=None):
    from consul_client import ConsulAggregator
    agg = ConsulAggregator(load_config())
    result = agg.get_all_services()
    if filters:
        if filters.get("tag"):
            result = [s for s in result if filters["tag"] in s["tags"]]
        if filters.get("search"):
            q = filters["search"].lower()
            result = [s for s in result if q in s["name"].lower()]
    return result

def get_services(filters=None):
    return _test_services(filters) if get_mode() == "test" else _live_services(filters)

def _test_service_detail(service_name):
    from test_data import NODES, SERVICES, build_health_checks
    instances = []
    for svc in SERVICES:
        if svc["Service"] == service_name:
            for node_name in svc["Nodes"]:
                node = next((n for n in NODES if n["Node"] == node_name), None)
                if not node:
                    continue
                checks = [c for c in build_health_checks()
                          if c["Node"] == node_name and c["ServiceName"] == service_name]
                status = "passing"
                for c in checks:
                    if c["Status"] == "critical": status = "critical"; break
                    if c["Status"] == "warning": status = "warning"
                instances.append({
                    "service": {"ID": svc["ID"], "Service": svc["Service"], "Tags": svc["Tags"],
                                "Port": svc["Port"], "Meta": svc["Meta"]},
                    "node": node, "checks": checks, "status": status,
                })
    return instances

def _live_service_detail(service_name):
    from consul_client import ConsulAggregator
    agg = ConsulAggregator(load_config())
    return agg.get_service_detail(service_name)

def get_service_detail(service_name):
    return _test_service_detail(service_name) if get_mode() == "test" else _live_service_detail(service_name)

# ──────────────────────────────────────
# API endpoints
# ──────────────────────────────────────

@app.route("/api/mode")
def api_mode():
    return jsonify({"mode": get_mode()})

@app.route("/api/datacenters")
def api_datacenters():
    if get_mode() == "test":
        from test_data import DATACENTERS
        return jsonify(DATACENTERS)
    else:
        from consul_client import ConsulAggregator
        agg = ConsulAggregator(load_config())
        return jsonify(agg.get_datacenters())

@app.route("/api/nodes")
def api_nodes():
    filters = {k: request.args.get(k) for k in ["dc", "env", "team", "system_name", "search"] if request.args.get(k)}
    return jsonify(get_nodes(filters))

@app.route("/api/nodes/<node_name>")
def api_node_detail(node_name):
    data = get_node_detail(node_name)
    if not data:
        return jsonify({"error": "Node not found"}), 404
    return jsonify(data)

@app.route("/api/services")
def api_services():
    filters = {k: request.args.get(k) for k in ["dc", "tag", "search"] if request.args.get(k)}
    return jsonify(get_services(filters))

@app.route("/api/services/<service_name>")
def api_service_detail(service_name):
    instances = get_service_detail(service_name)
    if not instances:
        return jsonify({"error": "Service not found"}), 404
    return jsonify(instances)

@app.route("/api/health/summary")
def api_health_summary():
    nodes = get_nodes()
    if get_mode() == "test":
        from test_data import build_health_checks
        checks = build_health_checks()
    else:
        from consul_client import ConsulAggregator
        from concurrent.futures import ThreadPoolExecutor, as_completed
        agg = ConsulAggregator(load_config())
        checks = []
        # Parallel health check fetch instead of sequential N+1
        def _fetch_checks(client, node_name):
            return client.get_health_checks(node_name)

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = []
            for n in nodes:
                node_name = n["Node"]
                # Find the right DC client
                for client in agg.clients:
                    if client.dc_name == n.get("Datacenter"):
                        futures.append(pool.submit(_fetch_checks, client, node_name))
                        break
            for f in as_completed(futures):
                try:
                    checks.extend(f.result())
                except Exception:
                    pass

    summary = {"passing": 0, "warning": 0, "critical": 0, "total_services": 0, "total_nodes": len(nodes)}
    svc_names = set()
    for c in checks:
        summary[c.get("Status", "passing")] = summary.get(c.get("Status", "passing"), 0) + 1
        if c.get("ServiceName"):
            svc_names.add(c["ServiceName"])
    summary["total_services"] = len(svc_names)
    return jsonify(summary)

@app.route("/api/tags")
def api_tags():
    services = get_services()
    tags = set()
    for s in services:
        tags.update(s.get("tags", []))
    return jsonify(sorted(tags))

@app.route("/api/teams")
def api_teams():
    nodes = get_nodes()
    return jsonify(sorted({n["Meta"].get("team", "") for n in nodes if n["Meta"].get("team")}))

@app.route("/api/environments")
def api_environments():
    nodes = get_nodes()
    return jsonify(sorted({n["Meta"].get("environment", "") for n in nodes if n["Meta"].get("environment")}))

@app.route("/api/systems")
def api_systems():
    nodes = get_nodes()
    return jsonify(sorted({n["Meta"].get("system_name", "") for n in nodes
                           if n["Meta"].get("system_name") and n["Meta"]["system_name"] != "-"}))

@app.route("/api/analytics")
def api_analytics():
    nodes = get_nodes()
    services_list = get_services()

    # Gather checks
    if get_mode() == "test":
        from test_data import build_health_checks, SERVICES as TEST_SERVICES
        checks = build_health_checks()
    else:
        checks = []

    # Servers by dc / env / os / team
    servers_by_dc, servers_by_env, servers_by_os, servers_by_team = {}, {}, {}, {}
    for n in nodes:
        dc = n["Datacenter"]
        servers_by_dc[dc] = servers_by_dc.get(dc, 0) + 1
        env = n["Meta"].get("environment", "unknown")
        servers_by_env[env] = servers_by_env.get(env, 0) + 1
        os_name = n["Meta"].get("os", "unknown")
        servers_by_os[os_name] = servers_by_os.get(os_name, 0) + 1
        team = n["Meta"].get("team", "unknown")
        servers_by_team[team] = servers_by_team.get(team, 0) + 1

    # Services by category
    category_map = {"web": "Web / Proxy", "proxy": "Web / Proxy", "api": "API", "gateway": "API",
                    "auth": "API", "database": "Database", "sql": "Database", "nosql": "Database",
                    "cache": "Cache", "in-memory": "Cache", "mq": "Message Queue", "amqp": "Message Queue",
                    "streaming": "Message Queue", "monitoring": "Monitoring", "metrics": "Monitoring",
                    "dashboards": "Monitoring", "logs": "Monitoring", "alerts": "Monitoring",
                    "infra": "Infrastructure", "service-discovery": "Infrastructure", "exporter": "Monitoring"}
    svc_by_category = {}
    for svc in services_list:
        cat = "Other"
        for tag in svc.get("tags", []):
            if tag in category_map:
                cat = category_map[tag]; break
        svc_by_category[cat] = svc_by_category.get(cat, 0) + 1

    # Instances per service
    instances_per_svc = {s["name"]: s["instances"] for s in services_list}
    instances_per_svc = dict(sorted(instances_per_svc.items(), key=lambda x: -x[1]))

    # Health
    health = {"passing": 0, "warning": 0, "critical": 0}
    for c in checks:
        health[c.get("Status", "passing")] = health.get(c.get("Status", "passing"), 0) + 1

    # Services per server (test mode only for now)
    svcs_per_server = {}
    if get_mode() == "test":
        for n in nodes:
            cnt = sum(1 for svc in TEST_SERVICES if n["Node"] in svc["Nodes"])
            svcs_per_server[n["Node"]] = cnt
    else:
        for n in nodes:
            svcs_per_server[n["Node"]] = 0  # filled lazily
    svcs_per_server = dict(sorted(svcs_per_server.items(), key=lambda x: -x[1]))

    # Health by DC
    health_by_dc = {}
    for c in checks:
        node = next((n for n in nodes if n["Node"] == c["Node"]), None)
        if node:
            dc = node["Datacenter"]
            if dc not in health_by_dc:
                health_by_dc[dc] = {"passing": 0, "warning": 0, "critical": 0}
            health_by_dc[dc][c["Status"]] = health_by_dc[dc].get(c["Status"], 0) + 1

    # Monitoring coverage
    basic_monitoring_svcs = {"node-exporter", "consul-agent"}
    full_monitoring_svcs = {"prometheus", "grafana", "alertmanager", "loki",
                            "victoria-metrics", "zabbix-agent", "telegraf",
                            "filebeat", "fluentd", "vector"}
    monitoring_levels = {"full": [], "basic": [], "none": []}
    monitoring_by_dc, monitoring_by_env = {}, {}

    if get_mode() == "test":
        for n in nodes:
            node_name = n["Node"]
            node_svcs = {svc["Service"] for svc in TEST_SERVICES if node_name in svc["Nodes"]}
            has_full = bool(node_svcs & full_monitoring_svcs)
            has_basic = bool(node_svcs & basic_monitoring_svcs)
            level = "full" if has_full else ("basic" if has_basic else "none")
            monitoring_levels[level].append(node_name)
            dc = n["Datacenter"]
            if dc not in monitoring_by_dc: monitoring_by_dc[dc] = {"full": 0, "basic": 0, "none": 0}
            monitoring_by_dc[dc][level] += 1
            env = n["Meta"].get("environment", "unknown")
            if env not in monitoring_by_env: monitoring_by_env[env] = {"full": 0, "basic": 0, "none": 0}
            monitoring_by_env[env][level] += 1
    else:
        # In live mode, use system_dependencies_monitoring metadata
        for n in nodes:
            raw = n.get("_raw_meta", {})
            has_mon = raw.get("system_dependencies_monitoring", "").lower() in ("true", "1", "yes")
            level = "full" if has_mon else "basic"
            monitoring_levels[level].append(n["Node"])
            dc = n["Datacenter"]
            if dc not in monitoring_by_dc: monitoring_by_dc[dc] = {"full": 0, "basic": 0, "none": 0}
            monitoring_by_dc[dc][level] += 1
            env = n["Meta"].get("environment", "unknown")
            if env not in monitoring_by_env: monitoring_by_env[env] = {"full": 0, "basic": 0, "none": 0}
            monitoring_by_env[env][level] += 1

    total_nodes = len(nodes)
    monitoring_summary = {
        "total": total_nodes,
        "monitored": len(monitoring_levels["full"]) + len(monitoring_levels["basic"]),
        "full": len(monitoring_levels["full"]),
        "basic": len(monitoring_levels["basic"]),
        "none": len(monitoring_levels["none"]),
        "coverage_pct": round((len(monitoring_levels["full"]) + len(monitoring_levels["basic"])) / total_nodes * 100) if total_nodes else 0,
        "servers_full": monitoring_levels["full"],
        "servers_basic": monitoring_levels["basic"],
        "servers_none": monitoring_levels["none"],
    }

    # Hosts by IS
    hosts_by_system = {}
    for n in nodes:
        sys_name = n["Meta"].get("system_name", "Unassigned")
        if sys_name == "-": sys_name = "Unassigned"
        if sys_name not in hosts_by_system:
            hosts_by_system[sys_name] = {"count": 0, "servers": [], "dcs": set(), "envs": set()}
        hosts_by_system[sys_name]["count"] += 1
        hosts_by_system[sys_name]["servers"].append(n["Node"])
        hosts_by_system[sys_name]["dcs"].add(n["Datacenter"])
        hosts_by_system[sys_name]["envs"].add(n["Meta"].get("environment", "unknown"))

    hosts_by_system_out = {}
    for sys_name, info in sorted(hosts_by_system.items(), key=lambda x: -x[1]["count"]):
        hosts_by_system_out[sys_name] = {
            "count": info["count"], "servers": info["servers"],
            "datacenters": sorted(info["dcs"]), "environments": sorted(info["envs"]),
        }

    # Services per system
    services_by_system = {}
    if get_mode() == "test":
        for sys_name, info in hosts_by_system.items():
            svc_set = set()
            for svc in TEST_SERVICES:
                for srv in info["servers"]:
                    if srv in svc["Nodes"]:
                        svc_set.add(svc["Service"])
            services_by_system[sys_name] = sorted(svc_set)
    else:
        for sys_name in hosts_by_system:
            services_by_system[sys_name] = []

    return jsonify({
        "services_by_category": svc_by_category, "servers_by_dc": servers_by_dc,
        "servers_by_env": servers_by_env, "servers_by_os": servers_by_os,
        "servers_by_team": servers_by_team, "instances_per_service": instances_per_svc,
        "health_status": health, "services_per_server": svcs_per_server,
        "health_by_dc": health_by_dc, "monitoring": monitoring_summary,
        "monitoring_by_dc": monitoring_by_dc, "monitoring_by_env": monitoring_by_env,
        "hosts_by_system": hosts_by_system_out, "services_by_system": services_by_system,
    })

# ──────────────────────────────────────
# Admin API
# ──────────────────────────────────────

@app.route("/api/admin/config", methods=["GET"])
def admin_get_config():
    cfg = load_config()
    # Hide tokens in GET
    safe = json.loads(json.dumps(cfg))
    for cluster in safe.get("clusters", {}).values():
        for dc in cluster.get("datacenters", []):
            if dc.get("token"):
                dc["token"] = dc["token"][:8] + "****"
    return jsonify(safe)

@app.route("/api/admin/config", methods=["POST"])
def admin_save_config():
    pwd = request.json.get("password", "")
    cfg = load_config()
    if pwd != cfg.get("app", {}).get("admin_password", "admin"):
        return jsonify({"error": "Неверный пароль"}), 403

    new_cfg = request.json.get("config")
    if not new_cfg:
        return jsonify({"error": "Пустой конфиг"}), 400

    # Preserve tokens that were masked
    old_cfg = load_config()
    for cluster_id, cluster in new_cfg.get("clusters", {}).items():
        old_cluster = old_cfg.get("clusters", {}).get(cluster_id, {})
        for i, dc in enumerate(cluster.get("datacenters", [])):
            if dc.get("token", "").endswith("****"):
                old_dcs = old_cluster.get("datacenters", [])
                if i < len(old_dcs):
                    dc["token"] = old_dcs[i]["token"]

    save_config(new_cfg)
    return jsonify({"ok": True})

@app.route("/api/admin/mode", methods=["POST"])
def admin_set_mode():
    pwd = request.json.get("password", "")
    cfg = load_config()
    if pwd != cfg.get("app", {}).get("admin_password", "admin"):
        return jsonify({"error": "Неверный пароль"}), 403
    new_mode = request.json.get("mode")
    if new_mode not in ("test", "live"):
        return jsonify({"error": "Режим должен быть test или live"}), 400
    cfg["mode"] = new_mode
    save_config(cfg)
    return jsonify({"ok": True, "mode": new_mode})

@app.route("/api/admin/diagnose")
def admin_diagnose():
    """Full diagnostic: test every DC, try fetching nodes."""
    cfg = load_config()
    mode = cfg.get("mode", "test")
    results = {"mode": mode, "clusters": [], "errors": []}

    if mode == "test":
        from test_data import NODES
        results["test_nodes"] = len(NODES)
        return jsonify(results)

    from consul_client import ConsulAggregator
    agg = ConsulAggregator(cfg)

    # Test connectivity
    connectivity = agg.test_all()
    for r in connectivity:
        results["clusters"].append(r)
        if not r.get("ok"):
            results["errors"].append(f"DC '{r['dc']}': {r.get('error', 'HTTP ' + str(r.get('status', '?')))}")

    # Try fetching nodes
    try:
        nodes = agg.get_all_nodes()
        results["total_nodes"] = len(nodes)
        if nodes:
            results["sample_node"] = {
                "Node": nodes[0]["Node"],
                "Address": nodes[0]["Address"],
                "DC": nodes[0]["Datacenter"],
                "Meta_keys": list(nodes[0].get("_raw_meta", {}).keys())[:15],
            }
    except Exception as e:
        results["errors"].append(f"get_all_nodes: {type(e).__name__}: {e}")
        results["total_nodes"] = 0

    return jsonify(results)

@app.route("/api/admin/test-connection", methods=["POST"])
def admin_test_connection():
    """Test connectivity to a single Consul DC."""
    host = request.json.get("host", "")
    token = request.json.get("token", "")
    scheme = request.json.get("scheme", "https")
    verify = request.json.get("verify_ssl", True)
    try:
        import requests as req
        url = f"{scheme}://{host}/v1/status/leader"
        headers = {"X-Consul-Token": token} if token else {}
        r = req.get(url, headers=headers, verify=verify, timeout=5)
        r.raise_for_status()
        leader = r.json()
        return jsonify({"ok": True, "leader": leader})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/admin/cluster", methods=["POST"])
def admin_add_cluster():
    """Add a new cluster to config."""
    pwd = request.json.get("password", "")
    cfg = load_config()
    if pwd != cfg.get("app", {}).get("admin_password", "admin"):
        return jsonify({"error": "Неверный пароль"}), 403
    cluster = request.json.get("cluster")
    if not cluster or not cluster.get("name"):
        return jsonify({"error": "Укажите имя кластера"}), 400
    cfg.setdefault("clusters", {})[cluster["name"]] = cluster
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/admin/cluster/<cluster_id>", methods=["DELETE"])
def admin_delete_cluster(cluster_id):
    pwd = request.json.get("password", "")
    cfg = load_config()
    if pwd != cfg.get("app", {}).get("admin_password", "admin"):
        return jsonify({"error": "Неверный пароль"}), 403
    cfg.get("clusters", {}).pop(cluster_id, None)
    save_config(cfg)
    return jsonify({"ok": True})

# ──────────────────────────────────────
# Serve SPA + Admin
# ──────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/admin")
def admin_page():
    return send_from_directory("static", "admin.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


def _start_warmer():
    """Start background cache warmer if in live mode."""
    try:
        from consul_client import start_cache_warmer
        start_cache_warmer(load_config)
    except Exception as e:
        log.error(f"Failed to start cache warmer: {e}")

_start_warmer()

if __name__ == "__main__":
    cfg = load_config()
    mode = cfg.get("mode", "test")
    port = cfg.get("app", {}).get("port", 5000)
    print(f"\n  Каталог сервисов — http://localhost:{port}")
    print(f"  Режим: {'ТЕСТ' if mode == 'test' else 'БОЕВОЙ (Consul API)'}")
    print(f"  Админка: http://localhost:{port}/admin\n")
    app.run(debug=True, host="0.0.0.0", port=port)
