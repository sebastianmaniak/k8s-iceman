"""FortiGate Wrapper API with MCP endpoint for kagent integration.

Exposes FortiGate firewall operations (policies, NATs, address groups,
DHCP leases, wireless clients, device inventory, and network control)
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


async def _post(path: str, payload: dict | None = None, params: dict | None = None) -> Any:
    async with _client() as c:
        params = params or {}
        params["vdom"] = VDOM
        r = await c.post(path, json=payload or {}, params=params)
        r.raise_for_status()
        return r.json()


async def _put(path: str, payload: dict, params: dict | None = None) -> Any:
    async with _client() as c:
        params = params or {}
        params["vdom"] = VDOM
        r = await c.put(path, json=payload, params=params)
        r.raise_for_status()
        return r.json()


async def _delete(path: str, params: dict | None = None) -> Any:
    async with _client() as c:
        params = params or {}
        params["vdom"] = VDOM
        r = await c.delete(path, params=params)
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


# ── DHCP Leases ──


@mcp.tool()
async def list_dhcp_leases(
    interface: str = "",
    ip: str = "",
    mac: str = "",
    hostname: str = "",
) -> str:
    """List active DHCP leases from all DHCP servers on the FortiGate.
    Optional filters: interface, ip, mac, hostname (case-insensitive substring match).
    Returns IP, MAC, hostname, interface, lease expiry, and vendor hint for each lease."""
    data = await _get("/api/v2/monitor/system/dhcp")
    leases: list[dict] = []
    for entry in data.get("results", []):
        for lease in entry.get("leases", []):
            leases.append(
                {
                    "ip": lease.get("ip", ""),
                    "mac": lease.get("mac", ""),
                    "hostname": lease.get("hostname", ""),
                    "interface": entry.get("interface", lease.get("interface", "")),
                    "expire_time": lease.get("expire_time", ""),
                    "type": lease.get("type", ""),
                    "server_mkey": entry.get("server_mkey", ""),
                    "vci": lease.get("vci", ""),
                }
            )
    # Apply filters
    if interface:
        leases = [l for l in leases if interface.lower() in l["interface"].lower()]
    if ip:
        leases = [l for l in leases if ip in l["ip"]]
    if mac:
        leases = [l for l in leases if mac.lower() in l["mac"].lower()]
    if hostname:
        leases = [l for l in leases if hostname.lower() in l["hostname"].lower()]
    return json.dumps(leases, indent=2)


# ── Wireless / FortiAP Clients ──


@mcp.tool()
async def list_wireless_clients(
    ssid: str = "",
    ap_name: str = "",
    band: str = "",
) -> str:
    """List all connected wireless/FortiAP clients.
    Optional filters: ssid, ap_name, band (e.g. '2.4GHz', '5GHz').
    Returns client MAC, IP, hostname, SSID, AP name, signal/RSSI,
    connection duration, and OS/vendor fingerprint if available."""
    data = await _get("/api/v2/monitor/wifi/client")
    clients: list[dict] = []
    for c in data.get("results", []):
        clients.append(
            {
                "mac": c.get("mac", ""),
                "ip": c.get("ip", ""),
                "hostname": c.get("hostname", ""),
                "ssid": c.get("ssid", ""),
                "ap_name": c.get("ap_name", c.get("wtp_name", "")),
                "band": c.get("band", c.get("radio_type", "")),
                "signal_strength": c.get("signal_strength", c.get("signal", "")),
                "noise": c.get("noise", ""),
                "snr": c.get("snr", ""),
                "channel": c.get("channel", ""),
                "bandwidth_tx": c.get("bandwidth_tx", ""),
                "bandwidth_rx": c.get("bandwidth_rx", ""),
                "association_time": c.get("association_time", ""),
                "idle_time": c.get("idle_time", ""),
                "os": c.get("os", ""),
                "vendor": c.get("manufacturer", c.get("vendor", "")),
                "vlan_id": c.get("vlan_id", ""),
            }
        )
    # Apply filters
    if ssid:
        clients = [c for c in clients if ssid.lower() in c["ssid"].lower()]
    if ap_name:
        clients = [c for c in clients if ap_name.lower() in c["ap_name"].lower()]
    if band:
        clients = [c for c in clients if band.lower() in c["band"].lower()]
    return json.dumps(clients, indent=2)


# ── Device Inventory / Endpoint Discovery ──


@mcp.tool()
async def list_detected_devices(
    device_type: str = "",
    os: str = "",
    vendor: str = "",
    ip: str = "",
    mac: str = "",
) -> str:
    """List all detected/discovered devices on the network (FortiGate device inventory).
    Optional filters: device_type, os, vendor, ip, mac (case-insensitive substring).
    Returns MAC, IP, hostname, device type, OS, vendor, interface, and last-seen timestamp."""
    data = await _get("/api/v2/monitor/user/device/query")
    devices: list[dict] = []
    for d in data.get("results", []):
        devices.append(
            {
                "mac": d.get("mac", ""),
                "ip": d.get("ipv4_address", d.get("ip", "")),
                "hostname": d.get("host", d.get("hostname", "")),
                "device_type": d.get("type", d.get("detected_device", "")),
                "os": d.get("os", ""),
                "vendor": d.get("hardware_vendor", d.get("vendor", "")),
                "interface": d.get("interface", ""),
                "last_seen": d.get("last_seen", ""),
                "is_online": d.get("is_online", ""),
                "user": d.get("user", ""),
            }
        )
    # Apply filters
    if device_type:
        devices = [d for d in devices if device_type.lower() in d["device_type"].lower()]
    if os:
        devices = [d for d in devices if os.lower() in d["os"].lower()]
    if vendor:
        devices = [d for d in devices if vendor.lower() in d["vendor"].lower()]
    if ip:
        devices = [d for d in devices if ip in d["ip"]]
    if mac:
        devices = [d for d in devices if mac.lower() in d["mac"].lower()]
    return json.dumps(devices, indent=2)


# ── Network Control Tools ──


@mcp.tool()
async def get_firewall_policy(policy_id: int) -> str:
    """Get a single firewall policy by ID. Use this before updating or toggling a policy."""
    data = await _get(f"/api/v2/cmdb/firewall/policy/{policy_id}")
    results = data.get("results", [])
    if not results:
        return json.dumps({"error": f"Policy {policy_id} not found"})
    return json.dumps(results[0], indent=2)


@mcp.tool()
async def enable_firewall_policy(policy_id: int) -> str:
    """Enable (activate) a firewall policy by its ID. Sets the policy status to 'enable'."""
    data = await _put(
        f"/api/v2/cmdb/firewall/policy/{policy_id}",
        {"status": "enable"},
    )
    return json.dumps({"result": "Policy enabled", "policy_id": policy_id, "response": data}, indent=2)


@mcp.tool()
async def disable_firewall_policy(policy_id: int) -> str:
    """Disable (deactivate) a firewall policy by its ID. Sets the policy status to 'disable'.
    Traffic matching this policy will no longer be processed by it."""
    data = await _put(
        f"/api/v2/cmdb/firewall/policy/{policy_id}",
        {"status": "disable"},
    )
    return json.dumps({"result": "Policy disabled", "policy_id": policy_id, "response": data}, indent=2)


@mcp.tool()
async def create_temporary_block_policy(
    name: str,
    srcaddr: str,
    dstaddr: str = "all",
    srcintf: str = "any",
    dstintf: str = "any",
    service: str = "ALL",
    comments: str = "",
) -> str:
    """Create a DENY firewall policy to temporarily block traffic.
    Parameters:
      - name: descriptive name for the block rule
      - srcaddr: source address object name (must already exist, e.g. a MAC-based address)
      - dstaddr: destination address object name (default 'all')
      - srcintf: source interface (default 'any')
      - dstintf: destination interface (default 'any')
      - service: service to block (default 'ALL')
      - comments: optional description of why this block was created
    The policy is created enabled with action=deny and logging enabled."""
    payload = {
        "name": name,
        "srcintf": [{"name": srcintf}],
        "dstintf": [{"name": dstintf}],
        "srcaddr": [{"name": srcaddr}],
        "dstaddr": [{"name": dstaddr}],
        "service": [{"name": service}],
        "action": "deny",
        "status": "enable",
        "logtraffic": "all",
        "comments": comments or f"Temporary block created by kagent: {name}",
    }
    data = await _post("/api/v2/cmdb/firewall/policy", payload)
    return json.dumps({"result": "Block policy created", "name": name, "response": data}, indent=2)


@mcp.tool()
async def disconnect_wireless_client(mac: str) -> str:
    """Disconnect (deauthenticate) a specific wireless client by MAC address.
    The client will be forced to reassociate. Format: XX:XX:XX:XX:XX:XX"""
    data = await _post(
        "/api/v2/monitor/wifi/client/deauth",
        {"mac": mac},
    )
    return json.dumps({"result": f"Client {mac} disconnected", "response": data}, indent=2)


@mcp.tool()
async def list_ssids() -> str:
    """List all configured wireless SSIDs (VAPs). Shows SSID name, status,
    security mode, VLAN, and broadcast settings."""
    data = await _get("/api/v2/cmdb/wireless-controller/vap")
    ssids = data.get("results", [])
    rows = []
    for s in ssids:
        rows.append(
            {
                "name": s.get("name", ""),
                "ssid": s.get("ssid", ""),
                "status": s.get("status", ""),
                "security": s.get("security", ""),
                "vlan_id": s.get("vlanid", ""),
                "broadcast_ssid": s.get("broadcast-ssid", ""),
                "schedule": s.get("schedule", ""),
                "max_clients": s.get("max-clients", ""),
            }
        )
    return json.dumps(rows, indent=2)


@mcp.tool()
async def disable_ssid(ssid_name: str) -> str:
    """Disable a wireless SSID (VAP) by its profile name. This affects ALL clients
    connected to this SSID — use with caution. The SSID stops broadcasting."""
    data = await _put(
        f"/api/v2/cmdb/wireless-controller/vap/{ssid_name}",
        {"status": "disable"},
    )
    return json.dumps({"result": f"SSID '{ssid_name}' disabled", "response": data}, indent=2)


@mcp.tool()
async def enable_ssid(ssid_name: str) -> str:
    """Enable a wireless SSID (VAP) by its profile name. The SSID will resume
    broadcasting and clients can reconnect."""
    data = await _put(
        f"/api/v2/cmdb/wireless-controller/vap/{ssid_name}",
        {"status": "enable"},
    )
    return json.dumps({"result": f"SSID '{ssid_name}' enabled", "response": data}, indent=2)


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
