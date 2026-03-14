from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.utils.f5_client import F5Client
from app.config import settings

router = APIRouter()


class VirtualServerCreate(BaseModel):
    name: str = Field(..., description="Virtual server name")
    partition: str = Field(default="Common")
    destination: str = Field(..., description="VIP address:port, e.g. '10.10.10.100:443'")
    pool: Optional[str] = Field(None, description="Default pool name")
    ip_protocol: str = Field(default="tcp")
    profiles: list[str] = Field(
        default=[], description="Profile names to attach, e.g. ['/Common/http', '/Common/clientssl']"
    )
    snat: str = Field(default="automap", description="SNAT type: none, automap, or SNAT pool name")
    irules: list[str] = Field(default=[], description="iRule names to attach")


@router.get("/", summary="List all virtual servers")
async def list_virtual_servers(request: Request, partition: str = "Common"):
    """List all LTM virtual servers in the specified partition."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/ltm/virtual?$filter=partition eq {partition}")


@router.get("/{vs_name}", summary="Get virtual server details")
async def get_virtual_server(request: Request, vs_name: str, partition: str = "Common"):
    """Get detailed config of a virtual server including profiles, iRules, and pool assignment."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/ltm/virtual/~{partition}~{vs_name}?expandSubcollections=true")


@router.post("/", summary="Create a virtual server", status_code=201)
async def create_virtual_server(request: Request, vs: VirtualServerCreate):
    """Create a new LTM virtual server."""
    if settings.READ_ONLY:
        raise HTTPException(status_code=403, detail="Read-only mode")
    client = F5Client(request)
    payload = {
        "name": vs.name,
        "partition": vs.partition,
        "destination": f"/{vs.partition}/{vs.destination}",
        "ipProtocol": vs.ip_protocol,
        "sourceAddressTranslation": {"type": vs.snat},
    }
    if vs.pool:
        payload["pool"] = f"/{vs.partition}/{vs.pool}"
    if vs.profiles:
        payload["profiles"] = [{"name": p} for p in vs.profiles]
    if vs.irules:
        payload["rules"] = [f"/{vs.partition}/{r}" for r in vs.irules]
    return await client.post("/mgmt/tm/ltm/virtual", payload)


@router.delete("/{vs_name}", summary="Delete a virtual server", status_code=204)
async def delete_virtual_server(request: Request, vs_name: str, partition: str = "Common"):
    """Delete an LTM virtual server. WARNING: This is destructive."""
    if settings.READ_ONLY:
        raise HTTPException(status_code=403, detail="Read-only mode")
    client = F5Client(request)
    await client.delete(f"/mgmt/tm/ltm/virtual/~{partition}~{vs_name}")
