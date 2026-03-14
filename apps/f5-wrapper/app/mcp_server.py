"""MCP server exposing F5 BIG-IP operations as tools for kagent."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

from app.config import settings
from app.utils.f5_client import F5Client

mcp = FastMCP("f5-wrapper", host="0.0.0.0")

# The token manager is set at startup by main.py
_token_manager = None


def set_token_manager(tm):
    global _token_manager
    _token_manager = tm


def _client() -> F5Client:
    if _token_manager is None:
        raise RuntimeError("Token manager not initialized")
    return F5Client(_token_manager)


def _json(data) -> str:
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Pools
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_pools(partition: str = "Common") -> str:
    """List all LTM pools in the specified partition."""
    data = await _client().get(f"/mgmt/tm/ltm/pool?$filter=partition eq {partition}")
    items = data.get("items", [])
    result = []
    for item in items:
        members_ref = item.get("membersReference", {})
        members_count = len(members_ref.get("items", [])) if "membersReference" in item else 0
        result.append({
            "name": item["name"],
            "partition": item.get("partition", "Common"),
            "monitor": item.get("monitor", "none"),
            "lb_method": item.get("loadBalancingMode", "round-robin"),
            "members_count": members_count,
        })
    return _json(result)


@mcp.tool()
async def get_pool(pool_name: str, partition: str = "Common") -> str:
    """Get detailed information about a specific pool including all members and their status."""
    data = await _client().get(
        f"/mgmt/tm/ltm/pool/~{partition}~{pool_name}?expandSubcollections=true"
    )
    return _json(data)


@mcp.tool()
async def create_pool(
    name: str,
    partition: str = "Common",
    monitor: str = "/Common/http",
    lb_method: str = "round-robin",
    members: Optional[str | list] = None,
) -> str:
    """Create a new LTM pool. members is a JSON array of objects with 'name' (address:port) and optional 'description'."""
    if settings.READ_ONLY:
        return "ERROR: Service is in read-only mode"
    payload = {
        "name": name,
        "partition": partition,
        "monitor": monitor,
        "loadBalancingMode": lb_method,
    }
    if members:
        payload["members"] = json.loads(members) if isinstance(members, str) else members
    data = await _client().post("/mgmt/tm/ltm/pool", payload)
    return _json(data)


@mcp.tool()
async def delete_pool(pool_name: str, partition: str = "Common") -> str:
    """Delete an LTM pool. WARNING: This is destructive."""
    if settings.READ_ONLY:
        return "ERROR: Service is in read-only mode"
    await _client().delete(f"/mgmt/tm/ltm/pool/~{partition}~{pool_name}")
    return f"Pool {pool_name} deleted"


@mcp.tool()
async def list_pool_members(pool_name: str, partition: str = "Common") -> str:
    """List all members of a pool with their current status."""
    data = await _client().get(f"/mgmt/tm/ltm/pool/~{partition}~{pool_name}/members")
    return _json(data)


@mcp.tool()
async def add_pool_member(
    pool_name: str, member_name: str, partition: str = "Common", description: str = ""
) -> str:
    """Add a new member (address:port format, e.g. '10.0.1.50:80') to an existing pool."""
    if settings.READ_ONLY:
        return "ERROR: Service is in read-only mode"
    data = await _client().post(
        f"/mgmt/tm/ltm/pool/~{partition}~{pool_name}/members",
        {"name": member_name, "description": description},
    )
    return _json(data)


@mcp.tool()
async def remove_pool_member(pool_name: str, member_name: str, partition: str = "Common") -> str:
    """Remove a member from a pool."""
    if settings.READ_ONLY:
        return "ERROR: Service is in read-only mode"
    await _client().delete(
        f"/mgmt/tm/ltm/pool/~{partition}~{pool_name}/members/~{partition}~{member_name}"
    )
    return f"Member {member_name} removed from pool {pool_name}"


@mcp.tool()
async def set_pool_member_state(
    pool_name: str, member_name: str, state: str, partition: str = "Common"
) -> str:
    """Change a pool member's state: 'enabled', 'disabled', or 'forced-offline'."""
    if settings.READ_ONLY:
        return "ERROR: Service is in read-only mode"
    state_map = {
        "enabled": {"state": "user-up", "session": "user-enabled"},
        "disabled": {"state": "user-up", "session": "user-disabled"},
        "forced-offline": {"state": "user-down", "session": "user-disabled"},
    }
    if state not in state_map:
        return f"ERROR: Invalid state. Use: {list(state_map.keys())}"
    data = await _client().patch(
        f"/mgmt/tm/ltm/pool/~{partition}~{pool_name}/members/~{partition}~{member_name}",
        state_map[state],
    )
    return _json(data)


# ---------------------------------------------------------------------------
# Virtual Servers
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_virtual_servers(partition: str = "Common") -> str:
    """List all LTM virtual servers in the specified partition."""
    data = await _client().get(f"/mgmt/tm/ltm/virtual?$filter=partition eq {partition}")
    return _json(data)


@mcp.tool()
async def get_virtual_server(vs_name: str, partition: str = "Common") -> str:
    """Get detailed config of a virtual server including profiles, iRules, and pool assignment."""
    data = await _client().get(
        f"/mgmt/tm/ltm/virtual/~{partition}~{vs_name}?expandSubcollections=true"
    )
    return _json(data)


@mcp.tool()
async def create_virtual_server(
    name: str,
    destination: str,
    partition: str = "Common",
    pool: Optional[str] = None,
    ip_protocol: str = "tcp",
    profiles: Optional[str] = None,
    snat: str = "automap",
    irules: Optional[str] = None,
) -> str:
    """Create a new LTM virtual server. destination is 'address:port' (e.g. '10.10.10.100:443').
    profiles and irules are JSON arrays of strings if provided."""
    if settings.READ_ONLY:
        return "ERROR: Service is in read-only mode"
    payload = {
        "name": name,
        "partition": partition,
        "destination": f"/{partition}/{destination}",
        "ipProtocol": ip_protocol,
        "sourceAddressTranslation": {"type": snat},
    }
    if pool:
        payload["pool"] = f"/{partition}/{pool}"
    if profiles:
        payload["profiles"] = [{"name": p} for p in json.loads(profiles)]
    if irules:
        payload["rules"] = [f"/{partition}/{r}" for r in json.loads(irules)]
    data = await _client().post("/mgmt/tm/ltm/virtual", payload)
    return _json(data)


@mcp.tool()
async def delete_virtual_server(vs_name: str, partition: str = "Common") -> str:
    """Delete an LTM virtual server. WARNING: This is destructive."""
    if settings.READ_ONLY:
        return "ERROR: Service is in read-only mode"
    await _client().delete(f"/mgmt/tm/ltm/virtual/~{partition}~{vs_name}")
    return f"Virtual server {vs_name} deleted"


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_nodes(partition: str = "Common") -> str:
    """List all LTM nodes in the specified partition."""
    data = await _client().get(f"/mgmt/tm/ltm/node?$filter=partition eq {partition}")
    return _json(data)


@mcp.tool()
async def get_node(node_name: str, partition: str = "Common") -> str:
    """Get detailed information about a specific node."""
    data = await _client().get(f"/mgmt/tm/ltm/node/~{partition}~{node_name}")
    return _json(data)


@mcp.tool()
async def create_node(
    name: str, address: str, partition: str = "Common", description: str = ""
) -> str:
    """Create a new LTM node."""
    if settings.READ_ONLY:
        return "ERROR: Service is in read-only mode"
    payload = {"name": name, "address": address, "partition": partition}
    if description:
        payload["description"] = description
    data = await _client().post("/mgmt/tm/ltm/node", payload)
    return _json(data)


@mcp.tool()
async def delete_node(node_name: str, partition: str = "Common") -> str:
    """Delete an LTM node. WARNING: This is destructive."""
    if settings.READ_ONLY:
        return "ERROR: Service is in read-only mode"
    await _client().delete(f"/mgmt/tm/ltm/node/~{partition}~{node_name}")
    return f"Node {node_name} deleted"


@mcp.tool()
async def set_node_state(node_name: str, state: str, partition: str = "Common") -> str:
    """Enable or disable a node across all pools. state: 'enabled', 'disabled', or 'forced-offline'."""
    if settings.READ_ONLY:
        return "ERROR: Service is in read-only mode"
    state_map = {
        "enabled": {"state": "user-up", "session": "user-enabled"},
        "disabled": {"state": "user-up", "session": "user-disabled"},
        "forced-offline": {"state": "user-down", "session": "user-disabled"},
    }
    if state not in state_map:
        return f"ERROR: Invalid state. Use: {list(state_map.keys())}"
    data = await _client().patch(f"/mgmt/tm/ltm/node/~{partition}~{node_name}", state_map[state])
    return _json(data)


# ---------------------------------------------------------------------------
# Monitors
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_monitors(partition: str = "Common") -> str:
    """List all LTM health monitors in the specified partition."""
    data = await _client().get(f"/mgmt/tm/ltm/monitor?$filter=partition eq {partition}")
    return _json(data)


@mcp.tool()
async def list_http_monitors(partition: str = "Common") -> str:
    """List all HTTP health monitors."""
    data = await _client().get(f"/mgmt/tm/ltm/monitor/http?$filter=partition eq {partition}")
    return _json(data)


@mcp.tool()
async def list_https_monitors(partition: str = "Common") -> str:
    """List all HTTPS health monitors."""
    data = await _client().get(f"/mgmt/tm/ltm/monitor/https?$filter=partition eq {partition}")
    return _json(data)


@mcp.tool()
async def list_tcp_monitors(partition: str = "Common") -> str:
    """List all TCP health monitors."""
    data = await _client().get(f"/mgmt/tm/ltm/monitor/tcp?$filter=partition eq {partition}")
    return _json(data)


# ---------------------------------------------------------------------------
# iRules
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_irules(partition: str = "Common") -> str:
    """List all iRules in the specified partition."""
    data = await _client().get(f"/mgmt/tm/ltm/rule?$filter=partition eq {partition}")
    return _json(data)


@mcp.tool()
async def get_irule(irule_name: str, partition: str = "Common") -> str:
    """Get the full iRule definition including its TCL code."""
    data = await _client().get(f"/mgmt/tm/ltm/rule/~{partition}~{irule_name}")
    return _json(data)


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_certificates(partition: str = "Common") -> str:
    """List all SSL certificates and their expiration dates."""
    data = await _client().get(f"/mgmt/tm/sys/file/ssl-cert?$filter=partition eq {partition}")
    return _json(data)


@mcp.tool()
async def get_certificate(cert_name: str, partition: str = "Common") -> str:
    """Get detailed information about a specific SSL certificate."""
    data = await _client().get(f"/mgmt/tm/sys/file/ssl-cert/~{partition}~{cert_name}")
    return _json(data)


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

@mcp.tool()
async def system_info() -> str:
    """Get BIG-IP version, hostname, and platform info."""
    data = await _client().get("/mgmt/tm/sys/version")
    return _json(data)


@mcp.tool()
async def failover_status() -> str:
    """Check HA failover status — active or standby."""
    data = await _client().get("/mgmt/tm/sys/failover")
    return _json(data)


@mcp.tool()
async def system_performance() -> str:
    """Get throughput, connections, CPU, and memory stats."""
    data = await _client().get("/mgmt/tm/sys/performance/all-stats")
    return _json(data)


@mcp.tool()
async def config_sync_status() -> str:
    """Check config sync status across HA peers."""
    data = await _client().get("/mgmt/tm/cm/sync-status")
    return _json(data)
