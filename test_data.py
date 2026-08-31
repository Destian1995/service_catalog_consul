"""
Test database with mock Consul data for the Service Catalog.
Simulates Consul API responses with realistic server/service topology.
"""

import json
import random
import time

# ─── Datacenters ───
DATACENTERS = ["dc-moscow", "dc-spb", "dc-kazan"]

# ─── Nodes (servers) ───
NODES = [
    {
        "ID": "node-001",
        "Node": "prod-web-01",
        "Address": "10.0.1.10",
        "Datacenter": "dc-moscow",
        "Meta": {
            "os": "Ubuntu 22.04",
            "cpu": "8 vCPU",
            "ram": "32 GB",
            "disk": "500 GB SSD",
            "kernel": "5.15.0-76-generic",
            "environment": "production",
            "team": "platform",
            "system_name": "ERP",
        },
        "TaggedAddresses": {"lan": "10.0.1.10", "wan": "203.0.113.10"},
    },
    {
        "ID": "node-002",
        "Node": "prod-web-02",
        "Address": "10.0.1.11",
        "Datacenter": "dc-moscow",
        "Meta": {
            "os": "Ubuntu 22.04",
            "cpu": "8 vCPU",
            "ram": "32 GB",
            "disk": "500 GB SSD",
            "kernel": "5.15.0-76-generic",
            "environment": "production",
            "team": "platform",
            "system_name": "CRM",
        },
        "TaggedAddresses": {"lan": "10.0.1.11", "wan": "203.0.113.11"},
    },
    {
        "ID": "node-003",
        "Node": "prod-api-01",
        "Address": "10.0.2.10",
        "Datacenter": "dc-moscow",
        "Meta": {
            "os": "CentOS 8",
            "cpu": "16 vCPU",
            "ram": "64 GB",
            "disk": "1 TB NVMe",
            "kernel": "4.18.0-348.el8",
            "environment": "production",
            "team": "backend",
            "system_name": "ERP",
        },
        "TaggedAddresses": {"lan": "10.0.2.10", "wan": "203.0.113.20"},
    },
    {
        "ID": "node-004",
        "Node": "prod-api-02",
        "Address": "10.0.2.11",
        "Datacenter": "dc-moscow",
        "Meta": {
            "os": "CentOS 8",
            "cpu": "16 vCPU",
            "ram": "64 GB",
            "disk": "1 TB NVMe",
            "kernel": "4.18.0-348.el8",
            "environment": "production",
            "team": "backend",
            "system_name": "Payment Gateway",
        },
        "TaggedAddresses": {"lan": "10.0.2.11", "wan": "203.0.113.21"},
    },
    {
        "ID": "node-005",
        "Node": "prod-db-01",
        "Address": "10.0.3.10",
        "Datacenter": "dc-moscow",
        "Meta": {
            "os": "Ubuntu 20.04",
            "cpu": "32 vCPU",
            "ram": "128 GB",
            "disk": "2 TB NVMe",
            "kernel": "5.4.0-150-generic",
            "environment": "production",
            "team": "dba",
            "system_name": "ERP",
        },
        "TaggedAddresses": {"lan": "10.0.3.10", "wan": "203.0.113.30"},
    },
    {
        "ID": "node-006",
        "Node": "prod-db-02",
        "Address": "10.0.3.11",
        "Datacenter": "dc-moscow",
        "Meta": {
            "os": "Ubuntu 20.04",
            "cpu": "32 vCPU",
            "ram": "128 GB",
            "disk": "2 TB NVMe",
            "kernel": "5.4.0-150-generic",
            "environment": "production",
            "team": "dba",
            "system_name": "CRM",
        },
        "TaggedAddresses": {"lan": "10.0.3.11", "wan": "203.0.113.31"},
    },
    {
        "ID": "node-007",
        "Node": "prod-cache-01",
        "Address": "10.0.4.10",
        "Datacenter": "dc-spb",
        "Meta": {
            "os": "Debian 11",
            "cpu": "4 vCPU",
            "ram": "64 GB",
            "disk": "200 GB SSD",
            "kernel": "5.10.0-23-amd64",
            "environment": "production",
            "team": "platform",
            "system_name": "E-Commerce",
        },
        "TaggedAddresses": {"lan": "10.0.4.10", "wan": "198.51.100.10"},
    },
    {
        "ID": "node-008",
        "Node": "prod-mq-01",
        "Address": "10.0.5.10",
        "Datacenter": "dc-spb",
        "Meta": {
            "os": "Ubuntu 22.04",
            "cpu": "8 vCPU",
            "ram": "32 GB",
            "disk": "500 GB SSD",
            "kernel": "5.15.0-76-generic",
            "environment": "production",
            "team": "platform",
            "system_name": "Payment Gateway",
        },
        "TaggedAddresses": {"lan": "10.0.5.10", "wan": "198.51.100.20"},
    },
    {
        "ID": "node-009",
        "Node": "prod-monitor-01",
        "Address": "10.0.6.10",
        "Datacenter": "dc-spb",
        "Meta": {
            "os": "Ubuntu 22.04",
            "cpu": "8 vCPU",
            "ram": "16 GB",
            "disk": "1 TB HDD",
            "kernel": "5.15.0-76-generic",
            "environment": "production",
            "team": "sre",
            "system_name": "Infrastructure",
        },
        "TaggedAddresses": {"lan": "10.0.6.10", "wan": "198.51.100.30"},
    },
    {
        "ID": "node-010",
        "Node": "stage-app-01",
        "Address": "10.1.1.10",
        "Datacenter": "dc-kazan",
        "Meta": {
            "os": "Ubuntu 22.04",
            "cpu": "4 vCPU",
            "ram": "16 GB",
            "disk": "250 GB SSD",
            "kernel": "5.15.0-76-generic",
            "environment": "staging",
            "team": "backend",
            "system_name": "ERP",
        },
        "TaggedAddresses": {"lan": "10.1.1.10", "wan": "192.0.2.10"},
    },
    {
        "ID": "node-011",
        "Node": "stage-db-01",
        "Address": "10.1.2.10",
        "Datacenter": "dc-kazan",
        "Meta": {
            "os": "Ubuntu 20.04",
            "cpu": "8 vCPU",
            "ram": "32 GB",
            "disk": "500 GB SSD",
            "kernel": "5.4.0-150-generic",
            "environment": "staging",
            "team": "dba",
            "system_name": "CRM",
        },
        "TaggedAddresses": {"lan": "10.1.2.10", "wan": "192.0.2.20"},
    },
    {
        "ID": "node-012",
        "Node": "dev-all-01",
        "Address": "10.2.1.10",
        "Datacenter": "dc-kazan",
        "Meta": {
            "os": "Ubuntu 22.04",
            "cpu": "4 vCPU",
            "ram": "8 GB",
            "disk": "100 GB SSD",
            "kernel": "5.15.0-76-generic",
            "environment": "development",
            "team": "backend",
            "system_name": "E-Commerce",
        },
        "TaggedAddresses": {"lan": "10.2.1.10", "wan": "192.0.2.30"},
    },
]

# ─── Services mapped to nodes ───
SERVICES = [
    # Web servers
    {
        "ID": "svc-nginx-001",
        "Service": "nginx",
        "Tags": ["web", "proxy", "v1.24"],
        "Port": 80,
        "Meta": {"version": "1.24.0", "config": "/etc/nginx/nginx.conf"},
        "Nodes": ["prod-web-01", "prod-web-02"],
    },
    {
        "ID": "svc-nginx-ssl-001",
        "Service": "nginx-ssl",
        "Tags": ["web", "proxy", "ssl", "v1.24"],
        "Port": 443,
        "Meta": {"version": "1.24.0", "ssl_cert": "/etc/ssl/certs/server.crt"},
        "Nodes": ["prod-web-01", "prod-web-02"],
    },
    # API services
    {
        "ID": "svc-api-gateway",
        "Service": "api-gateway",
        "Tags": ["api", "gateway", "v3.2"],
        "Port": 8080,
        "Meta": {"version": "3.2.1", "framework": "Spring Boot"},
        "Nodes": ["prod-api-01", "prod-api-02"],
    },
    {
        "ID": "svc-auth-service",
        "Service": "auth-service",
        "Tags": ["api", "auth", "security", "v2.1"],
        "Port": 8081,
        "Meta": {"version": "2.1.0", "framework": "FastAPI"},
        "Nodes": ["prod-api-01", "prod-api-02"],
    },
    {
        "ID": "svc-user-service",
        "Service": "user-service",
        "Tags": ["api", "users", "v1.8"],
        "Port": 8082,
        "Meta": {"version": "1.8.3", "framework": "Go Fiber"},
        "Nodes": ["prod-api-01"],
    },
    {
        "ID": "svc-payment-service",
        "Service": "payment-service",
        "Tags": ["api", "payments", "critical", "v4.0"],
        "Port": 8083,
        "Meta": {"version": "4.0.2", "framework": "Spring Boot"},
        "Nodes": ["prod-api-02"],
    },
    {
        "ID": "svc-notification-service",
        "Service": "notification-service",
        "Tags": ["api", "notifications", "v1.3"],
        "Port": 8084,
        "Meta": {"version": "1.3.0", "framework": "Node.js Express"},
        "Nodes": ["prod-api-01", "prod-api-02"],
    },
    # Databases
    {
        "ID": "svc-postgresql",
        "Service": "postgresql",
        "Tags": ["database", "sql", "primary", "v15"],
        "Port": 5432,
        "Meta": {"version": "15.3", "max_connections": "500", "role": "primary"},
        "Nodes": ["prod-db-01"],
    },
    {
        "ID": "svc-postgresql-replica",
        "Service": "postgresql-replica",
        "Tags": ["database", "sql", "replica", "v15"],
        "Port": 5432,
        "Meta": {"version": "15.3", "max_connections": "500", "role": "replica"},
        "Nodes": ["prod-db-02"],
    },
    {
        "ID": "svc-mongodb",
        "Service": "mongodb",
        "Tags": ["database", "nosql", "v6"],
        "Port": 27017,
        "Meta": {"version": "6.0.8", "replica_set": "rs0"},
        "Nodes": ["prod-db-01", "prod-db-02"],
    },
    # Cache
    {
        "ID": "svc-redis",
        "Service": "redis",
        "Tags": ["cache", "in-memory", "v7"],
        "Port": 6379,
        "Meta": {"version": "7.2.0", "maxmemory": "48gb", "policy": "allkeys-lru"},
        "Nodes": ["prod-cache-01"],
    },
    {
        "ID": "svc-memcached",
        "Service": "memcached",
        "Tags": ["cache", "in-memory", "v1.6"],
        "Port": 11211,
        "Meta": {"version": "1.6.21", "maxmemory": "16gb"},
        "Nodes": ["prod-cache-01"],
    },
    # Message Queue
    {
        "ID": "svc-rabbitmq",
        "Service": "rabbitmq",
        "Tags": ["mq", "amqp", "v3.12"],
        "Port": 5672,
        "Meta": {"version": "3.12.4", "management_port": "15672"},
        "Nodes": ["prod-mq-01"],
    },
    {
        "ID": "svc-kafka",
        "Service": "kafka",
        "Tags": ["mq", "streaming", "v3.5"],
        "Port": 9092,
        "Meta": {"version": "3.5.1", "broker_id": "1"},
        "Nodes": ["prod-mq-01"],
    },
    # Monitoring
    {
        "ID": "svc-prometheus",
        "Service": "prometheus",
        "Tags": ["monitoring", "metrics", "v2.47"],
        "Port": 9090,
        "Meta": {"version": "2.47.0", "retention": "30d"},
        "Nodes": ["prod-monitor-01"],
    },
    {
        "ID": "svc-grafana",
        "Service": "grafana",
        "Tags": ["monitoring", "dashboards", "v10"],
        "Port": 3000,
        "Meta": {"version": "10.1.1", "auth": "ldap"},
        "Nodes": ["prod-monitor-01"],
    },
    {
        "ID": "svc-alertmanager",
        "Service": "alertmanager",
        "Tags": ["monitoring", "alerts", "v0.26"],
        "Port": 9093,
        "Meta": {"version": "0.26.0"},
        "Nodes": ["prod-monitor-01"],
    },
    {
        "ID": "svc-loki",
        "Service": "loki",
        "Tags": ["monitoring", "logs", "v2.9"],
        "Port": 3100,
        "Meta": {"version": "2.9.1", "storage": "filesystem"},
        "Nodes": ["prod-monitor-01"],
    },
    # Consul agent on every node
    {
        "ID": "svc-consul-agent",
        "Service": "consul-agent",
        "Tags": ["infra", "service-discovery", "v1.16"],
        "Port": 8500,
        "Meta": {"version": "1.16.1", "mode": "client"},
        "Nodes": [n["Node"] for n in NODES],
    },
    # Node exporter on every node
    {
        "ID": "svc-node-exporter",
        "Service": "node-exporter",
        "Tags": ["monitoring", "metrics", "exporter", "v1.6"],
        "Port": 9100,
        "Meta": {"version": "1.6.1"},
        "Nodes": [n["Node"] for n in NODES],
    },
    # Staging services
    {
        "ID": "svc-stage-api",
        "Service": "api-gateway",
        "Tags": ["api", "gateway", "staging", "v3.3-rc1"],
        "Port": 8080,
        "Meta": {"version": "3.3.0-rc1", "framework": "Spring Boot"},
        "Nodes": ["stage-app-01"],
    },
    {
        "ID": "svc-stage-nginx",
        "Service": "nginx",
        "Tags": ["web", "proxy", "staging", "v1.25"],
        "Port": 80,
        "Meta": {"version": "1.25.2"},
        "Nodes": ["stage-app-01"],
    },
    {
        "ID": "svc-stage-pg",
        "Service": "postgresql",
        "Tags": ["database", "sql", "staging", "v16"],
        "Port": 5432,
        "Meta": {"version": "16.0", "role": "standalone"},
        "Nodes": ["stage-db-01"],
    },
    {
        "ID": "svc-stage-redis",
        "Service": "redis",
        "Tags": ["cache", "in-memory", "staging", "v7"],
        "Port": 6379,
        "Meta": {"version": "7.2.0", "maxmemory": "4gb"},
        "Nodes": ["stage-db-01"],
    },
    # Dev services
    {
        "ID": "svc-dev-api",
        "Service": "api-gateway",
        "Tags": ["api", "gateway", "dev", "v3.3-dev"],
        "Port": 8080,
        "Meta": {"version": "3.3.0-dev", "framework": "Spring Boot"},
        "Nodes": ["dev-all-01"],
    },
    {
        "ID": "svc-dev-pg",
        "Service": "postgresql",
        "Tags": ["database", "sql", "dev", "v16"],
        "Port": 5432,
        "Meta": {"version": "16.0", "role": "standalone"},
        "Nodes": ["dev-all-01"],
    },
    {
        "ID": "svc-dev-redis",
        "Service": "redis",
        "Tags": ["cache", "in-memory", "dev", "v7"],
        "Port": 6379,
        "Meta": {"version": "7.2.0", "maxmemory": "1gb"},
        "Nodes": ["dev-all-01"],
    },
]


def _generate_health_status():
    """Generate realistic health check statuses with occasional warnings/criticals."""
    r = random.random()
    if r < 0.75:
        return "passing"
    elif r < 0.90:
        return "warning"
    else:
        return "critical"


def _generate_check_output(service_name, status):
    if status == "passing":
        return f"{service_name} is healthy — response time 12ms"
    elif status == "warning":
        return f"{service_name} — high latency detected (>500ms)"
    else:
        return f"{service_name} — connection refused or timeout"


def build_health_checks():
    """Build health checks for every service instance."""
    checks = []
    for svc in SERVICES:
        for node_name in svc["Nodes"]:
            status = _generate_health_status()
            checks.append({
                "Node": node_name,
                "CheckID": f"service:{svc['Service']}",
                "Name": f"Service '{svc['Service']}' check",
                "Status": status,
                "Output": _generate_check_output(svc["Service"], status),
                "ServiceID": svc["ID"],
                "ServiceName": svc["Service"],
                "Type": "http",
            })
        # Serfhealth check per node
    for node in NODES:
        checks.append({
            "Node": node["Node"],
            "CheckID": "serfHealth",
            "Name": "Serf Health Status",
            "Status": "passing",
            "Output": "Agent alive and reachable",
            "ServiceID": "",
            "ServiceName": "",
            "Type": "serf",
        })
    return checks


def get_catalog():
    """Return the full test catalog as a dict."""
    return {
        "datacenters": DATACENTERS,
        "nodes": NODES,
        "services": SERVICES,
        "checks": build_health_checks(),
    }
