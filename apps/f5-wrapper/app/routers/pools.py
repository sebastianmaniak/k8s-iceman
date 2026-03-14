from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.utils.f5_client import F5Client
from app.config import settings

router = APIRouter()


class PoolMember(BaseModel):
    name: str = Field(..., description="Member in format 'address:port', e.g. '10.0.1.50:80'")
    description: Optional[str] = None


class PoolCreate(BaseModel):
    name: str = Field(..., description="Pool name, e.g. 'prod-web-pool'")
    partition: str = Field(default="Common", description="BIG-IP partition")
    monitor: str = Field(default="/Common/http", description="Health monitor path")
    lb_method: str = Field(
        default="round-robin",
        description="Load balancing method: round-robin, least-connections-member, ratio-member, etc.",
    )
    members: list[PoolMember] = Field(default=[], description="Initial pool members")


class PoolMemberState(BaseModel):
    state: str = Field(..., description="'enabled' or 'disabled' or 'forced-offline'")


class PoolSummary(BaseModel):
    name: str
    partition: str
    monitor: str
    lb_method: str
    members_count: int
    status: str


@router.get("/", summary="List all pools", response_model=list[PoolSummary])
async def list_pools(request: Request, partition: str = "Common"):
    """List all LTM pools in the specified partition."""
    client = F5Client(request)
    data = await client.get(f"/mgmt/tm/ltm/pool?$filter=partition eq {partition}")
    pools = []
    for item in data.get("items", []):
        members_ref = item.get("membersReference", {})
        members_count = len(members_ref.get("items", [])) if "membersReference" in item else 0
        pools.append(
            PoolSummary(
                name=item["name"],
                partition=item.get("partition", "Common"),
                monitor=item.get("monitor", "none"),
                lb_method=item.get("loadBalancingMode", "round-robin"),
                members_count=members_count,
                status="active",
            )
        )
    return pools


@router.get("/{pool_name}", summary="Get pool details")
async def get_pool(request: Request, pool_name: str, partition: str = "Common"):
    """Get detailed information about a specific pool including all members and their status."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/ltm/pool/~{partition}~{pool_name}?expandSubcollections=true")


@router.post("/", summary="Create a new pool", status_code=201)
async def create_pool(request: Request, pool: PoolCreate):
    """Create a new LTM pool with optional members."""
    if settings.READ_ONLY:
        raise HTTPException(status_code=403, detail="Service is in read-only mode")
    client = F5Client(request)
    payload = {
        "name": pool.name,
        "partition": pool.partition,
        "monitor": pool.monitor,
        "loadBalancingMode": pool.lb_method,
    }
    if pool.members:
        payload["members"] = [{"name": m.name, "description": m.description or ""} for m in pool.members]
    return await client.post("/mgmt/tm/ltm/pool", payload)


@router.delete("/{pool_name}", summary="Delete a pool", status_code=204)
async def delete_pool(request: Request, pool_name: str, partition: str = "Common"):
    """Delete an LTM pool. WARNING: This is destructive."""
    if settings.READ_ONLY:
        raise HTTPException(status_code=403, detail="Service is in read-only mode")
    client = F5Client(request)
    await client.delete(f"/mgmt/tm/ltm/pool/~{partition}~{pool_name}")


@router.get("/{pool_name}/members", summary="List pool members")
async def list_pool_members(request: Request, pool_name: str, partition: str = "Common"):
    """List all members of a pool with their current status."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/ltm/pool/~{partition}~{pool_name}/members")


@router.post("/{pool_name}/members", summary="Add a member to a pool", status_code=201)
async def add_pool_member(request: Request, pool_name: str, member: PoolMember, partition: str = "Common"):
    """Add a new member (node:port) to an existing pool."""
    if settings.READ_ONLY:
        raise HTTPException(status_code=403, detail="Service is in read-only mode")
    client = F5Client(request)
    return await client.post(
        f"/mgmt/tm/ltm/pool/~{partition}~{pool_name}/members",
        {"name": member.name, "description": member.description or ""},
    )


@router.delete("/{pool_name}/members/{member_name}", summary="Remove a member from a pool", status_code=204)
async def remove_pool_member(request: Request, pool_name: str, member_name: str, partition: str = "Common"):
    """Remove a member from a pool."""
    if settings.READ_ONLY:
        raise HTTPException(status_code=403, detail="Service is in read-only mode")
    client = F5Client(request)
    await client.delete(f"/mgmt/tm/ltm/pool/~{partition}~{pool_name}/members/~{partition}~{member_name}")


@router.patch(
    "/{pool_name}/members/{member_name}/state",
    summary="Enable/disable a pool member",
)
async def set_member_state(
    request: Request,
    pool_name: str,
    member_name: str,
    body: PoolMemberState,
    partition: str = "Common",
):
    """Change a pool member's state: enabled, disabled (accept active connections),
    or forced-offline (drop all connections)."""
    if settings.READ_ONLY:
        raise HTTPException(status_code=403, detail="Service is in read-only mode")
    client = F5Client(request)
    state_map = {
        "enabled": {"state": "user-up", "session": "user-enabled"},
        "disabled": {"state": "user-up", "session": "user-disabled"},
        "forced-offline": {"state": "user-down", "session": "user-disabled"},
    }
    if body.state not in state_map:
        raise HTTPException(400, f"Invalid state. Use: {list(state_map.keys())}")
    return await client.patch(
        f"/mgmt/tm/ltm/pool/~{partition}~{pool_name}/members/~{partition}~{member_name}",
        state_map[body.state],
    )
