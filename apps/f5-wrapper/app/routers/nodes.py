from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.utils.f5_client import F5Client
from app.config import settings

router = APIRouter()


class NodeCreate(BaseModel):
    name: str = Field(..., description="Node name, e.g., 'web-server-01'")
    address: str = Field(..., description="IP address, e.g., '10.0.1.50'")
    partition: str = Field(default="Common")
    description: Optional[str] = None


class NodeState(BaseModel):
    state: str = Field(..., description="'enabled' or 'disabled' or 'forced-offline'")


@router.get("/", summary="List all nodes")
async def list_nodes(request: Request, partition: str = "Common"):
    """List all LTM nodes in the specified partition."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/ltm/node?$filter=partition eq {partition}")


@router.get("/{node_name}", summary="Get node details")
async def get_node(request: Request, node_name: str, partition: str = "Common"):
    """Get detailed information and stats about a specific node."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/ltm/node/~{partition}~{node_name}")


@router.post("/", summary="Create a node", status_code=201)
async def create_node(request: Request, node: NodeCreate):
    """Create a new LTM node."""
    if settings.READ_ONLY:
        raise HTTPException(status_code=403, detail="Read-only mode")
    client = F5Client(request)
    payload = {"name": node.name, "address": node.address, "partition": node.partition}
    if node.description:
        payload["description"] = node.description
    return await client.post("/mgmt/tm/ltm/node", payload)


@router.delete("/{node_name}", summary="Delete a node", status_code=204)
async def delete_node(request: Request, node_name: str, partition: str = "Common"):
    """Delete an LTM node. WARNING: This is destructive."""
    if settings.READ_ONLY:
        raise HTTPException(status_code=403, detail="Read-only mode")
    client = F5Client(request)
    await client.delete(f"/mgmt/tm/ltm/node/~{partition}~{node_name}")


@router.patch("/{node_name}/state", summary="Enable/disable a node")
async def set_node_state(request: Request, node_name: str, body: NodeState, partition: str = "Common"):
    """Enable or disable a node across all pools."""
    if settings.READ_ONLY:
        raise HTTPException(status_code=403, detail="Read-only mode")
    client = F5Client(request)
    state_map = {
        "enabled": {"state": "user-up", "session": "user-enabled"},
        "disabled": {"state": "user-up", "session": "user-disabled"},
        "forced-offline": {"state": "user-down", "session": "user-disabled"},
    }
    if body.state not in state_map:
        raise HTTPException(400, f"Invalid state. Use: {list(state_map.keys())}")
    return await client.patch(f"/mgmt/tm/ltm/node/~{partition}~{node_name}", state_map[body.state])
