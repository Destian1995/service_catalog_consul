"""
Consul API client — fetches real data from Consul clusters.
Adapts real Consul metadata to the internal format.

Includes TTL cache + background warm-up thread.
"""

import logging
import time
import threading
import requests
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_TIMEOUT = 10
CACHE_TTL = 60

log = logging.getLogger("consul_client")


# ── Thread-safe TTL cache ──

class _TTLCache:
    def __init__(self, ttl=CACHE_TTL):
        self._ttl = ttl
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            entry = self._data.get(key)
            if entry and time.monotonic() < entry[1]:
                return entry[0]
        return None

    def set(self, key, value):
        with self._lock:
            self._data[key] = (value, time.monotonic() + self._ttl)

    def clear(self):
        with self._lock:
            self._data.clear()


_cache = _TTLCache()


class ConsulClient:
    """Fetches data from one Consul datacenter."""

    def __init__(self, host, token="", scheme="https", verify_ssl=True, dc_name=""):
        self.base_url = f"{scheme}://{host}"
        self.token = token
        self.verify = verify_ssl
        self.dc_name = dc_name
        self.headers = {}
        if token:
            self.headers["X-Consul-Token"] = token
        log.info(f"[DC:{dc_name}] init {self.base_url} token={'YES' if token else 'NO'} ssl={verify_ssl}")

    def _get(self, path, params=None):
        url = f"{self.base_url}/v1{path}"
        try:
            r = requests.get(url, headers=self.headers, params=params,
                             verify=self.verify, timeout=DEFAULT_TIMEOUT)
            log.debug(f"[DC:{self.dc_name}] {path} -> {r.status_code} ({len(r.content)}b)")
            r.raise_for_status()
            return r.json()
        except requests.exceptions.SSLError as e:
            log.error(f"[DC:{self.dc_name}] SSL {url}: {e}")
        except requests.exceptions.ConnectionError as e:
            log.error(f"[DC:{self.dc_name}] CONN {url}: {e}")
        except requests.exceptions.Timeout:
            log.error(f"[DC:{self.dc_name}] TIMEOUT {url}")
        except requests.exceptions.HTTPError:
            log.error(f"[DC:{self.dc_name}] HTTP {r.status_code} {url}: {r.text[:200]}")
        except Exception as e:
            log.error(f"[DC:{self.dc_name}] ERR {url}: {type(e).__name__}: {e}")
        return None

    def get_nodes(self):
        raw = self._get("/catalog/nodes")
        if not raw:
            return []
        nodes = []
        for n in raw:
            meta = n.get("Meta") or {}
            nodes.append({
                "ID": n.get("ID", ""),
                "Node": n.get("Node", ""),
                "Address": n.get("Address", ""),
                "Datacenter": n.get("Datacenter", self.dc_name),
                "Meta": {
                    "os": meta.get("system_operation_type", meta.get("os", "-")),
                    "cpu": meta.get("cpu", "-"),
                    "ram": meta.get("ram", "-"),
                    "disk": meta.get("disk", "-"),
                    "kernel": meta.get("kernel", "-"),
                    "environment": (meta.get("system_environment") or meta.get("environment") or "-").lower(),
                    "team": (meta.get("system_team") or meta.get("team") or "-").lower(),
                    "system_name": meta.get("system_name", "-"),
                    "system_owner": meta.get("system_owner", ""),
                    "system_role": meta.get("system_role", ""),
                    "system_service_type": meta.get("system_service_type", ""),
                    "consul_version": meta.get("consul-version", ""),
                },
                "TaggedAddresses": n.get("TaggedAddresses") or {},
                "_raw_meta": meta,
            })
        log.info(f"[DC:{self.dc_name}] {len(nodes)} nodes")
        return nodes

    def get_node_services(self, node_name):
        raw = self._get(f"/catalog/node/{node_name}")
        if not raw:
            return {"services": [], "node": None}
        services = []
        for svc_id, svc in (raw.get("Services") or {}).items():
            services.append({
                "ID": svc.get("ID", svc_id),
                "Service": svc.get("Service", ""),
                "Tags": svc.get("Tags") or [],
                "Port": svc.get("Port", 0),
                "Meta": svc.get("Meta") or {},
            })
        return {"services": services, "node_raw": raw.get("Node")}

    def get_health_checks(self, node_name):
        raw = self._get(f"/health/node/{node_name}")
        if not raw:
            return []
        return [{
            "Node": c.get("Node", node_name),
            "CheckID": c.get("CheckID", ""),
            "Name": c.get("Name", ""),
            "Status": c.get("Status", "passing"),
            "Output": c.get("Output", ""),
            "ServiceID": c.get("ServiceID", ""),
            "ServiceName": c.get("ServiceName", ""),
            "Type": c.get("Type", ""),
        } for c in raw]

    def get_services_catalog(self):
        """GET /v1/catalog/services — returns {name: [tags]} — 1 request per DC."""
        return self._get("/catalog/services") or {}

    def get_service_health(self, service_name):
        return self._get(f"/health/service/{service_name}") or []

    def test_connectivity(self):
        url = f"{self.base_url}/v1/status/leader"
        try:
            r = requests.get(url, headers=self.headers, verify=self.verify, timeout=5)
            return {"ok": r.ok, "status": r.status_code, "leader": r.text.strip(), "url": self.base_url}
        except Exception as e:
            return {"ok": False, "error": str(e), "url": self.base_url}


class ConsulAggregator:
    """Aggregates data from multiple Consul DCs."""

    def __init__(self, config):
        self.config = config
        self.clients = []
        for cluster_id, cluster in config.get("clusters", {}).items():
            for dc in cluster.get("datacenters", []):
                self.clients.append(ConsulClient(
                    host=dc["host"], token=dc.get("token", ""),
                    scheme=dc.get("scheme", "https"),
                    verify_ssl=dc.get("verify_ssl", True),
                    dc_name=dc.get("name", ""),
                ))
        log.info(f"Aggregator: {len(self.clients)} DCs")

    def get_all_nodes(self):
        cached = _cache.get("all_nodes")
        if cached is not None:
            return cached
        all_nodes = []
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(c.get_nodes): c for c in self.clients}
            for f in as_completed(futures):
                try:
                    nodes = f.result()
                    if nodes:
                        all_nodes.extend(nodes)
                except Exception as e:
                    log.error(f"get_all_nodes exception: {e}")
        elapsed = time.monotonic() - t0
        log.info(f"Fetched {len(all_nodes)} nodes in {elapsed:.1f}s")
        _cache.set("all_nodes", all_nodes)
        return all_nodes

    def get_node_detail(self, node_name):
        cache_key = f"node_detail:{node_name}"
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
        for client in self.clients:
            data = client.get_node_services(node_name)
            if data["services"]:
                checks = client.get_health_checks(node_name)
                result = {"services": data["services"], "checks": checks}
                _cache.set(cache_key, result)
                return result
        return {"services": [], "checks": []}

    def get_all_services(self):
        """Fast service list — uses only /catalog/services (1 req per DC).
        Does NOT fetch health per service. Instances count = 0 (filled on detail)."""
        cached = _cache.get("all_services")
        if cached is not None:
            return cached

        seen = {}
        t0 = time.monotonic()
        # Parallel catalog fetch — one request per DC
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(c.get_services_catalog): c for c in self.clients}
            for f in as_completed(futures):
                try:
                    catalog = f.result()
                    for svc_name, tags in catalog.items():
                        if svc_name not in seen:
                            seen[svc_name] = {"name": svc_name, "tags": set(), "instances": 0, "ports": set()}
                        seen[svc_name]["tags"].update(tags or [])
                except Exception as e:
                    log.error(f"get_all_services catalog: {e}")

        result = sorted([
            {"name": d["name"], "tags": sorted(d["tags"]), "instances": d["instances"], "ports": sorted(d["ports"])}
            for d in seen.values()
        ], key=lambda x: x["name"])
        elapsed = time.monotonic() - t0
        log.info(f"Fetched {len(result)} services in {elapsed:.1f}s")
        _cache.set("all_services", result)
        return result

    def get_service_detail(self, service_name):
        cache_key = f"svc_detail:{service_name}"
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached

        instances = []
        # Parallel across DCs
        def _fetch(client):
            return client.get_service_health(service_name)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_fetch, c): c for c in self.clients}
            for f in as_completed(futures):
                client = futures[f]
                try:
                    health = f.result()
                except Exception:
                    continue
                for entry in health:
                    node_data = entry.get("Node") or {}
                    svc_data = entry.get("Service") or {}
                    checks_data = entry.get("Checks") or []
                    meta = node_data.get("Meta") or {}
                    status = "passing"
                    parsed_checks = []
                    for c in checks_data:
                        cs = c.get("Status", "passing")
                        parsed_checks.append({
                            "Node": node_data.get("Node", ""), "CheckID": c.get("CheckID", ""),
                            "Name": c.get("Name", ""), "Status": cs, "Output": c.get("Output", ""),
                            "ServiceID": c.get("ServiceID", ""), "ServiceName": c.get("ServiceName", ""),
                            "Type": c.get("Type", ""),
                        })
                        if cs == "critical": status = "critical"
                        elif cs == "warning" and status != "critical": status = "warning"
                    instances.append({
                        "service": {"ID": svc_data.get("ID", ""), "Service": svc_data.get("Service", service_name),
                                    "Tags": svc_data.get("Tags") or [], "Port": svc_data.get("Port", 0),
                                    "Meta": svc_data.get("Meta") or {}},
                        "node": {"ID": node_data.get("ID", ""), "Node": node_data.get("Node", ""),
                                 "Address": node_data.get("Address", ""),
                                 "Datacenter": node_data.get("Datacenter", client.dc_name),
                                 "Meta": {"os": meta.get("system_operation_type", "-"),
                                          "environment": (meta.get("system_environment") or "-").lower(),
                                          "team": (meta.get("system_team") or "-").lower(),
                                          "system_name": meta.get("system_name", "-")},
                                 "TaggedAddresses": node_data.get("TaggedAddresses") or {}},
                        "checks": parsed_checks, "status": status,
                    })
        _cache.set(cache_key, instances)
        return instances

    def get_datacenters(self):
        return sorted({c.dc_name for c in self.clients})

    def test_all(self):
        results = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(c.test_connectivity): c for c in self.clients}
            for f in as_completed(futures):
                client = futures[f]
                res = f.result()
                res["dc"] = client.dc_name
                results.append(res)
        return results


# ── Background cache warmer ──

_warmer_started = False
_warmer_lock = threading.Lock()


def start_cache_warmer(config_loader):
    """Start a background thread that pre-warms cache every CACHE_TTL seconds.
    config_loader is a callable returning current config dict."""
    global _warmer_started
    with _warmer_lock:
        if _warmer_started:
            return
        _warmer_started = True

    def _warm():
        time.sleep(2)  # let app start
        while True:
            try:
                cfg = config_loader()
                if cfg.get("mode") == "live":
                    log.info("[Warmer] Refreshing cache...")
                    t0 = time.monotonic()
                    agg = ConsulAggregator(cfg)
                    agg.get_all_nodes()
                    agg.get_all_services()
                    elapsed = time.monotonic() - t0
                    log.info(f"[Warmer] Cache refreshed in {elapsed:.1f}s")
            except Exception as e:
                log.error(f"[Warmer] Error: {e}")
            time.sleep(CACHE_TTL)

    t = threading.Thread(target=_warm, daemon=True, name="cache-warmer")
    t.start()
    log.info("[Warmer] Background cache warmer started")
