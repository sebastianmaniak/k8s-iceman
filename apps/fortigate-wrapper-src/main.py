"""FortiGate Wrapper API with MCP endpoint for kagent integration.

Exposes FortiGate firewall read operations (policies, NATs, address groups)
via a FastAPI REST + MCP server for kagent tool discovery.
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("fortigate-wrapper")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

# --- FortiGate client ---

FORTI_HOST = os.environ["FORTI_HOST"]  # e.g. https://10.0.0.1:443
FORTI_TOKEN = os.environ["FORTI_TOKEN"]
VERIFY_SSL = os.getenv("FORTI_VERIFY_SSL", "false").lower() == "true"
VDOM = os.getenv("FORTI_VDOM", "root")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=FORTI_HOST,
        headers={"Authorization": f"Bearer {FORTI_TOKEN}"},
        verify=VERIFY_SSL,
        timeout=30.0,
    )


async def _get(path: str, params: dict | None = None) -> Any:
    async with _client() as c:
        params = params or {}
        params["vdom"] = VDOM
        r = await c.get(path, params=params)
        r.raise_for_status()
        return r.json()


# --- MCP tool server ---

mcp = FastMCP("fortigate-mcp")


# ── Firewall Policies ──


@mcp.tool()
async def list_policies() -> str:
    """List all IPv4 firewall policies. Returns policy ID, name, source/destination
    interfaces, addresses, services, action, NAT status, and whether the policy is enabled."""
    data = await _get("/api/v2/cmdb/firewall/policy")
    policies = data.get("results", [])
    rows = []
    for p in policies:
        rows.append(
            {
                "policyid": p.get("policyid"),
                "name": p.get("name", ""),
                "srcintf": [i.get("name") for i in p.get("srcintf", [])],
                "dstintf": [i.get("name") for i in p.get("dstintf", [])],
                "srcaddr": [a.get("name") for a in p.get("srcaddr", [])],
                "dstaddr": [a.get("name") for a in p.get("dstaddr", [])],
                "service": [s.get("name") for s in p.get("service", [])],
                "action": p.get("action"),
                "nat": p.get("nat"),
                "status": p.get("status"),
                "logtraffic": p.get("logtraffic"),
                "comments": p.get("comments", ""),
            }
        )
    return json.dumps(rows, indent=2)


@mcp.tool()
async def get_policy(policy_id: int) -> str:
    """Get detailed information about a specific firewall policy by its ID."""
    data = await _get(f"/api/v2/cmdb/firewall/policy/{policy_id}")
    results = data.get("results", [])
    if not results:
        return json.dumps({"error": f"Policy {policy_id} not found"})
    return json.dumps(results[0], indent=2)


# ── NAT ──


@mcp.tool()
async def list_central_snat() -> str:
    """List central SNAT (source NAT) map entries. Shows source/destination
    interfaces, addresses, NAT IP pools, and protocol details."""
    data = await _get("/api/v2/cmdb/firewall/central-snat-map")
    entries = data.get("results", [])
    rows = []
    for e in entries:
        rows.append(
            {
                "policyid": e.get("policyid"),
                "srcintf": [i.get("name") for i in e.get("srcintf", [])],
                "dstintf": [i.get("name") for i in e.get("dstintf", [])],
                "orig-addr": [a.get("name") for a in e.get("orig-addr", [])],
                "dst-addr": [a.get("name") for a in e.get("dst-addr", [])],
                "nat-ippool": [p.get("name") for p in e.get("nat-ippool", [])],
                "nat": e.get("nat"),
                "status": e.get("status"),
                "comments": e.get("comments", ""),
            }
        )
    return json.dumps(rows, indent=2)


@mcp.tool()
async def list_ip_pools() -> str:
    """List all NAT IP pools. Shows pool name, type, start/end IP, and associated interface."""
    data = await _get("/api/v2/cmdb/firewall/ippool")
    pools = data.get("results", [])
    rows = []
    for p in pools:
        rows.append(
            {
                "name": p.get("name"),
                "type": p.get("type"),
                "startip": p.get("startip"),
                "endip": p.get("endip"),
                "associated-interface": p.get("associated-interface", ""),
                "comments": p.get("comments", ""),
            }
        )
    return json.dumps(rows, indent=2)


@mcp.tool()
async def list_vips() -> str:
    """List all Virtual IPs (destination NAT / port forwarding rules).
    Shows external IP, mapped IP, port mappings, and associated interface."""
    data = await _get("/api/v2/cmdb/firewall/vip")
    vips = data.get("results", [])
    rows = []
    for v in vips:
        rows.append(
            {
                "name": v.get("name"),
                "extip": v.get("extip"),
                "mappedip": [m.get("range") for m in v.get("mappedip", [])],
                "extintf": v.get("extintf"),
                "portforward": v.get("portforward"),
                "extport": v.get("extport", ""),
                "mappedport": v.get("mappedport", ""),
                "protocol": v.get("protocol"),
                "comment": v.get("comment", ""),
            }
        )
    return json.dumps(rows, indent=2)


# ── Address Objects & Groups ──


@mcp.tool()
async def list_addresses() -> str:
    """List all firewall address objects. Shows name, type, subnet/FQDN/range,
    and associated interface."""
    data = await _get("/api/v2/cmdb/firewall/address")
    addrs = data.get("results", [])
    rows = []
    for a in addrs:
        rows.append(
            {
                "name": a.get("name"),
                "type": a.get("type"),
                "subnet": a.get("subnet", ""),
                "fqdn": a.get("fqdn", ""),
                "start-ip": a.get("start-ip", ""),
                "end-ip": a.get("end-ip", ""),
                "associated-interface": a.get("associated-interface", ""),
                "comment": a.get("comment", ""),
            }
        )
    return json.dumps(rows, indent=2)


@mcp.tool()
async def list_address_groups() -> str:
    """List all firewall address groups. Shows group name, member addresses,
    and comments."""
    data = await _get("/api/v2/cmdb/firewall/addrgrp")
    groups = data.get("results", [])
    rows = []
    for g in groups:
        rows.append(
            {
                "name": g.get("name"),
                "members": [m.get("name") for m in g.get("member", [])],
                "comment": g.get("comment", ""),
            }
        )
    return json.dumps(rows, indent=2)


@mcp.tool()
async def get_address_group(group_name: str) -> str:
    """Get detailed information about a specific address group by name,
    including all member addresses."""
    data = await _get(f"/api/v2/cmdb/firewall/addrgrp/{group_name}")
    results = data.get("results", [])
    if not results:
        return json.dumps({"error": f"Address group '{group_name}' not found"})
    return json.dumps(results[0], indent=2)


# ── Services ──


@mcp.tool()
async def list_services() -> str:
    """List all firewall service objects (custom services). Shows name,
    protocol, TCP/UDP port ranges, and category."""
    data = await _get("/api/v2/cmdb/firewall.service/custom")
    services = data.get("results", [])
    rows = []
    for s in services:
        rows.append(
            {
                "name": s.get("name"),
                "protocol": s.get("protocol"),
                "tcp-portrange": s.get("tcp-portrange", ""),
                "udp-portrange": s.get("udp-portrange", ""),
                "category": s.get("category", ""),
                "comment": s.get("comment", ""),
            }
        )
    return json.dumps(rows, indent=2)


@mcp.tool()
async def list_service_groups() -> str:
    """List all firewall service groups. Shows group name and member services."""
    data = await _get("/api/v2/cmdb/firewall.service/group")
    groups = data.get("results", [])
    rows = []
    for g in groups:
        rows.append(
            {
                "name": g.get("name"),
                "members": [m.get("name") for m in g.get("member", [])],
                "comment": g.get("comment", ""),
            }
        )
    return json.dumps(rows, indent=2)


# ── Interfaces ──


@mcp.tool()
async def list_interfaces() -> str:
    """List all network interfaces. Shows interface name, IP, type, status,
    VDOM assignment, and description."""
    data = await _get("/api/v2/cmdb/system/interface")
    ifaces = data.get("results", [])
    rows = []
    for i in ifaces:
        rows.append(
            {
                "name": i.get("name"),
                "ip": i.get("ip", ""),
                "type": i.get("type"),
                "status": i.get("status"),
                "vdom": i.get("vdom"),
                "description": i.get("description", ""),
                "alias": i.get("alias", ""),
            }
        )
    return json.dumps(rows, indent=2)


# ── System ──


@mcp.tool()
async def system_status() -> str:
    """Get FortiGate system status including hostname, firmware version,
    serial number, uptime, and HA status."""
    data = await _get("/api/v2/monitor/system/status")
    return json.dumps(data.get("results", data), indent=2)


@mcp.tool()
async def system_resources() -> str:
    """Get system resource utilization including CPU usage, memory usage,
    disk usage, and session count."""
    data = await _get("/api/v2/monitor/system/resource/usage", {"interval": "1-min"})
    return json.dumps(data.get("results", data), indent=2)


@mcp.tool()
async def ha_status() -> str:
    """Get HA (High Availability) cluster status including peer information,
    sync status, and failover priority."""
    data = await _get("/api/v2/monitor/system/ha-peer")
    return json.dumps(data.get("results", data), indent=2)


# ── Routes ──


@mcp.tool()
async def list_static_routes() -> str:
    """List all static routes. Shows destination, gateway, interface,
    distance, priority, and status."""
    data = await _get("/api/v2/cmdb/router/static")
    routes = data.get("results", [])
    rows = []
    for r in routes:
        rows.append(
            {
                "seq-num": r.get("seq-num"),
                "dst": r.get("dst", ""),
                "gateway": r.get("gateway", ""),
                "device": r.get("device", ""),
                "distance": r.get("distance"),
                "priority": r.get("priority"),
                "status": r.get("status"),
                "comment": r.get("comment", ""),
            }
        )
    return json.dumps(rows, indent=2)


# --- FastAPI app ---


@asynccontextmanager
async def lifespan(a: FastAPI):
    logger.info("FortiGate wrapper starting — host=%s vdom=%s", FORTI_HOST, VDOM)
    yield


app = FastAPI(title="FortiGate Wrapper", lifespan=lifespan)

# Mount MCP as ASGI sub-app at /mcp
app.mount("/mcp", mcp.sse_app())


@app.get("/health")
async def health():
    return {"status": "ok"}
