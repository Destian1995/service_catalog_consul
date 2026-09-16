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

def _exclude_systems(nodes):
    """Remove nodes belonging to excluded systems or matching excluded hostname patterns."""
    import fnmatch
    cfg = load_config()
    excluded_is = set(cfg.get("excluded_systems", []))
    excluded_hosts = cfg.get("excluded_hosts", ["consul-aton-infra-prod*"])
    result = nodes
    if excluded_is:
        result = [n for n in result if (n["Meta"].get("system_name") or "").strip() not in excluded_is]
    if excluded_hosts:
        result = [n for n in result
                  if not any(fnmatch.fnmatch(n["Node"].lower(), pat.lower()) for pat in excluded_hosts)]
    return result

# ──────────────────────────────────────
# Data providers: test vs live
# ──────────────────────────────────────

def _test_nodes(filters=None):
    from test_data import NODES
    result = _exclude_systems(NODES[:])
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
            result = [n for n in result if s in n["Node"].lower()
                      or s in n["Address"]
                      or s in (n["Meta"].get("system_name") or "").lower()]
    return result

def _live_nodes(filters=None):
    from consul_client import ConsulAggregator
    agg = ConsulAggregator(load_config())
    result = _exclude_systems(agg.get_all_nodes())
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
            result = [n for n in result if s in n["Node"].lower()
                      or s in n["Address"]
                      or s in (n["Meta"].get("system_name") or "").lower()]
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

@app.route("/api/health/details")
def api_health_details():
    """Detailed health checks with filters: dc, status, system_name.
    Returns checks grouped by server with IS and service info."""
    f_dc = request.args.get("dc", "")
    f_status = request.args.get("status", "")
    f_system = request.args.get("system_name", "")

    filters = {}
    if f_dc:
        filters["dc"] = f_dc
    nodes = get_nodes(filters if filters else None)

    if f_system:
        nodes = [n for n in nodes if n["Meta"].get("system_name") == f_system]

    # Gather checks per node
    results = []
    for n in nodes:
        detail = get_node_detail(n["Node"])
        if not detail:
            continue
        checks = detail.get("checks", [])
        if f_status:
            checks = [c for c in checks if c.get("Status") == f_status]
        if not checks and f_status:
            continue
        results.append({
            "node": n["Node"],
            "address": n["Address"],
            "datacenter": n["Datacenter"],
            "system_name": n["Meta"].get("system_name", "-"),
            "environment": n["Meta"].get("environment", "-"),
            "team": n["Meta"].get("team", "-"),
            "services_count": len(detail.get("services", [])),
            "checks": checks,
        })

    return jsonify(results)

@app.route("/api/search")
def api_global_search():
    q = request.args.get("q", "").lower().strip()
    if len(q) < 2:
        return jsonify({"servers": [], "services": [], "systems": []})
    # Search servers
    nodes = get_nodes()
    matched_nodes = [{"Node": n["Node"], "Address": n["Address"],
                      "Datacenter": n["Datacenter"],
                      "system_name": n["Meta"].get("system_name", "-")}
                     for n in nodes if q in n["Node"].lower()
                     or q in n["Address"]
                     or q in (n["Meta"].get("system_name") or "").lower()][:20]
    # Search services
    services = get_services()
    matched_svcs = [{"name": s["name"], "instances": s["instances"]}
                    for s in services if q in s["name"].lower()][:20]
    # Search IS names
    all_sys = sorted({n["Meta"].get("system_name", "") for n in nodes
                      if n["Meta"].get("system_name") and n["Meta"]["system_name"] != "-"})
    matched_sys = [s for s in all_sys if q in s.lower()][:20]
    return jsonify({"servers": matched_nodes, "services": matched_svcs, "systems": matched_sys})

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
        env = (n["Meta"].get("environment") or "").strip().lower() or "не указана"
        servers_by_env[env] = servers_by_env.get(env, 0) + 1
        os_name = (n["Meta"].get("os") or "").strip().lower() or "не указана"
        servers_by_os[os_name] = servers_by_os.get(os_name, 0) + 1
        team = (n["Meta"].get("team") or "").strip().lower() or "не указана"
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

    # Monitoring coverage per host
    # basic = only node-exporter / windows_exporter (base OS metrics)
    # advanced = more than one monitoring exporter/agent
    # none = no monitoring services
    # "full" (полный) = set manually per-IS by admin, not per-host
    base_exporters = {"node-exporter", "node_exporter", "windows-exporter", "windows_exporter"}
    all_mon_svcs = base_exporters | {
        "consul-agent", "consul_agent",
        "prometheus", "grafana", "alertmanager", "loki",
        "victoria-metrics", "vmagent", "vmalert",
        "zabbix-agent", "zabbix_agent", "telegraf",
        "filebeat", "fluentd", "vector",
        "blackbox-exporter", "blackbox_exporter",
        "postgres-exporter", "postgres_exporter",
        "mysqld-exporter", "mysqld_exporter",
        "mongodb-exporter", "mongodb_exporter",
        "process-exporter", "process_exporter",
        "cadvisor", "node-problem-detector",
        "pushgateway", "snmp-exporter", "snmp_exporter",
    }

    monitoring_levels = {"advanced": [], "basic": [], "none": []}
    monitoring_by_dc, monitoring_by_env = {}, {}

    def _classify_host(node_svcs_set):
        mon_svcs = node_svcs_set & all_mon_svcs
        if not mon_svcs:
            return "none"
        # Only base exporters?
        if mon_svcs <= base_exporters:
            return "basic"
        return "advanced"

    if get_mode() == "test":
        for n in nodes:
            node_name = n["Node"]
            node_svcs = {svc["Service"] for svc in TEST_SERVICES if node_name in svc["Nodes"]}
            level = _classify_host(node_svcs)
            monitoring_levels[level].append(node_name)
            dc = n["Datacenter"]
            if dc not in monitoring_by_dc: monitoring_by_dc[dc] = {"advanced": 0, "basic": 0, "none": 0}
            monitoring_by_dc[dc][level] += 1
            env = n["Meta"].get("environment", "unknown")
            if env not in monitoring_by_env: monitoring_by_env[env] = {"advanced": 0, "basic": 0, "none": 0}
            monitoring_by_env[env][level] += 1
    else:
        # Live mode — build node→services map from catalog, then classify
        from consul_client import ConsulAggregator
        agg = ConsulAggregator(load_config())
        # Get all services with their health (includes node info)
        node_svc_map = {}  # node_name -> set of service names
        all_svc_catalog = agg.get_all_services()
        # For monitoring services only, fetch which nodes they run on
        mon_svc_names = set()
        for svc in all_svc_catalog:
            svc_lower = svc["name"].lower().replace("-", "_")
            # Check if any known monitoring service name is substring
            for known in all_mon_svcs:
                if known.replace("-", "_") == svc_lower or svc_lower.startswith(known.replace("-", "_")):
                    mon_svc_names.add(svc["name"])
                    break
        log.info(f"Monitoring services found in catalog: {mon_svc_names}")

        from concurrent.futures import ThreadPoolExecutor, as_completed
        def _fetch_svc_nodes(svc_name):
            instances = agg.get_service_detail(svc_name)
            return [(inst.get("node", {}).get("Node", ""), svc_name) for inst in instances]

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_fetch_svc_nodes, s): s for s in mon_svc_names}
            for f in as_completed(futures):
                try:
                    for node_name, svc_name in f.result():
                        if node_name:
                            node_svc_map.setdefault(node_name, set()).add(svc_name)
                except Exception:
                    pass

        for n in nodes:
            node_name = n["Node"]
            node_svcs = node_svc_map.get(node_name, set())
            level = _classify_host(node_svcs)
            monitoring_levels[level].append(node_name)
            dc = n["Datacenter"]
            if dc not in monitoring_by_dc: monitoring_by_dc[dc] = {"advanced": 0, "basic": 0, "none": 0}
            monitoring_by_dc[dc][level] += 1
            env = n["Meta"].get("environment", "unknown")
            if env not in monitoring_by_env: monitoring_by_env[env] = {"advanced": 0, "basic": 0, "none": 0}
            monitoring_by_env[env][level] += 1

    total_nodes = len(nodes)
    total_monitored = len(monitoring_levels["advanced"]) + len(monitoring_levels["basic"])
    monitoring_summary = {
        "total": total_nodes,
        "monitored": total_monitored,
        "advanced": len(monitoring_levels["advanced"]),
        "basic": len(monitoring_levels["basic"]),
        "none": len(monitoring_levels["none"]),
        "coverage_pct": round(total_monitored / total_nodes * 100) if total_nodes else 0,
        "servers_advanced": monitoring_levels["advanced"],
        "servers_basic": monitoring_levels["basic"],
        "servers_none": monitoring_levels["none"],
    }

    # Hosts by IS + monitoring level per IS
    # "Полный мониторинг" ИС = отмечено вручную админом
    cfg = load_config()
    full_systems = set(cfg.get("monitored_systems", []))
    adv_servers = set(monitoring_levels["advanced"])
    basic_servers = set(monitoring_levels["basic"])

    hosts_by_system = {}
    for n in nodes:
        sys_name = (n["Meta"].get("system_name") or "").strip()
        if not sys_name or sys_name == "-":
            sys_name = "Unassigned"
        if sys_name not in hosts_by_system:
            hosts_by_system[sys_name] = {"count": 0, "servers": [], "dcs": set(), "envs": set(),
                                          "advanced": 0, "basic": 0, "none": 0}
        hosts_by_system[sys_name]["count"] += 1
        hosts_by_system[sys_name]["servers"].append(n["Node"])
        hosts_by_system[sys_name]["dcs"].add(n["Datacenter"])
        hosts_by_system[sys_name]["envs"].add(n["Meta"].get("environment", "unknown"))
        if n["Node"] in adv_servers:
            hosts_by_system[sys_name]["advanced"] += 1
        elif n["Node"] in basic_servers:
            hosts_by_system[sys_name]["basic"] += 1
        else:
            hosts_by_system[sys_name]["none"] += 1

    hosts_by_system_out = {}
    for sys_name, info in sorted(hosts_by_system.items(), key=lambda x: -x[1]["count"]):
        all_covered = (info["advanced"] + info["basic"]) == info["count"] and info["count"] > 0
        is_full = sys_name in full_systems  # полный мониторинг = ручная отметка
        hosts_by_system_out[sys_name] = {
            "count": info["count"], "servers": info["servers"],
            "datacenters": sorted(info["dcs"]), "environments": sorted(info["envs"]),
            "mon_advanced": info["advanced"], "mon_basic": info["basic"], "mon_none": info["none"],
            "is_monitored": all_covered or is_full,
            "is_full": is_full,
            "all_covered": all_covered,
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

    # IS monitoring summary
    total_is = len([s for s in hosts_by_system if s != "Unassigned"])
    monitored_is_count = len([s for s in hosts_by_system_out
                              if hosts_by_system_out[s]["is_monitored"] and s != "Unassigned"])
    fully_covered_is = len([s for s in hosts_by_system_out
                            if hosts_by_system_out[s]["all_covered"] and s != "Unassigned"])

    is_monitoring_summary = {
        "total_is": total_is,
        "monitored_is": monitored_is_count,
        "fully_covered_is": fully_covered_is,
        "coverage_pct": round(monitored_is_count / total_is * 100) if total_is else 0,
    }

    # Servers without system_name
    unassigned_servers = [n["Node"] for n in nodes
                          if not (n["Meta"].get("system_name") or "").strip()
                          or n["Meta"].get("system_name") == "-"]

    # Exporters count per IS
    exporters_by_system = {}
    if get_mode() == "test":
        for sys_name, info in hosts_by_system.items():
            exp_set = set()
            for svc in TEST_SERVICES:
                for srv in info["servers"]:
                    if srv in svc["Nodes"]:
                        exp_set.add(svc["Service"])
            exporters_by_system[sys_name] = len(exp_set)
    else:
        for sys_name in hosts_by_system:
            exporters_by_system[sys_name] = 0

    return jsonify({
        "services_by_category": svc_by_category, "servers_by_dc": servers_by_dc,
        "servers_by_env": servers_by_env, "servers_by_os": servers_by_os,
        "servers_by_team": servers_by_team, "instances_per_service": instances_per_svc,
        "health_status": health, "services_per_server": svcs_per_server,
        "health_by_dc": health_by_dc, "monitoring": monitoring_summary,
        "monitoring_by_dc": monitoring_by_dc, "monitoring_by_env": monitoring_by_env,
        "hosts_by_system": hosts_by_system_out, "services_by_system": services_by_system,
        "is_monitoring": is_monitoring_summary,
        "unassigned_servers": unassigned_servers,
        "exporters_by_system": exporters_by_system,
        "total_exporters_unique": len(services_list),
        "total_exporters_instances": sum(s.get("instances", 0) for s in services_list),
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

@app.route("/api/admin/debug-service/<service_name>")
def admin_debug_service(service_name):
    """Show raw Consul API response for a service — for debugging."""
    if get_mode() == "test":
        return jsonify({"error": "Only in live mode"})
    from consul_client import ConsulAggregator
    agg = ConsulAggregator(load_config())
    raw_results = []
    for client in agg.clients:
        raw = client._get(f"/health/service/{service_name}")
        if raw:
            for entry in raw:
                raw_results.append({
                    "dc": client.dc_name,
                    "node": (entry.get("Node") or {}).get("Node", "?"),
                    "node_address": (entry.get("Node") or {}).get("Address", "?"),
                    "service_id": (entry.get("Service") or {}).get("ID", "?"),
                    "service_port": (entry.get("Service") or {}).get("Port", 0),
                    "service_tags": (entry.get("Service") or {}).get("Tags", []),
                    "checks_count": len(entry.get("Checks") or []),
                })
    return jsonify({"service": service_name, "instances": len(raw_results), "data": raw_results})

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

@app.route("/api/admin/monitored-systems", methods=["GET"])
def admin_get_monitored_systems():
    cfg = load_config()
    return jsonify(cfg.get("monitored_systems", []))

@app.route("/api/admin/monitored-systems", methods=["POST"])
def admin_set_monitored_systems():
    """Save list of IS names marked as fully monitored."""
    pwd = request.json.get("password", "")
    cfg = load_config()
    if pwd != cfg.get("app", {}).get("admin_password", "admin"):
        return jsonify({"error": "Неверный пароль"}), 403
    systems = request.json.get("systems", [])
    cfg["monitored_systems"] = sorted(set(systems))
    save_config(cfg)
    return jsonify({"ok": True, "systems": cfg["monitored_systems"]})

@app.route("/api/admin/toggle-monitored-system", methods=["POST"])
def admin_toggle_monitored_system():
    """Toggle a single IS in monitored list (no password for quick toggle)."""
    system_name = request.json.get("system_name", "")
    if not system_name:
        return jsonify({"error": "system_name required"}), 400
    cfg = load_config()
    monitored = set(cfg.get("monitored_systems", []))
    if system_name in monitored:
        monitored.discard(system_name)
    else:
        monitored.add(system_name)
    cfg["monitored_systems"] = sorted(monitored)
    save_config(cfg)
    return jsonify({"ok": True, "is_monitored": system_name in monitored,
                    "systems": cfg["monitored_systems"]})

@app.route("/api/admin/excluded-systems", methods=["GET"])
def admin_get_excluded():
    return jsonify(load_config().get("excluded_systems", []))

@app.route("/api/admin/toggle-excluded-system", methods=["POST"])
def admin_toggle_excluded():
    """Toggle a single IS in excluded list."""
    system_name = request.json.get("system_name", "")
    if not system_name:
        return jsonify({"error": "system_name required"}), 400
    cfg = load_config()
    excluded = set(cfg.get("excluded_systems", []))
    if system_name in excluded:
        excluded.discard(system_name)
    else:
        excluded.add(system_name)
    cfg["excluded_systems"] = sorted(excluded)
    save_config(cfg)
    return jsonify({"ok": True, "is_excluded": system_name in excluded})

# ──────────────────────────────────────
# Inventory export
# ──────────────────────────────────────

import re as _re

def _detect_dc_group(hostname):
    """Detect DC group from hostname digits: 9xx→92xx, 2xx→2xx, else→1xx."""
    m = _re.search(r'(\d{2,})', hostname)
    if not m:
        return "1xx"
    digits = m.group(1)
    if digits.startswith("92"):
        return "92xx"
    if digits.startswith("2"):
        return "2xx"
    return "1xx"

@app.route("/api/export/inventory")
def api_export_inventory():
    """Generate Ansible inventory.ini for servers without system_name metadata."""
    from flask import Response
    nodes = get_nodes()

    # Only unassigned servers
    unassigned = [n for n in nodes
                  if not (n["Meta"].get("system_name") or "").strip()
                  or n["Meta"].get("system_name") == "-"]

    # Classify by DC group + OS
    groups = {}
    for n in unassigned:
        hostname = n["Node"]
        os_type = (n["Meta"].get("os") or "").strip().lower()
        if os_type in ("windows", "win", "win32", "win64"):
            os_label = "windows"
        else:
            os_label = "linux"
        dc = _detect_dc_group(hostname)
        group_name = f"dc_{dc}_{os_label}"
        groups.setdefault(group_name, []).append(hostname)

    # Sort hosts within each group
    for g in groups:
        groups[g].sort()

    # Build inventory
    lines = [f"# Ansible inventory — серверы без метаданных (system_name)",
             f"# Всего: {len(unassigned)} серверов",
             ""]

    linux_children = []
    windows_children = []

    # Sorted group names
    for group_name in sorted(groups.keys()):
        lines.append(f"[{group_name}]")
        for host in groups[group_name]:
            lines.append(host)
        lines.append("")
        if "_linux" in group_name:
            linux_children.append(group_name)
        elif "_windows" in group_name:
            windows_children.append(group_name)

    # Children groups
    if linux_children:
        lines.append("[linux:children]")
        for g in sorted(linux_children):
            lines.append(g)
        lines.append("")

    if windows_children:
        lines.append("[windows:children]")
        for g in sorted(windows_children):
            lines.append(g)
        lines.append("")

    content = "\n".join(lines)
    return Response(content, mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=inventory.ini"})

# ──────────────────────────────────────
# Advanced analytics APIs
# ──────────────────────────────────────

import csv
import io
import time as _time
from datetime import datetime as _dt

# ── Change history log (in-memory + file) ──
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "change_history.json")

def _load_history():
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def _save_history(history):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history[-500:], f, ensure_ascii=False, indent=2)

def _log_change(action, details=""):
    history = _load_history()
    history.append({"time": _dt.now().isoformat(timespec="seconds"), "action": action, "details": details})
    _save_history(history)

# Hook into toggle endpoints to log changes
_orig_toggle_mon = admin_toggle_monitored_system
@app.route("/api/admin/toggle-monitored-system", methods=["POST"], endpoint="toggle_mon_logged")
def _logged_toggle_mon():
    resp = _orig_toggle_mon()
    data = resp.get_json() if hasattr(resp, 'get_json') else {}
    sn = request.json.get("system_name", "")
    _log_change("monitoring_toggle", f"{sn}: {'ON' if data.get('is_monitored') else 'OFF'}")
    return resp

_orig_toggle_excl = admin_toggle_excluded
@app.route("/api/admin/toggle-excluded-system", methods=["POST"], endpoint="toggle_excl_logged")
def _logged_toggle_excl():
    resp = _orig_toggle_excl()
    sn = request.json.get("system_name", "")
    _log_change("exclude_toggle", sn)
    return resp

@app.route("/api/history")
def api_history():
    return jsonify(_load_history()[-100:])

# ── Exporter versions ──
@app.route("/api/exporter-versions")
def api_exporter_versions():
    """Returns per-node exporter list with versions."""
    nodes = get_nodes()
    result = []
    for n in nodes[:200]:  # limit to avoid long load
        detail = get_node_detail(n["Node"])
        if not detail:
            continue
        for svc in detail.get("services", []):
            result.append({
                "node": n["Node"],
                "system_name": n["Meta"].get("system_name", "-"),
                "service": svc.get("Service", ""),
                "version": (svc.get("Meta") or {}).get("version", "-"),
                "port": svc.get("Port", 0),
            })
    return jsonify(result)

# ── Expected exporters config ──
@app.route("/api/admin/expected-exporters", methods=["GET"])
def api_get_expected():
    cfg = load_config()
    return jsonify(cfg.get("expected_exporters", {}))

@app.route("/api/admin/expected-exporters", methods=["POST"])
def api_set_expected():
    """Set expected exporter list per IS. Body: {system_name: [exporter_names]}"""
    cfg = load_config()
    cfg["expected_exporters"] = request.json.get("expected", {})
    save_config(cfg)
    _log_change("expected_exporters_update", str(list(cfg["expected_exporters"].keys())))
    return jsonify({"ok": True})

# ── Coverage gaps ──
@app.route("/api/coverage-gaps")
def api_coverage_gaps():
    """Find servers where expected exporters are missing."""
    cfg = load_config()
    expected = cfg.get("expected_exporters", {})
    if not expected:
        return jsonify([])

    nodes = get_nodes()
    gaps = []
    for n in nodes:
        sys_name = (n["Meta"].get("system_name") or "").strip()
        if not sys_name or sys_name == "-" or sys_name not in expected:
            continue
        detail = get_node_detail(n["Node"])
        actual = set()
        if detail:
            actual = {s["Service"] for s in detail.get("services", [])}
        missing = set(expected[sys_name]) - actual
        if missing:
            gaps.append({
                "node": n["Node"],
                "system_name": sys_name,
                "expected": sorted(expected[sys_name]),
                "actual": sorted(actual),
                "missing": sorted(missing),
            })
    return jsonify(gaps)

# ── Owners map ──
@app.route("/api/owners")
def api_owners():
    """IS → owner mapping from Consul metadata."""
    nodes = get_nodes()
    owners = {}
    for n in nodes:
        sys_name = (n["Meta"].get("system_name") or "").strip()
        if not sys_name or sys_name == "-":
            continue
        owner = n["Meta"].get("system_owner") or n.get("_raw_meta", {}).get("system_owner", "")
        if sys_name not in owners:
            owners[sys_name] = {"owner": owner, "servers": 0, "team": n["Meta"].get("team", "-")}
        owners[sys_name]["servers"] += 1
        if owner and not owners[sys_name]["owner"]:
            owners[sys_name]["owner"] = owner
    return jsonify(owners)

# ── CSV export ──
@app.route("/api/export/csv")
def api_export_csv():
    """Export full server inventory as CSV."""
    from flask import Response
    nodes = get_nodes()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Server", "IP", "IS", "Datacenter", "Environment", "OS", "Team", "Owner", "Exporters"])
    for n in nodes:
        detail = get_node_detail(n["Node"])
        svcs = []
        if detail:
            svcs = [s["Service"] for s in detail.get("services", [])]
        writer.writerow([
            n["Node"], n["Address"],
            n["Meta"].get("system_name", "-"),
            n["Datacenter"],
            n["Meta"].get("environment", "-"),
            n["Meta"].get("os", "-"),
            n["Meta"].get("team", "-"),
            n["Meta"].get("system_owner", ""),
            "; ".join(svcs),
        ])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=servers_report.csv"})

# ── SLA / uptime snapshot ──
@app.route("/api/sla")
def api_sla():
    """Current SLA snapshot per IS — % of passing checks + problem details."""
    nodes = get_nodes()
    is_checks = {}
    for n in nodes:
        sys_name = (n["Meta"].get("system_name") or "").strip()
        if not sys_name or sys_name == "-":
            continue
        detail = get_node_detail(n["Node"])
        if not detail:
            continue
        if sys_name not in is_checks:
            is_checks[sys_name] = {"total": 0, "passing": 0, "problems": []}
        for c in detail.get("checks", []):
            is_checks[sys_name]["total"] += 1
            if c.get("Status") == "passing":
                is_checks[sys_name]["passing"] += 1
            else:
                is_checks[sys_name]["problems"].append({
                    "node": n["Node"],
                    "check": c.get("Name", ""),
                    "status": c.get("Status", ""),
                    "service": c.get("ServiceName", ""),
                    "output": (c.get("Output") or "")[:200],
                })

    result = {}
    for sys_name, data in is_checks.items():
        pct = round(data["passing"] / data["total"] * 100, 1) if data["total"] else 100
        result[sys_name] = {
            "total": data["total"], "passing": data["passing"], "sla_pct": pct,
            "problems": data["problems"][:50],
        }
    return jsonify(result)

# ── IS comparison ──
@app.route("/api/compare")
def api_compare():
    """Compare two IS side by side."""
    is1 = request.args.get("is1", "")
    is2 = request.args.get("is2", "")
    if not is1 or not is2:
        return jsonify({"error": "is1 and is2 required"}), 400

    nodes = get_nodes()
    def _gather(sys_name):
        sns = [n for n in nodes if (n["Meta"].get("system_name") or "").strip() == sys_name]
        exporters = set()
        checks_total, checks_pass = 0, 0
        for n in sns:
            detail = get_node_detail(n["Node"])
            if detail:
                for s in detail.get("services", []):
                    exporters.add(s["Service"])
                for c in detail.get("checks", []):
                    checks_total += 1
                    if c.get("Status") == "passing":
                        checks_pass += 1
        return {
            "servers": len(sns),
            "server_list": [n["Node"] for n in sns],
            "exporters": sorted(exporters),
            "checks_total": checks_total,
            "checks_passing": checks_pass,
            "sla_pct": round(checks_pass / checks_total * 100, 1) if checks_total else 100,
            "dcs": sorted({n["Datacenter"] for n in sns}),
            "envs": sorted({n["Meta"].get("environment", "-") for n in sns}),
            "os": sorted({n["Meta"].get("os", "-") for n in sns}),
        }

    return jsonify({"is1": {"name": is1, **_gather(is1)}, "is2": {"name": is2, **_gather(is2)}})

# ── Heatmap data ──
@app.route("/api/heatmap")
def api_heatmap():
    """DC × IS matrix with server counts."""
    nodes = get_nodes()
    matrix = {}
    all_dcs = set()
    all_is = set()
    for n in nodes:
        dc = n["Datacenter"]
        sys_name = (n["Meta"].get("system_name") or "").strip() or "Unassigned"
        all_dcs.add(dc)
        all_is.add(sys_name)
        key = f"{dc}|{sys_name}"
        matrix[key] = matrix.get(key, 0) + 1
    return jsonify({
        "datacenters": sorted(all_dcs),
        "systems": sorted(all_is),
        "matrix": matrix,
    })

# ── Architecture / IS dependencies ──
ARCH_PATH = os.path.join(os.path.dirname(__file__), "architecture.json")

def _load_arch():
    if os.path.exists(ARCH_PATH):
        with open(ARCH_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"nodes": [], "links": []}

def _save_arch(data):
    with open(ARCH_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.route("/api/architecture")
def api_architecture():
    arch = _load_arch()
    # Enrich nodes with monitoring data from Consul
    try:
        nodes = get_nodes()
        # Build IS → stats map
        # Exporter keyword → human label (matched by substring)
        exporter_keywords = [
            ("windows_exporter", "ОС + Процессы"),
            ("windows-exporter", "ОС + Процессы"),
            ("node_exporter", "ОС"),
            ("node-exporter", "ОС"),
            ("postgres", "БД PostgreSQL"),
            ("mysql", "БД MySQL"),
            ("mongodb", "БД MongoDB"),
            ("mongo", "БД MongoDB"),
            ("mssql", "БД MSSQL"),
            ("redis", "БД Redis"),
            ("oracle", "БД Oracle"),
            ("process_exporter", "Процессы"),
            ("process-exporter", "Процессы"),
            ("blackbox", "Доступность"),
            ("cadvisor", "Контейнеры"),
            ("telegraf", "Телеметрия"),
            ("filebeat", "Логи"),
            ("fluentd", "Логи"),
            ("vector", "Логи"),
            ("loki", "Логи"),
            ("prometheus", "Метрики"),
            ("vmagent", "Метрики"),
            ("alertmanager", "Алерты"),
            ("vmalert", "Алерты"),
            ("grafana", "Дашборды"),
            ("snmp", "SNMP"),
            ("consul", "Discovery"),
            ("kafka", "Kafka"),
            ("rabbitmq", "RabbitMQ"),
            ("nginx", "Nginx"),
            ("apache", "Apache"),
            ("jmx", "JMX"),
            ("appsoft", "Приложение"),
        ]

        is_stats = {}
        for n in nodes:
            sn = (n["Meta"].get("system_name") or "").strip()
            if not sn or sn == "-":
                continue
            if sn not in is_stats:
                is_stats[sn] = {"servers": 0, "has_monitoring": False, "exporters": set()}
            is_stats[sn]["servers"] += 1
            # Collect exporters from node detail (cached)
            detail = get_node_detail(n["Node"])
            if detail:
                for svc in detail.get("services", []):
                    is_stats[sn]["exporters"].add(svc["Service"])

        cfg = load_config()
        mon_systems = set(cfg.get("monitored_systems", []))
        for sn in is_stats:
            if sn in mon_systems:
                is_stats[sn]["has_monitoring"] = True

        # Match arch nodes to IS (with aliases for non-obvious names)
        arch_aliases = cfg.get("arch_aliases", {})
        # Default aliases (arch node id → possible system_name values)
        default_aliases = {
            "Website": ["aton.ru", "сайт aton.ru", "web-сайт ооо атон", "web-сайт атон", "сайт атон", "www.aton.ru"],
        }
        for k, v in default_aliases.items():
            if k not in arch_aliases:
                arch_aliases[k] = v

        is_lower = {k.lower(): k for k in is_stats}
        for node in arch.get("nodes", []):
            label_l = (node.get("label") or "").lower()
            id_l = (node.get("id") or "").lower()
            matched = is_lower.get(label_l) or is_lower.get(id_l)
            # Check aliases
            if not matched:
                node_aliases = arch_aliases.get(node.get("id"), [])
                for alias in node_aliases:
                    matched = is_lower.get(alias.lower())
                    if matched:
                        break
            # Fuzzy: try substring match on all system_names
            if not matched:
                for is_name_lower, is_name_orig in is_lower.items():
                    # arch label contains system_name or vice versa
                    if (label_l in is_name_lower and len(label_l) > 3) or \
                       (is_name_lower in label_l and len(is_name_lower) > 3) or \
                       (id_l in is_name_lower and len(id_l) > 3) or \
                       (is_name_lower in id_l and len(is_name_lower) > 3):
                        matched = is_name_orig
                        break

            if matched:
                st = is_stats[matched]
                node["mon_servers"] = st["servers"]
                node["mon_status"] = "full" if st["has_monitoring"] else "partial"
                # Build human-readable monitoring summary (keyword-based dedup)
                tags = set()
                for exp in st["exporters"]:
                    exp_lower = exp.lower()
                    found = False
                    for keyword, label in exporter_keywords:
                        if keyword in exp_lower:
                            tags.add(label)
                            found = True
                            break
                    if not found and ("exporter" in exp_lower or "monitor" in exp_lower):
                        base = exp.split("@")[0].split(".")[0]
                        base = _re.sub(r'[_-]?\d+$', '', base).strip("_- ")
                        tags.add(_re.sub(r'[_-]', ' ', base).strip().title())
                node["mon_tags"] = sorted(tags)
            else:
                node["mon_servers"] = 0
                node["mon_status"] = "none"
                node["mon_tags"] = []
    except Exception:
        pass
    return jsonify(arch)

@app.route("/api/architecture", methods=["POST"])
def api_save_architecture():
    data = request.json
    _save_arch(data)
    _log_change("architecture_update", f"{len(data.get('nodes', []))} nodes, {len(data.get('links', []))} links")
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
