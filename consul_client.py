"""
Consul API client — fetches real data from Consul clusters.
Adapts real Consul metadata (system_name, system_environment, etc.)
to the internal format used by the portal.
"""

import requests
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed

# Suppress SSL warnings for internal certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_TIMEOUT = 10


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

    def _get(self, path, params=None):
        url = f"{self.base_url}/v1{path}"
        try:
            r = requests.get(url, headers=self.headers, params=params,
                             verify=self.verify, timeout=DEFAULT_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[Consul] Error fetching {url}: {e}")
            return None

    def get_nodes(self):
        """GET /v1/catalog/nodes"""
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
                    "environment": meta.get("system_environment", meta.get("environment", "-")),
                    "team": meta.get("system_team", meta.get("team", "-")),
                    "system_name": meta.get("system_name", "-"),
                    "system_owner": meta.get("system_owner", ""),
                    "system_role": meta.get("system_role", ""),
                    "system_service_type": meta.get("system_service_type", ""),
                    "consul_version": meta.get("consul-version", ""),
                },
                "TaggedAddresses": n.get("TaggedAddresses") or {},
                "_raw_meta": meta,
            })
        return nodes

    def get_node_services(self, node_name):
        """GET /v1/catalog/node/:node"""
        raw = self._get(f"/catalog/node/{node_name}")
        if not raw:
            return {"services": [], "node": None}
        services = []
        raw_services = raw.get("Services") or {}
        for svc_id, svc in raw_services.items():
            services.append({
                "ID": svc.get("ID", svc_id),
                "Service": svc.get("Service", ""),
                "Tags": svc.get("Tags") or [],
                "Port": svc.get("Port", 0),
                "Meta": svc.get("Meta") or {},
            })
        return {"services": services, "node_raw": raw.get("Node")}

    def get_health_checks(self, node_name):
        """GET /v1/health/node/:node"""
        raw = self._get(f"/health/node/{node_name}")
        if not raw:
            return []
        checks = []
        for c in raw:
            checks.append({
                "Node": c.get("Node", node_name),
                "CheckID": c.get("CheckID", ""),
                "Name": c.get("Name", ""),
                "Status": c.get("Status", "passing"),
                "Output": c.get("Output", ""),
                "ServiceID": c.get("ServiceID", ""),
                "ServiceName": c.get("ServiceName", ""),
                "Type": c.get("Type", ""),
            })
        return checks

    def get_services_catalog(self):
        """GET /v1/catalog/services — returns {service_name: [tags]}"""
        return self._get("/catalog/services") or {}

    def get_service_health(self, service_name):
        """GET /v1/health/service/:service"""
        return self._get(f"/health/service/{service_name}") or []


class ConsulAggregator:
    """Aggregates data from multiple Consul DCs across clusters."""

    def __init__(self, config):
        self.config = config
        self.clients = []
        clusters = config.get("clusters", {})
        for cluster_id, cluster in clusters.items():
            for dc in cluster.get("datacenters", []):
                self.clients.append(ConsulClient(
                    host=dc["host"],
                    token=dc.get("token", ""),
                    scheme=dc.get("scheme", "https"),
                    verify_ssl=dc.get("verify_ssl", True),
                    dc_name=dc.get("name", ""),
                ))

    def get_all_nodes(self):
        """Fetch nodes from all DCs in parallel."""
        all_nodes = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(c.get_nodes): c for c in self.clients}
            for f in as_completed(futures):
                nodes = f.result()
                if nodes:
                    all_nodes.extend(nodes)
        return all_nodes

    def get_node_detail(self, node_name):
        """Find the right DC and fetch node detail."""
        for client in self.clients:
            data = client.get_node_services(node_name)
            if data["services"]:
                checks = client.get_health_checks(node_name)
                return {"services": data["services"], "checks": checks}
        return {"services": [], "checks": []}

    def get_all_services(self):
        """Aggregate service catalog from all DCs."""
        seen = {}
        for client in self.clients:
            catalog = client.get_services_catalog()
            for svc_name, tags in catalog.items():
                if svc_name not in seen:
                    seen[svc_name] = {"name": svc_name, "tags": set(), "instances": 0, "ports": set()}
                seen[svc_name]["tags"].update(tags or [])
                # Get instance count via health endpoint
                health = client.get_service_health(svc_name)
                seen[svc_name]["instances"] += len(health)
                for h in health:
                    port = (h.get("Service") or {}).get("Port", 0)
                    if port:
                        seen[svc_name]["ports"].add(port)
        result = []
        for name, data in seen.items():
            result.append({
                "name": data["name"],
                "tags": sorted(data["tags"]),
                "instances": data["instances"],
                "ports": sorted(data["ports"]),
            })
        return sorted(result, key=lambda x: x["name"])

    def get_service_detail(self, service_name):
        """Get all instances of a service across DCs."""
        instances = []
        for client in self.clients:
            health = client.get_service_health(service_name)
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
                        "Node": node_data.get("Node", ""),
                        "CheckID": c.get("CheckID", ""),
                        "Name": c.get("Name", ""),
                        "Status": cs,
                        "Output": c.get("Output", ""),
                        "ServiceID": c.get("ServiceID", ""),
                        "ServiceName": c.get("ServiceName", ""),
                        "Type": c.get("Type", ""),
                    })
                    if cs == "critical":
                        status = "critical"
                    elif cs == "warning" and status != "critical":
                        status = "warning"

                instances.append({
                    "service": {
                        "ID": svc_data.get("ID", ""),
                        "Service": svc_data.get("Service", service_name),
                        "Tags": svc_data.get("Tags") or [],
                        "Port": svc_data.get("Port", 0),
                        "Meta": svc_data.get("Meta") or {},
                    },
                    "node": {
                        "ID": node_data.get("ID", ""),
                        "Node": node_data.get("Node", ""),
                        "Address": node_data.get("Address", ""),
                        "Datacenter": node_data.get("Datacenter", client.dc_name),
                        "Meta": {
                            "os": meta.get("system_operation_type", "-"),
                            "environment": meta.get("system_environment", "-"),
                            "team": meta.get("system_team", "-"),
                            "system_name": meta.get("system_name", "-"),
                        },
                        "TaggedAddresses": node_data.get("TaggedAddresses") or {},
                    },
                    "checks": parsed_checks,
                    "status": status,
                })
        return instances

    def get_datacenters(self):
        """Return list of known DC names."""
        dcs = set()
        for c in self.clients:
            dcs.add(c.dc_name)
        return sorted(dcs)
